from copy import deepcopy
from macr.providers.base import ModelRequest, ModelResponse, ProviderCapabilities, ProviderError, validate_request, validate_response

class MockLLMProvider:
    """Scripted contract fixture. Never represents deployed-model readiness."""
    name = 'mock'
    capabilities = ProviderCapabilities()
    def __init__(self, responses: list[ModelResponse] | None = None):
        self.responses = list(responses or [])
        self.requests = []
    def complete(self, request: ModelRequest) -> ModelResponse:
        validate_request(request)
        self.requests.append(deepcopy(request))
        if not self.responses:
            raise ProviderError('mock_script_exhausted')
        return deepcopy(validate_response(self.responses.pop(0)))
