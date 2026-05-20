from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    model: str = "mock"
    provider: str = "mock"
    finish_reason: str = "stop"


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        ...

