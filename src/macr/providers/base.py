from __future__ import annotations
import inspect
import math
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

class ProviderError(Exception):
    def __init__(self, code: str, *, response: ModelResponse | None = None, failure_kind: str | None = None):
        self.code = code
        # A validated failure envelope is evidence for recording, never a successful output.
        self.response = validate_response(response) if response is not None else None
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

@dataclass
class ModelRequest:
    messages: list[dict[str, str]]
    tools: list[dict] = field(default_factory=list)
    response_schema: dict | None = None
    max_input_tokens: int = 64000
    max_output_tokens: int = 2000
    timeout_seconds: float = 60.0
    metadata: dict = field(default_factory=dict)

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

def validate_response(response):
    if inspect.iscoroutine(response):
        response.close()
        raise ProviderError('provider_contract')
    if not isinstance(response, ModelResponse) or not isinstance(response.content, str) or response.usage_kind not in ('measured', 'estimated', 'unknown') or not isinstance(response.usage, dict) or not isinstance(response.metrics, dict) or any(type(v) is not int or v < 0 for v in response.usage.values()):
        raise ProviderError('provider_contract')
    if response.usage_kind != 'unknown' and not {'input_tokens', 'output_tokens'} <= response.usage.keys():
        raise ProviderError('provider_contract')
    return response

def invoke(provider: LLMProvider, request: ModelRequest) -> ModelResponse:
    validate_request(request)
    return validate_response(provider.complete(request))
