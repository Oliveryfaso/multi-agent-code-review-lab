from __future__ import annotations

from dataclasses import dataclass

from macr.providers.base import LLMResponse


@dataclass(frozen=True)
class TokenPrices:
    input_cache_hit_per_million: float
    input_cache_miss_per_million: float
    output_per_million: float


DEEPSEEK_DEFAULT_PRICES = TokenPrices(
    input_cache_hit_per_million=0.0028,
    input_cache_miss_per_million=0.14,
    output_per_million=0.28,
)


def estimate_llm_cost(response: LLMResponse) -> dict[str, object]:
    usage = response.usage or {}
    if response.provider != "deepseek":
        return {
            "estimated_cost_usd": None,
            "cache_hit_ratio": None,
            "reason": "cost estimator currently supports deepseek usage fields",
        }
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or usage.get("prompt_tokens_details", {}).get("cached_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or cache_hit + cache_miss)
    output_tokens = int(usage.get("completion_tokens") or 0)
    if cache_miss == 0 and prompt_tokens >= cache_hit:
        cache_miss = max(0, prompt_tokens - cache_hit)
    prices = DEEPSEEK_DEFAULT_PRICES
    cost = (
        cache_hit / 1_000_000 * prices.input_cache_hit_per_million
        + cache_miss / 1_000_000 * prices.input_cache_miss_per_million
        + output_tokens / 1_000_000 * prices.output_per_million
    )
    return {
        "estimated_cost_usd": round(cost, 8),
        "cache_hit_ratio": round(cache_hit / prompt_tokens, 4) if prompt_tokens else 0,
        "prompt_tokens": prompt_tokens,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
        "completion_tokens": output_tokens,
        "pricing": {
            "input_cache_hit_per_million": prices.input_cache_hit_per_million,
            "input_cache_miss_per_million": prices.input_cache_miss_per_million,
            "output_per_million": prices.output_per_million,
        },
    }


def summarize_llm_costs(llm_calls: list[dict]) -> dict[str, object]:
    cost_items = [call.get("cost") or {} for call in llm_calls]
    known = [item for item in cost_items if item.get("estimated_cost_usd") is not None]
    total = sum(float(item["estimated_cost_usd"]) for item in known)
    prompt_tokens = sum(int(item.get("prompt_tokens") or 0) for item in known)
    cache_hit = sum(int(item.get("prompt_cache_hit_tokens") or 0) for item in known)
    return {
        "llm_call_count": len(llm_calls),
        "estimated_cost_usd": round(total, 8),
        "prompt_tokens": prompt_tokens,
        "prompt_cache_hit_tokens": cache_hit,
        "cache_hit_ratio": round(cache_hit / prompt_tokens, 4) if prompt_tokens else 0,
    }
