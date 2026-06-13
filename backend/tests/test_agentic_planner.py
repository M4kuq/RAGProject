from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.rag.agentic_planner import (
    AgenticPlannerAttemptSummary,
    AgenticPlannerSufficiencySummary,
    AgenticStrategyPlanningRequest,
    OpenAICompatibleAgenticStrategyPlanner,
    create_agentic_strategy_planner,
)
from app.rag.strategy import RetrievalStrategy


def test_lmstudio_agentic_planner_uses_override_model_and_redacts_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action":"retrieve","strategy":"hybrid",'
                                '"confidence":0.8,"reason_codes":["keyword_heavy"]}'
                            )
                        }
                    }
                ]
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("app.rag.agentic_planner.httpx.post", fake_post)
    planner = create_agentic_strategy_planner(
        Settings(
            app_env="test",
            router_mode="llm",
            generation_provider="lmstudio",
            generation_model_name="qwen3.5-9b",
            router_llm_planner_model_name="lmstudio-community/Qwen3.5-4B-GGUF:Q4_K_M",
            lmstudio_api_key="lm-studio",
            lmstudio_base_url="http://host.docker.internal:1234/v1",
            router_llm_planner_timeout_seconds=15,
        )
    )

    assert isinstance(planner, OpenAICompatibleAgenticStrategyPlanner)
    result = planner.plan(
        AgenticStrategyPlanningRequest(
            query="Find policy api_key=super-secret-token",
            phase="fallback",
            available_strategies=(RetrievalStrategy.HYBRID,),
            candidate_strategies=(RetrievalStrategy.HYBRID,),
            attempted_strategies=(RetrievalStrategy.DENSE,),
            attempt_summaries=(
                AgenticPlannerAttemptSummary(
                    strategy=RetrievalStrategy.DENSE,
                    role="initial",
                    candidate_count=1,
                    top_score=0.1,
                ),
            ),
            sufficiency_summaries=(
                AgenticPlannerSufficiencySummary(
                    sufficient=False,
                    score=0.3,
                    reason_codes=("low_top_score",),
                    candidate_count=1,
                    selected_count=1,
                    top_score=0.1,
                    fallback_recommended=True,
                    source_diversity=1,
                ),
            ),
            remaining_retrieval_calls=1,
            remaining_fallback_calls=1,
        )
    )

    user_payload = captured["json"]["messages"][1]["content"]
    assert captured["url"] == "http://host.docker.internal:1234/v1/chat/completions"
    assert captured["json"]["model"] == "qwen3.5-4b"
    assert captured["json"]["max_tokens"] == 256
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["json"]["enable_thinking"] is False
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["timeout"] == 15
    assert "super-secret-token" not in user_payload
    assert "raw_chunk" not in user_payload
    assert "content_text" not in user_payload
    assert "raw_prompt" not in user_payload
    assert result.succeeded is True
    assert result.plan is not None
    assert result.plan.strategy == RetrievalStrategy.HYBRID
    assert result.plan.reason_codes == ("keyword_heavy",)


def test_agentic_planner_uses_generation_model_when_override_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action":"retrieve","strategy":"dense",'
                                '"confidence":0.6,"reason_codes":["default"]}'
                            )
                        }
                    }
                ]
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.agentic_planner.httpx.post", fake_post)
    planner = create_agentic_strategy_planner(
        Settings(
            app_env="test",
            router_mode="llm",
            generation_provider="lmstudio",
            generation_model_name="lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
        )
    )

    assert isinstance(planner, OpenAICompatibleAgenticStrategyPlanner)
    planner.plan(_planning_request())

    assert captured["json"]["model"] == "qwen3.5-9b"


@pytest.mark.parametrize(
    ("response_content", "expected_reason"),
    [
        ("", "planner_empty_response"),
        ("not json", "planner_invalid_json"),
        ('{"action":"retrieve","strategy":"sparse"}', "planner_invalid_json"),
    ],
)
def test_agentic_planner_falls_back_on_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
    response_content: str,
    expected_reason: str,
) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": response_content}}]}

    monkeypatch.setattr(
        "app.rag.agentic_planner.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    planner = OpenAICompatibleAgenticStrategyPlanner(
        provider="lmstudio",
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-4b",
        timeout_seconds=10,
        max_output_tokens=256,
    )

    result = planner.plan(_planning_request())

    assert result.plan is None
    assert result.fallback_reason == expected_reason


def test_agentic_planner_falls_back_on_http_error_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner = OpenAICompatibleAgenticStrategyPlanner(
        provider="lmstudio",
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-4b",
        timeout_seconds=10,
        max_output_tokens=256,
    )

    class Response:
        status_code = 500

        def json(self) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(
        "app.rag.agentic_planner.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    http_result = planner.plan(_planning_request())

    def raise_timeout(*args: Any, **kwargs: Any) -> None:
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("app.rag.agentic_planner.httpx.post", raise_timeout)
    timeout_result = planner.plan(_planning_request())

    assert http_result.plan is None
    assert http_result.fallback_reason == "planner_http_error"
    assert timeout_result.plan is None
    assert timeout_result.fallback_reason == "planner_http_error"


def test_agentic_planner_falls_back_on_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200

        def json(self) -> list[dict[str, Any]]:
            return [{"choices": [{"message": {"content": "{}"}}]}]

    monkeypatch.setattr(
        "app.rag.agentic_planner.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    planner = OpenAICompatibleAgenticStrategyPlanner(
        provider="lmstudio",
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-4b",
        timeout_seconds=10,
        max_output_tokens=256,
    )

    result = planner.plan(_planning_request())

    assert result.plan is None
    assert result.fallback_reason == "planner_invalid_response"


def test_agentic_planner_is_not_created_for_rule_based_or_fake_provider() -> None:
    assert create_agentic_strategy_planner(Settings(app_env="test")) is None
    assert (
        create_agentic_strategy_planner(
            Settings(app_env="test", router_mode="llm", generation_provider="fake")
        )
        is None
    )


def _planning_request() -> AgenticStrategyPlanningRequest:
    return AgenticStrategyPlanningRequest(
        query="alpha policy",
        phase="initial",
        available_strategies=(RetrievalStrategy.DENSE,),
        candidate_strategies=(RetrievalStrategy.DENSE,),
    )
