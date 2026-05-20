from __future__ import annotations

from macr.providers.base import LLMResponse


class MockLLMProvider:
    """Provider placeholder used until a real LLM API key is configured."""

    name = "mock"

    async def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        response_schema: dict | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="Mock provider response. Real LLM provider is not configured yet.",
            usage={"input_tokens": 0, "output_tokens": 0},
            model="mock-llm",
            provider=self.name,
        )

