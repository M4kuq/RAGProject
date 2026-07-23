from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from app.rag.generation import TokenUsage


@dataclass(frozen=True)
class PricingRate:
    input_per_1m: float
    output_per_1m: float


# Best-effort defaults for estimated generation cost only. They are not an
# authoritative billing source and can be replaced through
# GENERATION_PRICING_OVERRIDES.
DEFAULT_PRICING: Mapping[str, Mapping[str, float]] = {
    "fake:fake-rag-answer": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "ollama:llama3.1": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "lmstudio:qwen3.5-4b": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "lmstudio:qwen3.5-9b": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "nvidia:*": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "openai:gpt-5.5": {"input_per_1m": 1.25, "output_per_1m": 10.0},
    "openai:gpt-5.4": {"input_per_1m": 1.25, "output_per_1m": 10.0},
    "anthropic:claude-sonnet-4-*": {"input_per_1m": 3.0, "output_per_1m": 15.0},
    "gemini:gemini-2.5-flash-lite*": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    "gemini:gemini-2.5-flash*": {"input_per_1m": 0.30, "output_per_1m": 2.50},
}


def estimate_cost_usd(
    provider: str,
    model: str,
    usage: TokenUsage | None,
    *,
    pricing_overrides: Mapping[str, Any] | None = None,
) -> float | None:
    """Estimate USD generation cost from usage tokens and provider/model rates.

    Rates are USD per 1M tokens and are estimates, not billing records. Currency
    conversion, provider discounts, cached-token pricing, and tiered pricing are
    intentionally out of scope for this B1 metadata foundation. Missing usage,
    unknown models, and invalid override entries degrade to ``None``.
    """

    if usage is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    if usage.input_tokens < 0 or usage.output_tokens < 0:
        return None
    rate = _find_rate(
        provider=provider,
        model=model,
        pricing_overrides=pricing_overrides,
    )
    if rate is None:
        return None
    cost = (
        usage.input_tokens * rate.input_per_1m / 1_000_000
        + usage.output_tokens * rate.output_per_1m / 1_000_000
    )
    return round(cost, 8)


def _find_rate(
    *,
    provider: str,
    model: str,
    pricing_overrides: Mapping[str, Any] | None,
) -> PricingRate | None:
    provider_key = provider.strip().lower()
    model_key = model.strip().lower()
    if not provider_key or not model_key:
        return None
    table = _pricing_entries(DEFAULT_PRICING)
    override_entries = _pricing_entries(pricing_overrides or {})
    for pattern, rate in [*override_entries, *table]:
        pattern_provider, separator, model_pattern = pattern.partition(":")
        if not separator:
            continue
        if provider_key != pattern_provider.strip().lower():
            continue
        if fnmatchcase(model_key, model_pattern.strip().lower()):
            return rate
    return None


def _pricing_entries(pricing: Mapping[str, Any]) -> list[tuple[str, PricingRate]]:
    entries: list[tuple[str, PricingRate]] = []
    for key, value in pricing.items():
        if not isinstance(key, str):
            continue
        rate = _pricing_rate(value)
        if rate is None:
            continue
        entries.append((key, rate))
    return entries


def _pricing_rate(value: object) -> PricingRate | None:
    if not isinstance(value, Mapping):
        return None
    input_rate = _non_negative_number(value.get("input_per_1m"))
    output_rate = _non_negative_number(value.get("output_per_1m"))
    if input_rate is None or output_rate is None:
        return None
    return PricingRate(input_per_1m=input_rate, output_per_1m=output_rate)


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number
