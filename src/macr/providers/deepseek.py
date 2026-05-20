from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from time import perf_counter
from typing import Any

from macr.providers.base import LLMResponse


class DeepSeekProvider:
    """DeepSeek OpenAI-compatible chat completions provider."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        env_file = _load_local_env()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or env_file.get("DEEPSEEK_API_KEY")
        self.base_url = (
            base_url
            or os.getenv("DEEPSEEK_BASE_URL")
            or env_file.get("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL") or env_file.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        return self.complete_sync(messages, tools, response_schema, metadata)

    def complete_sync(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set.")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_schema:
            payload["response_format"] = {"type": "json_object"}

        started = perf_counter()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek API network error: {exc}") from exc

        latency_ms = int((perf_counter() - started) * 1000)
        data = json.loads(raw)
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=message.get("tool_calls") or [],
            usage=data.get("usage") or {},
            latency_ms=latency_ms,
            model=data.get("model") or self.model,
            provider=self.name,
            finish_reason=choice.get("finish_reason") or "unknown",
        )


def _load_local_env() -> dict[str, str]:
    for directory in [Path.cwd(), *Path.cwd().parents]:
        path = directory / ".env"
        if not path.exists():
            continue
        values: dict[str, str] = {}
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values
    return {}
