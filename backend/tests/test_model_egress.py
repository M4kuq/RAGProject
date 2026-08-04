from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.model_egress import (
    MAX_MODEL_EGRESS_FIELD_CHARS,
    ModelEgressBlockedError,
    ModelEgressGuard,
)
from app.ingest.embedding import create_embedding_adapter
from app.rag.agentic_planner import (
    AgenticStrategyPlanningRequest,
    OpenAICompatibleAgenticStrategyPlanner,
    create_agentic_strategy_planner,
)
from app.rag.generation import (
    AnswerGenerationError,
    GenerationContextItem,
    GenerationRequest,
    OpenAICompatibleChatAnswerGenerator,
    create_answer_generator,
)
from app.rag.llm_orchestrator import (
    LLMToolPlanningRequest,
    OpenAICompatibleJSONToolPlanner,
    create_llm_tool_call_planner,
)
from app.rag.rerank import RerankCandidate, create_reranker
from app.rag.strategy import RetrievalStrategy

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pii_egress_dev_v1.json"


def _guard(
    *,
    policy: str = "mask",
    allowed_providers: tuple[str, ...] = ("openai",),
    pii_masking_enabled: bool = True,
) -> ModelEgressGuard:
    return ModelEgressGuard(
        policy=cast(Any, policy),
        allowed_providers=allowed_providers,
        pii_masking_enabled=pii_masking_enabled,
    )


def _generation_request() -> GenerationRequest:
    return GenerationRequest(
        message="What does the policy say? Contact demo.user@example.test.",
        context_items=[
            GenerationContextItem(
                document_chunk_id=10,
                source_label="owner-demo.user@example.test.md",
                text="Owner email: demo.user@example.test. Alpha is approved.",
                local_citation_id=1,
            )
        ],
        max_output_chars=500,
        system_instructions="Do not contact demo.user@example.test.",
        task_instructions="Return evidence for demo.user@example.test.",
        response_format={"type": "json_object", "description": "demo.user@example.test"},
    )


def test_settings_default_to_fail_closed_and_normalize_allowlist() -> None:
    defaults = Settings(_env_file=None, app_env="test")
    assert defaults.external_model_egress_policy == "deny"
    assert defaults.external_model_egress_allowed_providers == []

    explicit = Settings(
        _env_file=None,
        app_env="test",
        external_model_egress_policy="MASK",
        external_model_egress_allowed_providers="OpenAI, BEDROCK, qwen",
    )
    assert explicit.external_model_egress_policy == "mask"
    assert explicit.external_model_egress_allowed_providers == ["bedrock", "openai", "qwen"]

    with pytest.raises(ValidationError, match="unsupported providers"):
        Settings(
            _env_file=None,
            app_env="test",
            external_model_egress_allowed_providers=["unknown-cloud"],
        )


def test_synthetic_fixture_has_zero_masked_value_leakage_and_clean_utility() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    guard = _guard()

    for case in fixture["cases"]:
        inputs = tuple(case["inputs"])
        if case["expected_action"] == "blocked":
            with pytest.raises(ModelEgressBlockedError) as exc_info:
                guard.protect_texts(inputs, provider="openai", purpose="fixture")
            assert exc_info.value.reason_code == case["expected_reason"], case["id"]
            continue

        result = guard.protect_texts(inputs, provider="openai", purpose="fixture")
        assert result.audit.action == case["expected_action"], case["id"]
        assert {name for name, _ in result.audit.entity_counts} == set(case["expected_types"]), (
            case["id"]
        )
        for raw_value in case["pii_values"]:
            assert all(raw_value not in text for text in result.texts), case["id"]
        if case["expected_action"] == "allowed":
            assert result.texts == inputs, case["id"]


def test_masking_preserves_references_across_nested_payload() -> None:
    protected = _guard().protect_payload(
        {
            "question": "Owner same.person@example.test",
            "context": ["Contact same.person@example.test", {"clean": "alpha policy"}],
        },
        provider="openai",
        purpose="nested",
    )
    serialized = json.dumps(protected.payload, ensure_ascii=False)
    assert "same.person@example.test" not in serialized
    assert serialized.count("[PII_EMAIL_1]") == 2
    assert "alpha policy" in serialized


def test_mapping_keys_are_also_masked() -> None:
    protected = _guard().protect_payload(
        {"demo.user@example.test": "clean value"},
        provider="openai",
        purpose="mapping_key",
    )
    assert protected.payload == {"[PII_EMAIL_1]": "clean value"}


def test_policy_deny_allowlist_masking_disabled_and_unknown_provider_block() -> None:
    scenarios = [
        (_guard(policy="deny"), "openai", "egress_policy_denied"),
        (_guard(allowed_providers=("bedrock",)), "openai", "provider_not_allowed"),
        (_guard(pii_masking_enabled=False), "openai", "pii_masking_disabled"),
        (_guard(), "unclassified-provider", "unknown_provider"),
    ]
    for guard, provider, reason_code in scenarios:
        with pytest.raises(ModelEgressBlockedError) as exc_info:
            guard.protect_texts(["clean input"], provider=provider, purpose="policy")
        assert exc_info.value.reason_code == reason_code


@pytest.mark.parametrize(
    ("value", "reason_code"),
    [
        ("[PII_EMAIL_1]", "reserved_placeholder_collision"),
        ("sk-" + "a" * 24, "provider_token"),
        ("x" * (MAX_MODEL_EGRESS_FIELD_CHARS + 1), "payload_too_large"),
    ],
)
def test_collision_tokens_and_oversized_payload_fail_closed(
    value: str,
    reason_code: str,
) -> None:
    with pytest.raises(ModelEgressBlockedError) as exc_info:
        _guard().protect_texts([value], provider="openai", purpose="hardening")
    assert exc_info.value.reason_code == reason_code


def test_local_provider_bypass_and_explicit_rollback_are_exact() -> None:
    value = "Contact demo.user@example.test"
    local = _guard(policy="deny").protect_texts([value], provider="lmstudio", purpose="generation")
    rollback = _guard(policy="allow").protect_texts(
        [value], provider="openai", purpose="generation"
    )
    assert local.texts == (value,)
    assert local.audit.action == "local_bypass"
    assert rollback.texts == (value,)
    assert rollback.audit.reason_codes == ("explicit_unmasked_allow",)


def test_audit_log_contains_counts_only_not_raw_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_value = "audit.person@example.test"
    with caplog.at_level(logging.INFO, logger="app.core.model_egress"):
        result = _guard().protect_texts(
            [f"Email: {raw_value}"],
            provider="openai",
            purpose="audit_test",
        )
    assert result.audit.masked_count == 1
    assert raw_value not in caplog.text
    record = next(
        record for record in caplog.records if record.message == "model egress policy decision"
    )
    assert record.model_egress_entity_counts == {"EMAIL": 1}  # type: ignore[attr-defined]
    assert not any(raw_value in repr(value) for value in record.__dict__.values())


def test_generation_factory_blocks_before_transport_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_post(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("transport must not be called")

    monkeypatch.setattr("app.rag.generation.httpx.post", unexpected_post)
    generator = create_answer_generator(
        Settings(
            _env_file=None,
            app_env="test",
            generation_provider="openai",
            openai_api_key="synthetic-test-key",
        )
    )
    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_generation_request())
    assert exc_info.value.error_code == "model_egress_blocked"
    assert exc_info.value.error_category == "egress_policy_denied"
    assert called is False


def test_generation_factory_masks_every_prompt_field_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"output_text": "Alpha is approved [1]."}

    def fake_post(*args: object, **kwargs: Any) -> Response:
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = create_answer_generator(
        Settings(
            _env_file=None,
            app_env="test",
            generation_provider="openai",
            openai_api_key="synthetic-test-key",
            external_model_egress_policy="mask",
            external_model_egress_allowed_providers=["openai"],
        )
    )
    result = generator.generate(_generation_request())
    outbound = json.dumps(captured["json"], ensure_ascii=False)
    assert result.content == "Alpha is approved [1]."
    assert "demo.user@example.test" not in outbound
    assert "[PII_EMAIL_1]" in outbound


def test_lmstudio_generation_factory_remains_unwrapped() -> None:
    generator = create_answer_generator(
        Settings(_env_file=None, app_env="test", generation_provider="lmstudio")
    )
    assert isinstance(generator, OpenAICompatibleChatAnswerGenerator)
    assert generator.egress_guard is None
    assert generator.egress_provider == "lmstudio"


def test_external_agentic_planners_block_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_post(*args: object, **kwargs: object) -> object:
        raise AssertionError("transport must not be called")

    monkeypatch.setattr("app.rag.agentic_planner.httpx.post", unexpected_post)
    monkeypatch.setattr("app.rag.llm_orchestrator.httpx.post", unexpected_post)
    settings = Settings(
        _env_file=None,
        app_env="test",
        router_mode="llm",
        generation_provider="openai",
        openai_api_key="synthetic-test-key",
    )
    strategy_planner = create_agentic_strategy_planner(settings)
    assert isinstance(strategy_planner, OpenAICompatibleAgenticStrategyPlanner)
    strategy_result = strategy_planner.plan(
        AgenticStrategyPlanningRequest(
            query="alpha policy",
            phase="initial",
            available_strategies=(RetrievalStrategy.DENSE,),
            candidate_strategies=(RetrievalStrategy.DENSE,),
        )
    )
    assert strategy_result.fallback_reason == "planner_egress_egress_policy_denied"

    tool_planner = create_llm_tool_call_planner(settings)
    assert isinstance(tool_planner, OpenAICompatibleJSONToolPlanner)
    calls = tool_planner.plan(
        LLMToolPlanningRequest(
            user_query="alpha policy",
            top_k=10,
            max_query_chars=500,
            remaining_timeout_seconds=10,
            remaining_tool_calls=2,
            remaining_search_calls=2,
            available_tools=("dense_search",),
            tool_results=(),
        )
    )
    assert calls == []
    assert tool_planner.last_reason_code == "planner_egress_egress_policy_denied"


def test_bedrock_embedding_and_rerank_mask_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RuntimeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def invoke_model(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {"body": io.BytesIO(json.dumps({"embedding": [0.1] * 256}).encode())}

    class RerankClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def rerank(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {"results": [{"index": 0, "relevanceScore": 0.9}]}

    runtime = RuntimeClient()
    rerank_client = RerankClient()

    def fake_embedding_client(*args: object, **kwargs: object) -> RuntimeClient:
        return runtime

    def fake_rerank_client(*args: object, **kwargs: object) -> RerankClient:
        return rerank_client

    monkeypatch.setattr("app.ingest.embedding.create_aws_client", fake_embedding_client)
    monkeypatch.setattr("app.rag.rerank.create_aws_client", fake_rerank_client)
    settings = Settings(
        _env_file=None,
        app_env="test",
        embedding_provider="bedrock",
        embedding_vector_dimension=256,
        rerank_provider="bedrock",
        external_model_egress_policy="mask",
        external_model_egress_allowed_providers=["bedrock"],
    )
    create_embedding_adapter(settings).embed_texts(["demo.user@example.test"])
    create_reranker(settings).rerank(
        query="owner demo.user@example.test",
        candidates=[
            RerankCandidate(
                document_chunk_id=1,
                text="Contact demo.user@example.test",
                retrieval_score=0.8,
            )
        ],
    )

    embedding_body = cast(str, runtime.calls[0]["body"])
    rerank_payload = json.dumps(rerank_client.calls[0], ensure_ascii=False)
    assert "demo.user@example.test" not in embedding_body
    assert "demo.user@example.test" not in rerank_payload
    assert "[PII_EMAIL_1]" in embedding_body
    assert "[PII_EMAIL_1]" in rerank_payload


def test_production_external_transport_construction_is_factory_scoped() -> None:
    expected_factories = {
        "OpenAIResponsesAnswerGenerator": "create_answer_generator",
        "OpenAICompatibleChatAnswerGenerator": "create_answer_generator",
        "AnthropicMessagesAnswerGenerator": "create_answer_generator",
        "GeminiAnswerGenerator": "create_answer_generator",
        "BedrockConverseAnswerGenerator": "create_answer_generator",
        "OpenAICompatibleAgenticStrategyPlanner": "create_agentic_strategy_planner",
        "OpenAICompatibleJSONToolPlanner": "create_llm_tool_call_planner",
        "BedrockTitanEmbeddingAdapter": "create_embedding_adapter",
        "BedrockRerankerClient": "create_reranker",
    }
    findings: dict[str, list[tuple[str, str | None]]] = {name: [] for name in expected_factories}
    app_root = Path(__file__).parents[1] / "app"

    class FactoryCallVisitor(ast.NodeVisitor):
        def __init__(self, relative_path: str) -> None:
            self.relative_path = relative_path
            self.function_stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id in expected_factories:
                findings[node.func.id].append(
                    (
                        self.relative_path,
                        self.function_stack[-1] if self.function_stack else None,
                    )
                )
            self.generic_visit(node)

    for source_path in app_root.rglob("*.py"):
        relative_path = source_path.relative_to(app_root).as_posix()
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative_path)
        FactoryCallVisitor(relative_path).visit(tree)

    for class_name, expected_factory in expected_factories.items():
        assert findings[class_name], f"missing production construction for {class_name}"
        assert all(
            function_name == expected_factory for _, function_name in findings[class_name]
        ), findings[class_name]
