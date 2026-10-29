from __future__ import annotations
import json
import urllib.error
import urllib.request
from copy import deepcopy
from hashlib import sha256
from time import perf_counter
from macr.providers.base import (ConversationRequest, ConversationResponse, ModelRequest, ModelResponse,
    ProviderCapabilities, ProviderError, _conversation_usage, _validate_conversation_message,
    validate_conversation_request, validate_request, validate_response, validate_billing_evidence,
    _valid_identity_text, validate_identity_evidence)
from macr.providers.failure_evidence import (http_failure_metadata,
    make_failure_diagnostic, validate_failure_diagnostic)
from macr.providers.response_normalization import (ResponseNormalizationError,
    decode_provider_json, normalize_assistant_message)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError('model_redirect_blocked')

class _ApprovedTransport:
    def __init__(self, api_key):
        self._api_key = api_key
        self._response_metadata = None
    @property
    def response_metadata(self):
        return (validate_failure_diagnostic(self._response_metadata)
                if self._response_metadata is not None else None)
    def _metadata(self, response, *, response_bytes=None):
        status = getattr(response, 'status', None)
        if status is None:
            status = getattr(response, 'code', None)
        identifier = None
        try:
            headers = response.headers
            identifier = headers.get('x-request-id') or headers.get('X-Request-ID')
        except (AttributeError, TypeError, ValueError):
            pass
        return http_failure_metadata('http_status', status=status,
            request_id=identifier, response_bytes=response_bytes, secret=self._api_key)
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self._response_metadata = None
        request = urllib.request.Request(endpoint, data=body, headers={'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                self._response_metadata = self._metadata(response)
                raw = response.read(max_response_bytes + 1)
                self._response_metadata = self._metadata(response, response_bytes=len(raw))
        except urllib.error.HTTPError as exc:
            self._response_metadata = self._metadata(exc)
            exc.close()
            raise ProviderError('model_transport', diagnostic=self._response_metadata) from None
        except TimeoutError:
            raise ProviderError('model_timeout', diagnostic=make_failure_diagnostic(
                'transport', metadata=self._response_metadata)) from None
        except urllib.error.URLError as exc:
            raise ProviderError('model_timeout' if isinstance(exc.reason, TimeoutError) else 'model_transport',
                diagnostic=make_failure_diagnostic('transport', metadata=self._response_metadata)) from None
        if len(raw) > max_response_bytes:
            raise ProviderError('model_response_limit', diagnostic=make_failure_diagnostic(
                'transport', metadata=self._response_metadata))
        try:
            return decode_provider_json(raw)
        except ResponseNormalizationError as error:
            raise ProviderError('model_response_invalid', diagnostic=make_failure_diagnostic(
                'json_decode', metadata=self._response_metadata,
                validation_error=error.code)) from None
        except (ValueError, UnicodeError):
            raise ProviderError('model_response_invalid', diagnostic=make_failure_diagnostic(
                'json_decode', metadata=self._response_metadata)) from None
        except RecursionError:
            raise ProviderError('model_transport', diagnostic=make_failure_diagnostic(
                'json_decode', metadata=self._response_metadata)) from None

def _transport_metadata(transport):
    try:
        return getattr(transport, 'response_metadata', None)
    except Exception:
        return None


def _billing_evidence(data, *, model, body, max_output_tokens, latency_ms):
    """Validate billing independently, only after a clean JSON envelope arrived."""
    if (type(data) is not dict or 'error' in data or data.get('model') != model
            or type(data.get('choices')) is not list or len(data['choices']) != 1
            or type(data['choices'][0]) is not dict):
        return None
    try:
        return validate_billing_evidence({'schema': 'macr.provider.billing.v1',
            'provider': 'deepseek', 'model': model, 'request_sha256': sha256(body).hexdigest(),
            'max_output_tokens': max_output_tokens, 'raw_usage': _conversation_usage(data.get('usage')),
            'latency_ms': latency_ms})
    except (ValueError, TypeError, KeyError, OverflowError):
        return None

def _identity_evidence(data, *, body):
    """Retain only independently validated identity from a clean JSON envelope."""
    if (type(data) is not dict or 'error' in data
            or type(data.get('choices')) is not list or len(data['choices']) != 1
            or type(data['choices'][0]) is not dict):
        return None
    try:
        requested_model = json.loads(body)['model']
        model_state = ('missing' if 'model' not in data else
                       'validated' if _valid_identity_text(data['model']) else 'invalid')
        if model_state == 'validated' and data['model'] != requested_model:
            model_state = 'mismatch'
        fingerprint_state = ('missing' if 'system_fingerprint' not in data else
                             'validated' if _valid_identity_text(data['system_fingerprint']) else 'invalid')
        return validate_identity_evidence({'schema': 'macr.provider.identity.v1',
            'provider': 'deepseek', 'request_sha256': sha256(body).hexdigest(),
            'requested_model': requested_model, 'model_state': model_state,
            'model': data['model'] if model_state == 'validated' else None,
            'fingerprint_state': fingerprint_state,
            'system_fingerprint': data['system_fingerprint'] if fingerprint_state == 'validated' else None})
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        return None

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

    def encode_conversation_request(self, request: ConversationRequest) -> bytes:
        """Return the exact body sent by converse, so owners can admit its bytes first."""
        validate_conversation_request(request)
        payload = {'model': self.model, 'messages': request.messages,
                   'stream': False, 'max_tokens': request.max_output_tokens}
        if request.thinking is not None:
            payload['thinking'] = request.thinking
        if request.reasoning_effort is not None:
            payload['reasoning_effort'] = request.reasoning_effort
        if request.tools:
            payload.update(tools=request.tools, tool_choice='auto')
        if request.response_schema is not None:
            payload['response_format'] = {'type': 'json_object'}
        try:
            body = json.dumps(payload, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise ProviderError('model_request_invalid') from None
        if len(body) > 1024 * 1024:
            raise ProviderError('model_request_limit')
        return body

    def converse(self, request: ConversationRequest) -> ConversationResponse:
        """Complete one conversation step; tool execution and argument validation are caller-owned."""
        body = self.encode_conversation_request(request)
        if self.transport is None:
            if not self.allow_remote:
                raise ProviderError('remote_disabled')
            if not self._api_key or not self.base_url.startswith('https://'):
                raise ProviderError('remote_config_missing')
            transport = _ApprovedTransport(self._api_key)
        else:
            transport = self.transport
        started = perf_counter()
        try:
            data = transport.post(self.base_url + '/chat/completions', body,
                min(request.timeout_seconds, self.timeout_seconds), 1024 * 1024)
        except TimeoutError:
            raise ProviderError('model_timeout', diagnostic=make_failure_diagnostic(
                'transport', metadata=_transport_metadata(transport))) from None
        except ProviderError as error:
            # A transport exception did not produce a clean JSON envelope;
            # Transport-supplied evidence cannot become billing or identity proof.
            raise ProviderError(error.code, response=(None if error.failure_kind == 'transport'
                else error.response),
                failure_kind=error.failure_kind, diagnostic=error.diagnostic or make_failure_diagnostic(
                    'transport', metadata=_transport_metadata(transport))) from None
        except Exception:
            raise ProviderError('model_transport', diagnostic=make_failure_diagnostic(
                'transport', metadata=_transport_metadata(transport))) from None
        stage = 'response_envelope'
        billing = _billing_evidence(data, model=self.model, body=body,
            max_output_tokens=request.max_output_tokens,
            latency_ms=int((perf_counter() - started) * 1000))
        identity = _identity_evidence(data, body=body)
        try:
            if type(data) is not dict:
                raise ValueError()
            if 'error' in data:
                raise ProviderError('model_http_error', diagnostic=make_failure_diagnostic(
                    stage, data=data, metadata=_transport_metadata(transport)))
            if type(data.get('choices')) is not list or len(data['choices']) != 1:
                raise ValueError()
            choice = data['choices'][0]
            stage = 'assistant_message'
            tool_names = {tool['function']['name'] for tool in request.tools
                          if type(tool.get('function')) is dict
                          and type(tool['function'].get('name')) is str}
            message = normalize_assistant_message(choice.get('message'), tool_names=tool_names)
            stage = 'response_contract'
            json.dumps({'message': message, 'model': data.get('model'),
                        'system_fingerprint': data.get('system_fingerprint')},
                       ensure_ascii=False, allow_nan=False).encode('utf-8')
            content, calls = message['content'], message.get('tool_calls', [])
            stage = 'finish_reason'
            finish = choice['finish_reason']
            if type(finish) is not str or calls and not request.tools:
                raise ValueError()
            stage = 'usage'
            raw_usage = _conversation_usage(data.get('usage'))
            known = {'prompt_tokens', 'completion_tokens'} <= raw_usage.keys()
            usage = {'input_tokens': raw_usage['prompt_tokens'],
                     'output_tokens': raw_usage['completion_tokens']} if known else {}
            stage = 'response_contract'
            response = ConversationResponse(content=content if content is not None else '',
                tool_calls=deepcopy(calls), usage=usage, latency_ms=int((perf_counter() - started) * 1000),
                model=data.get('model'), provider=self.name, finish_reason=finish,
                usage_kind='measured' if known else 'unknown', assistant_message=deepcopy(message),
                raw_usage=raw_usage, system_fingerprint=data.get('system_fingerprint'))
            try:
                validate_response(response)
            except ProviderError:
                raise ValueError() from None
            if finish in {'length', 'content_filter', 'insufficient_system_resource', 'aborted'}:
                raise ProviderError('model_output_incomplete', response=response, billing_evidence=billing,
                    identity_evidence=identity,
                    diagnostic=make_failure_diagnostic(stage, data=data,
                        metadata=_transport_metadata(transport)))
            if bool(calls) != (finish == 'tool_calls') or not response.content.strip() and not calls:
                raise ProviderError('model_response_invalid', response=response, billing_evidence=billing,
                    identity_evidence=identity,
                    diagnostic=make_failure_diagnostic('finish_consistency', data=data,
                        metadata=_transport_metadata(transport)))
            return response
        except ProviderError:
            raise
        except ResponseNormalizationError as error:
            raise ProviderError('model_response_invalid', billing_evidence=billing, identity_evidence=identity,
                diagnostic=make_failure_diagnostic(stage, data=data,
                    metadata=_transport_metadata(transport), validation_error=error.code)) from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError('model_response_invalid', billing_evidence=billing, identity_evidence=identity,
                diagnostic=make_failure_diagnostic(
                stage, data=data, metadata=_transport_metadata(transport))) from None
