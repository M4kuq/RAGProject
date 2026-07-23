from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.config import Settings
from app.rag.generation import TokenUsage
from app.rag.pricing import estimate_cost_usd


def test_estimate_cost_usd_for_known_model() -> None:
    cost = estimate_cost_usd(
        "openai",
        "gpt-5.5",
        TokenUsage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000),
    )

    assert cost == 6.25


def test_estimate_cost_usd_treats_nvidia_catalog_endpoint_as_free() -> None:
    cost = estimate_cost_usd(
        "nvidia",
        "meta/llama-3.3-70b-instruct",
        TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
    )

    assert cost == 0.0


def test_estimate_cost_usd_unknown_model_returns_none() -> None:
    cost = estimate_cost_usd(
        "openai",
        "unknown-model",
        TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
    )

    assert cost is None


def test_estimate_cost_usd_usage_none_returns_none() -> None:
    assert estimate_cost_usd("openai", "gpt-5.5", None) is None


def test_estimate_cost_usd_uses_pricing_override() -> None:
    cost = estimate_cost_usd(
        "openai",
        "custom-model",
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000),
        pricing_overrides={
            "openai:custom-*": {"input_per_1m": 2.0, "output_per_1m": 8.0},
        },
    )

    assert cost == 10.0


def test_estimate_cost_usd_uses_specific_gemini_flash_lite_rate() -> None:
    cost = estimate_cost_usd(
        "gemini",
        "gemini-2.5-flash-lite",
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000),
    )

    assert cost == 0.5


def test_settings_parses_generation_pricing_overrides_json() -> None:
    settings = Settings(
        _env_file=None,
        generation_pricing_overrides=(
            '{"openai:custom-model":{"input_per_1m":2.0,"output_per_1m":8.0}}'
        ),
    )

    cost = estimate_cost_usd(
        "openai",
        "custom-model",
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000),
        pricing_overrides=cast("dict[str, Any]", settings.generation_pricing_overrides),
    )

    assert cost == 10.0


def test_settings_invalid_generation_pricing_overrides_degrades_to_empty() -> None:
    settings = Settings(_env_file=None, generation_pricing_overrides="{not-json")

    assert settings.generation_pricing_overrides == {}


def test_settings_non_object_generation_pricing_overrides_degrades_to_empty() -> None:
    settings = Settings(_env_file=None, generation_pricing_overrides='["not-an-object"]')

    assert settings.generation_pricing_overrides == {}


def test_settings_empty_generation_pricing_overrides_env_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_PRICING_OVERRIDES", "")

    settings = Settings(_env_file=None)

    assert settings.generation_pricing_overrides == {}

