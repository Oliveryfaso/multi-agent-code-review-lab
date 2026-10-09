from __future__ import annotations
import inspect
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

class ProviderError(Exception):
    def __init__(self, code: str):
        self.code = code
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

def validate_request(request):
    if not isinstance(request, ModelRequest) or not request.messages or type(request.max_input_tokens) is not int or type(request.max_output_tokens) is not int or request.max_input_tokens <= 0 or request.max_output_tokens <= 0:
        raise ProviderError('model_request_invalid')
    if type(request.timeout_seconds) not in (int, float) or not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0:
        raise ProviderError('model_request_invalid')
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
