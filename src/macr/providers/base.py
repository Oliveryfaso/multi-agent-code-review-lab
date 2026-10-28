from __future__ import annotations
import inspect
import math
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from macr.providers.failure_evidence import validate_failure_diagnostic

CONVERSATION_CONTRACT_VERSION = 'conversation.v1'
_CONVERSATION_FINISH_REASONS = {'stop', 'tool_calls', 'length', 'content_filter',
                                'insufficient_system_resource', 'aborted'}

class ProviderError(Exception):
    def __init__(self, code: str, *, response: ModelResponse | None = None, failure_kind: str | None = None,
                 diagnostic: dict | None = None):
        self.code = code
        # A validated failure envelope is evidence for recording, never a successful output.
        self.response = validate_response(response) if response is not None else None
        self._diagnostic = validate_failure_diagnostic(diagnostic) if diagnostic is not None else None
        inferred_kind = {
            'model_timeout': 'transport', 'model_transport': 'transport', 'model_busy': 'transport',
            'model_http_error': 'transport', 'model_redirect_blocked': 'transport',
            'model_output_incomplete': 'incomplete', 'model_output_invalid': 'invalid_json',
            'model_response_invalid': 'invalid_contract', 'provider_contract': 'invalid_contract',
            'model_request_invalid': 'invalid_contract', 'model_output_limit': 'invalid_contract',
            'model_context_overflow': 'invalid_contract',
            'model_request_limit': 'invalid_contract', 'model_response_limit': 'invalid_contract',
            'model_token_accounting_unavailable': 'invalid_contract', 'model_profile_invalid': 'invalid_contract',
        }.get(code)
        self.failure_kind = inferred_kind if failure_kind is None else failure_kind
        if self.failure_kind is not None and (type(self.failure_kind) is not str or self.failure_kind not in {
            'transport', 'incomplete', 'invalid_json', 'invalid_contract',
        }):
            raise ValueError('provider_failure_kind_invalid')
        super().__init__(code)

    @property
    def diagnostic(self):
        return validate_failure_diagnostic(self._diagnostic) if self._diagnostic is not None else None

@dataclass
class ModelRequest:
    messages: list[dict[str, str]]
    tools: list[dict] = field(default_factory=list)
    response_schema: dict | None = None
    max_input_tokens: int = 64000
    max_output_tokens: int = 2000
    timeout_seconds: float = 60.0
    metadata: dict = field(default_factory=dict)

@dataclass
class ConversationRequest:
    """Opt-in text/tool conversation; omitted thinking settings leave server defaults unspecified."""
    messages: list[dict[str, Any]]
    tools: list[dict] = field(default_factory=list)
    response_schema: dict | None = None
    max_input_tokens: int = 64000
    max_output_tokens: int = 2000
    timeout_seconds: float = 60.0
    metadata: dict = field(default_factory=dict)
    contract_version: str = CONVERSATION_CONTRACT_VERSION
    thinking: dict[str, str] | None = None
    reasoning_effort: str | None = None

@dataclass(frozen=True)
class ModelBudget:
    """Explicit caller-owned limits; applying them never changes request defaults."""
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self):
        _validate_limits(self.max_input_tokens, self.max_output_tokens, self.timeout_seconds)

    def apply(self, request: ModelRequest) -> ModelRequest:
        validate_request(request)
        return replace(request, max_input_tokens=self.max_input_tokens,
                       max_output_tokens=self.max_output_tokens, timeout_seconds=self.timeout_seconds)

@dataclass
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    model: str = 'mock'
    provider: str = 'mock'
    finish_reason: str = 'stop'
    usage_kind: str = 'unknown'
    metrics: dict[str, Any] = field(default_factory=dict)

@dataclass
class ConversationResponse(ModelResponse):
    """Normalized output plus bounded wire evidence and an assistant message for replay."""
    assistant_message: dict[str, Any] = field(default_factory=dict)
    raw_usage: dict[str, Any] = field(default_factory=dict)
    system_fingerprint: str | None = None
    contract_version: str = CONVERSATION_CONTRACT_VERSION

LLMResponse = ModelResponse

@dataclass(frozen=True)
class ProviderCapabilities:
    json_mode: bool = False
    usage: bool = False
    cancel: bool = False
    health: bool = False

class LLMProvider(Protocol):
    name: str
    def complete(self, request: ModelRequest) -> ModelResponse: ...

class HTTPTransport(Protocol):
    def post(self, endpoint: str, body: bytes, timeout_seconds: float, max_response_bytes: int) -> dict: ...

def _validate_limits(input_tokens, output_tokens, seconds):
    if type(input_tokens) is not int or type(output_tokens) is not int or input_tokens <= 0 or output_tokens <= 0:
        raise ProviderError('model_request_invalid')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ProviderError('model_request_invalid')

def validate_request(request):
    if not isinstance(request, ModelRequest) or not request.messages:
        raise ProviderError('model_request_invalid')
    _validate_limits(request.max_input_tokens, request.max_output_tokens, request.timeout_seconds)
    for message in request.messages:
        if not isinstance(message, dict) or set(message) != {'role', 'content'} or message['role'] not in ('system', 'user', 'assistant', 'tool') or not isinstance(message['content'], str):
            raise ProviderError('model_request_invalid')
    if not isinstance(request.tools, list) or not all(isinstance(t, dict) for t in request.tools) or request.response_schema is not None and not isinstance(request.response_schema, dict) or not isinstance(request.metadata, dict):
        raise ProviderError('model_request_invalid')

def _validate_conversation_message(message):
    if type(message) is not dict or not {'role', 'content'} <= message.keys():
        raise ValueError()
    role, content = message['role'], message['content']
    if role in ('system', 'user'):
        if set(message) != {'role', 'content'} or type(content) is not str:
            raise ValueError()
    elif role == 'tool':
        if set(message) != {'role', 'content', 'tool_call_id'} or type(content) is not str or type(message['tool_call_id']) is not str or not message['tool_call_id']:
            raise ValueError()
    elif role == 'assistant':
        if set(message) - {'role', 'content', 'reasoning_content', 'tool_calls'} or content is not None and type(content) is not str:
            raise ValueError()
        if message.get('reasoning_content') is not None and type(message['reasoning_content']) is not str:
            raise ValueError()
        calls = message.get('tool_calls', [])
        if type(calls) is not list:
            raise ValueError()
        ids = set()
        for call in calls:
            if type(call) is not dict or set(call) != {'id', 'type', 'function'} or type(call['id']) is not str or not 1 <= len(call['id']) <= 256 or call['id'] in ids or call['type'] != 'function':
                raise ValueError()
            function = call['function']
            if type(function) is not dict or set(function) != {'name', 'arguments'} or type(function['name']) is not str or not 1 <= len(function['name']) <= 128 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in function['name']) or type(function['arguments']) is not str:
                raise ValueError()
            ids.add(call['id'])
    else:
        raise ValueError()

def validate_conversation_request(request):
    if not isinstance(request, ConversationRequest) or request.contract_version != CONVERSATION_CONTRACT_VERSION:
        raise ProviderError('model_request_invalid')
    _validate_limits(request.max_input_tokens, request.max_output_tokens, request.timeout_seconds)
    if type(request.messages) is not list or not request.messages or type(request.tools) is not list or not all(type(t) is dict for t in request.tools) or request.response_schema is not None and type(request.response_schema) is not dict or type(request.metadata) is not dict:
        raise ProviderError('model_request_invalid')
    if request.thinking is not None and (type(request.thinking) is not dict
            or set(request.thinking) != {'type'} or type(request.thinking['type']) is not str
            or request.thinking['type'] not in ('enabled', 'disabled')):
        raise ProviderError('model_request_invalid')
    if request.reasoning_effort is not None and (type(request.reasoning_effort) is not str
            or request.reasoning_effort not in ('none', 'low', 'high', 'max')):
        raise ProviderError('model_request_invalid')
    if request.thinking is not None and request.reasoning_effort is not None:
        if (request.thinking['type'] == 'enabled') != (request.reasoning_effort != 'none'):
            raise ProviderError('model_request_invalid')
    try:
        pending, seen = set(), set()
        for message in request.messages:
            _validate_conversation_message(message)
            if message['role'] == 'tool':
                if message['tool_call_id'] not in pending:
                    raise ValueError()
                pending.remove(message['tool_call_id'])
            else:
                if pending:
                    raise ValueError()
                calls = message.get('tool_calls', [])
                pending = {call['id'] for call in calls}
                if pending & seen:
                    raise ValueError()
                seen.update(pending)
        if pending:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ProviderError('model_request_invalid') from None
    return request

def _conversation_usage(raw):
    """Retain only fixed official counters, never arbitrary server-supplied objects."""
    if raw is None:
        return {}
    if type(raw) is not dict:
        raise ValueError()
    result = {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
        if name in raw:
            if type(raw[name]) is not int:
                raise ValueError()
            result[name] = raw[name]
    for name, counter in (('prompt_tokens_details', 'cached_tokens'),
                          ('completion_tokens_details', 'reasoning_tokens')):
        if name in raw:
            if type(raw[name]) is not dict:
                raise ValueError()
            result[name] = {counter: raw[name][counter]} if counter in raw[name] else {}
    values = [value for value in result.values() if type(value) is not dict]
    values.extend(value for detail in result.values() if type(detail) is dict for value in detail.values())
    if any(type(value) is not int or not 0 <= value <= 2 ** 63 - 1 for value in values):
        raise ValueError()
    prompt, output, total = (result.get(name) for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
    hit, miss = (result.get(name) for name in ('prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'))
    cached = result.get('prompt_tokens_details', {}).get('cached_tokens')
    reasoning = result.get('completion_tokens_details', {}).get('reasoning_tokens')
    if prompt is not None and output is not None and total is not None and total != prompt + output:
        raise ValueError()
    if prompt is not None and (any(value > prompt for value in (hit, miss, cached) if value is not None) or hit is not None and miss is not None and hit + miss != prompt):
        raise ValueError()
    if hit is not None and cached is not None and hit != cached or output is not None and reasoning is not None and reasoning > output:
        raise ValueError()
    return result

def validate_response(response):
    if inspect.iscoroutine(response):
        response.close()
        raise ProviderError('provider_contract')
    if not isinstance(response, ModelResponse) or not isinstance(response.content, str) or response.usage_kind not in ('measured', 'estimated', 'unknown') or not isinstance(response.usage, dict) or not isinstance(response.metrics, dict) or any(type(v) is not int or v < 0 for v in response.usage.values()):
        raise ProviderError('provider_contract')
    if response.usage_kind != 'unknown' and not {'input_tokens', 'output_tokens'} <= response.usage.keys():
        raise ProviderError('provider_contract')
    if isinstance(response, ConversationResponse):
        try:
            _validate_conversation_message(response.assistant_message)
            raw_usage = _conversation_usage(response.raw_usage)
            if response.contract_version != CONVERSATION_CONTRACT_VERSION or response.assistant_message['role'] != 'assistant' or response.content != (response.assistant_message['content'] if response.assistant_message['content'] is not None else '') or response.tool_calls != response.assistant_message.get('tool_calls', []) or raw_usage != response.raw_usage:
                raise ValueError()
            if type(response.model) is not str or not 1 <= len(response.model) <= 256 or response.system_fingerprint is not None and (type(response.system_fingerprint) is not str or len(response.system_fingerprint) > 256) or response.finish_reason not in _CONVERSATION_FINISH_REASONS or type(response.latency_ms) is not int or response.latency_ms < 0:
                raise ValueError()
            known = {'prompt_tokens', 'completion_tokens'} <= raw_usage.keys()
            expected = {'input_tokens': raw_usage['prompt_tokens'], 'output_tokens': raw_usage['completion_tokens']} if known else {}
            if response.usage != expected or response.usage_kind != ('measured' if known else 'unknown'):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ProviderError('provider_contract') from None
    return response

def invoke(provider: LLMProvider, request: ModelRequest) -> ModelResponse:
    validate_request(request)
    return validate_response(provider.complete(request))
