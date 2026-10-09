from __future__ import annotations
import json
import urllib.error
import urllib.request
from time import perf_counter
from macr.providers.base import ModelRequest, ModelResponse, ProviderCapabilities, ProviderError, validate_request, validate_response

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError('model_redirect_blocked')

class _ApprovedTransport:
    def __init__(self, api_key):
        self._api_key = api_key
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        request = urllib.request.Request(endpoint, data=body, headers={'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(max_response_bytes + 1)
        except TimeoutError:
            raise ProviderError('model_timeout') from None
        except urllib.error.URLError as exc:
            raise ProviderError('model_timeout' if isinstance(exc.reason, TimeoutError) else 'model_transport') from None
        if len(raw) > max_response_bytes:
            raise ProviderError('model_response_limit')
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProviderError('model_response_invalid') from None

class DeepSeekProvider:
    """Compatibility adapter. Real remote transport requires explicit approval/config."""
    name = 'deepseek'
    capabilities = ProviderCapabilities(json_mode=True, usage=True)
    def __init__(self, api_key=None, base_url='https://api.deepseek.com', model='deepseek-chat', timeout_seconds=60, *, transport=None, allow_remote=False):
        self._api_key = api_key
        self.base_url, self.model = base_url.rstrip('/'), model
        self.timeout_seconds = timeout_seconds
        self.transport, self.allow_remote = transport, allow_remote
    def complete(self, request: ModelRequest) -> ModelResponse:
        validate_request(request)
        if self.transport is None:
            if not self.allow_remote:
                raise ProviderError('remote_disabled')
            if not self._api_key or not self.base_url.startswith('https://'):
                raise ProviderError('remote_config_missing')
            transport = _ApprovedTransport(self._api_key)
        else:
            transport = self.transport
        payload = {'model': self.model, 'messages': request.messages, 'temperature': 0.2, 'stream': False, 'max_tokens': request.max_output_tokens}
        if request.tools:
            payload.update(tools=request.tools, tool_choice='auto')
        if request.response_schema is not None:
            payload['response_format'] = {'type': 'json_object'}
        started = perf_counter()
        try:
            data = transport.post(self.base_url + '/chat/completions', json.dumps(payload, allow_nan=False).encode(), min(request.timeout_seconds, self.timeout_seconds), 1024 * 1024)
        except TimeoutError:
            raise ProviderError('model_timeout') from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('model_transport') from None
        try:
            choice = data['choices'][0]
            message = choice['message']
            content = message.get('content') or ''
            calls = message.get('tool_calls') or []
            if not isinstance(content, str) or not isinstance(calls, list):
                raise ValueError()
            raw_usage = data.get('usage') or {}
            usage = {k: v for k, v in raw_usage.items() if type(v) is int and v >= 0}
            known = type(raw_usage.get('prompt_tokens')) is int and type(raw_usage.get('completion_tokens')) is int and raw_usage['prompt_tokens'] >= 0 and raw_usage['completion_tokens'] >= 0
            if known:
                usage.update(input_tokens=raw_usage['prompt_tokens'], output_tokens=raw_usage['completion_tokens'])
            else:
                usage = {}
            response = ModelResponse(content, calls, usage, int((perf_counter()-started)*1000), data.get('model') or self.model, self.name, choice.get('finish_reason') or 'unknown', 'measured' if known else 'unknown')
            return validate_response(response)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError('model_response_invalid') from None
