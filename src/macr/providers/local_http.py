"""Bounded, nonstreaming local adapter. Never installs, starts, retries or attests a model."""
from __future__ import annotations

import copy
import json
import math
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from time import perf_counter

from macr.providers.base import ModelRequest, ModelResponse, ProviderError, validate_request, validate_response
from macr.providers.profiles import ModelProfile, canonical_endpoint, validate_profile

RESPONSE_LIMIT = 1024 * 1024
REQUEST_LIMIT = 1024 * 1024


def _object(raw):
    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    def invalid(value):
        raise ValueError()
    try:
        value = json.loads(raw.decode('utf-8') if type(raw) is bytes else raw,
                           object_pairs_hook=unique, parse_constant=invalid)
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProviderError('model_response_invalid') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError('model_redirect_blocked')


class LoopbackTransport:
    """urllib socket I/O timeout; this is not a remote generation cancellation API."""
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        endpoint = canonical_endpoint(endpoint)
        if (type(body) is not bytes or len(body) > REQUEST_LIMIT
                or type(max_response_bytes) is not int or not 0 < max_response_bytes <= RESPONSE_LIMIT
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ProviderError('model_request_invalid')
        request = urllib.request.Request(endpoint, data=body, method='POST',
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        started = perf_counter()
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                length = response.headers.get('Content-Length')
                if length is not None:
                    try:
                        if int(length) < 0:
                            raise ValueError()
                        if int(length) > max_response_bytes:
                            raise ProviderError('model_response_limit')
                    except ValueError:
                        raise ProviderError('model_response_invalid') from None
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(max_response_bytes + 1)
                if len(raw) > max_response_bytes:
                    raise ProviderError('model_response_limit')
                data = _object(raw)
                detail = data.get('error')
                code = detail.get('code') if type(detail) is dict else None
            except TimeoutError:
                raise ProviderError('model_timeout') from None
            except ProviderError as failure:
                if failure.code != 'model_response_invalid':
                    raise
                code = None
            except (OSError, ValueError, TypeError):
                code = None
            finally:
                error.close()
            if error.code in (301, 302, 303, 307, 308):
                raise ProviderError('model_redirect_blocked') from None
            if error.code == 413 or code in ('context_length_exceeded', 'context_overflow'):
                raise ProviderError('model_context_overflow') from None
            raise ProviderError('model_busy' if error.code == 429 else 'model_http_error') from None
        except (TimeoutError, urllib.error.URLError) as error:
            timeout = isinstance(error, TimeoutError) or isinstance(getattr(error, 'reason', None), TimeoutError)
            raise ProviderError('model_timeout' if timeout else 'model_transport') from None
        except ProviderError:
            raise
        except (OSError, ValueError):
            raise ProviderError('model_transport') from None
        if perf_counter() - started > timeout_seconds:
            raise ProviderError('model_timeout')
        if len(raw) > max_response_bytes:
            raise ProviderError('model_response_limit')
        return _object(raw)


class LocalHTTPProvider:
    name = 'local_http'

    def __init__(self, profile: ModelProfile, *, transport=None, token_counter=None):
        self.profile = copy.deepcopy(validate_profile(profile))
        self.capabilities = replace(self.profile.capabilities, cancel=False, health=False)
        self.transport = transport if transport is not None else LoopbackTransport()
        # Trusted caller supplies a bound including rendered template/tools/schema.
        # No tokenizer import, remote code, network discovery or character heuristic.
        self.token_counter = token_counter
        self._inflight = threading.Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        validate_request(request)
        profile = self.profile
        if request.max_output_tokens > profile.output_limit:
            raise ProviderError('model_output_limit')
        if request.max_input_tokens + request.max_output_tokens > profile.context_limit:
            raise ProviderError('model_context_overflow')
        try:
            bound = (self.token_counter(request, profile) if self.token_counter is not None
                     else request.metadata.get('input_token_upper_bound'))
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('model_token_accounting_unavailable') from None
        if type(bound) is not int or bound <= 0:
            raise ProviderError('model_token_accounting_unavailable')
        if bound > request.max_input_tokens or bound + request.max_output_tokens > profile.context_limit:
            raise ProviderError('model_context_overflow')
        payload = {'model': profile.model_id, 'messages': request.messages, 'stream': False,
                   'max_tokens': request.max_output_tokens, **profile.sampling,
                   'metadata': {'profile_id': profile.profile_id, 'model_revision': profile.model_revision,
                       'tokenizer_revision': profile.tokenizer_revision,
                       'chat_template_version': profile.chat_template_version}}
        if request.tools:
            payload.update(tools=request.tools, tool_choice='auto')
        if request.response_schema is not None and profile.capabilities.json_mode:
            # JSON mode does not imply schema-constrained decoding. Typed validation
            # and the single budgeted format-repair request belong to the caller.
            payload['response_format'] = {'type': 'json_object'}
        try:
            body = json.dumps(payload, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ProviderError('model_request_invalid') from None
        if len(body) > REQUEST_LIMIT:
            raise ProviderError('model_request_limit')
        if not self._inflight.acquire(blocking=False):
            raise ProviderError('model_busy')
        started = perf_counter()
        try:
            try:
                data = self.transport.post(canonical_endpoint(profile.endpoint), body,
                    min(request.timeout_seconds, profile.timeout_seconds), RESPONSE_LIMIT)
            except TimeoutError:
                raise ProviderError('model_timeout') from None
            except ProviderError:
                raise
            except Exception:
                raise ProviderError('model_transport') from None
            return self._response(data, request, bound, started)
        finally:
            self._inflight.release()

    def _response(self, data, request, bound, started):
        try:
            if type(data) is not dict or type(data.get('choices')) is not list or len(data['choices']) != 1:
                raise ValueError()
            choice = data['choices'][0]
            message = choice['message']
            content = message.get('content')
            calls = message.get('tool_calls', [])
            if type(calls) is not list or not all(type(c) is dict for c in calls) or (calls and not request.tools):
                raise ValueError()
            if content is None and calls:
                content = ''
            if type(content) is not str or (not content.strip() and not calls):
                raise ValueError()
            model = data.get('model', self.profile.model_id)
            finish = choice.get('finish_reason', 'unknown')
            if model != self.profile.model_id or type(finish) is not str or not finish:
                raise ValueError()
            usage = {}
            raw_usage = data.get('usage')
            if raw_usage is not None:
                if type(raw_usage) is not dict:
                    raise ValueError()
                for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                    if name in raw_usage and (type(raw_usage[name]) is not int or raw_usage[name] < 0):
                        raise ValueError()
                if {'prompt_tokens', 'completion_tokens'} <= set(raw_usage):
                    input_tokens, output_tokens = raw_usage['prompt_tokens'], raw_usage['completion_tokens']
                    if input_tokens > bound or output_tokens > request.max_output_tokens:
                        raise ValueError()
                    if 'total_tokens' in raw_usage and raw_usage['total_tokens'] != input_tokens + output_tokens:
                        raise ValueError()
                    usage = {'input_tokens': input_tokens, 'output_tokens': output_tokens}
            if request.response_schema is not None:
                if finish != 'stop':
                    raise ProviderError('model_output_incomplete')
                try:
                    value = _object(content)
                except ProviderError:
                    raise ProviderError('model_output_invalid') from None
                if request.response_schema.get('type', 'object') != 'object' or type(value) is not dict:
                    raise ProviderError('model_output_invalid')
            metrics = {'profile_id': self.profile.profile_id,
                       'model_revision': self.profile.model_revision,
                       'tokenizer_revision': self.profile.tokenizer_revision,
                       'chat_template_version': self.profile.chat_template_version,
                       'runtime': self.profile.runtime, 'runtime_version': self.profile.runtime_version,
                       'quantization': self.profile.quantization,
                       'latency_ms': int((perf_counter() - started) * 1000),
                       'input_token_upper_bound': bound,
                       'token_bound_source': 'injected_counter' if self.token_counter is not None else 'caller_supplied',
                       'prefill_tokens_per_second': None, 'decode_tokens_per_second': None,
                       'queue_ms': None, 'cold_start_ms': None, 'peak_memory_bytes': None,
                       'generation_cancel_supported': False, 'schema_validation': 'caller_owned'}
            return validate_response(ModelResponse(content=content, tool_calls=calls, usage=usage,
                latency_ms=metrics['latency_ms'], model=model, provider=self.name, finish_reason=finish,
                usage_kind='measured' if usage else 'unknown', metrics=metrics))
        except ProviderError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
            raise ProviderError('model_response_invalid') from None


def probe_service(profile: ModelProfile, *, transport=None, input_token_upper_bound=None):
    """Configuration or explicitly injected transport only; never declares readiness.

    Task 13 owns real runtime/model/tokenizer/template and service smoke attestation.
    This function does not start a service or silently call the default transport.
    """
    validate_profile(profile)
    result = {'profile_id': profile.profile_id, 'ready': False, 'attested': False,
              'source': 'configuration_only' if transport is None else 'injected_transport',
              'declared_capabilities': asdict(profile.capabilities),
              'observed_capabilities': {'json_mode': 'unavailable', 'usage': 'unavailable',
                                        'cancel': 'unsupported', 'health': 'unavailable'},
              'metrics': {'latency_ms': None, 'prefill_tokens_per_second': None,
                          'decode_tokens_per_second': None, 'queue_ms': None,
                          'cold_start_ms': None, 'peak_memory_bytes': None},
              'blockers': ['model_service_not_attested']}
    if transport is not None:
        output_limit = min(profile.output_limit, 32)
        request = ModelRequest([{'role': 'user', 'content': 'Reply with a JSON object containing ok=true.'}],
            response_schema={'type': 'object'}, max_input_tokens=profile.context_limit - output_limit,
            max_output_tokens=output_limit, timeout_seconds=profile.timeout_seconds,
            metadata={'input_token_upper_bound': input_token_upper_bound})
        try:
            response = LocalHTTPProvider(profile, transport=transport).complete(request)
            result['metrics'].update(response.metrics)
            result['observed_capabilities']['usage'] = response.usage_kind
            result['sample_response_valid'] = True
        except ProviderError as error:
            result['sample_response_valid'] = False
            result['blockers'].append(error.code)
    return result
