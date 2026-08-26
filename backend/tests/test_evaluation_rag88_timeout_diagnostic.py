from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

import app.rag.generation as generation_module
from app.evaluation.rag_service import generate_evaluation_answer
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
    OpenAICompatibleChatAnswerGenerator,
)
from app.services.evaluation_atomic_claim_contracts import canonical_json_bytes
from app.services.evaluation_atomic_claim_review_workflow_service import (
    _join_review_case_process,
    _review_generation_settings,
)
from app.services.evaluation_qwen_confirm_fixture_sensitivity_service import (
    build_rag87_private_fixture,
)
from app.services.evaluation_qwen_context_near_miss_service import Rag86LMInventorySummary
from app.services.evaluation_qwen_multifact_interference_repair_service import (
    Rag88PrivateFixtureEnvelope,
    _generate_repair_with_generator,
    _sha256,
    build_rag88_experiment_manifest,
    build_rag88_private_fixture,
    build_rag88_reference_catalog,
    run_rag88_experiment,
)
from app.services.evaluation_rag88_timeout_diagnostic_service import (
    RAG88_PRELIVE_COMMIT,
    RAG88_SOURCE_CODE_COMMIT,
    Rag89TimeoutDiagnosticError,
    build_rag89_timeout_diagnostic_report,
)

_DIRECT_TIMEOUT_ORDINALS = (
    10,
    14,
    18,
    23,
    27,
    29,
    31,
    33,
    34,
    35,
    38,
    41,
    43,
    45,
    46,
    49,
    53,
    63,
    65,
    66,
    67,
    69,
    74,
    75,
    77,
    78,
    93,
    97,
    98,
    99,
    102,
    105,
    107,
    109,
    110,
    122,
    126,
    130,
    141,
    143,
)
_DERIVED_UNAVAILABLE_ORDINALS = (24, 28, 32, 36, 44, 64, 68, 76, 100, 108, 144)


def test_fixed_raw_free_result_reconstructs_timeout_and_physical_call_bounds() -> None:
    result_bytes, attempt_bytes = _fixed_sanitized_evidence()

    report = build_rag89_timeout_diagnostic_report(
        result_bytes,
        attempt_bytes,
        source_code_commit_sha=RAG88_SOURCE_CODE_COMMIT,
        enforce_fixed_artifact_hashes=False,
    )

    assert report.direct_timeout_count == 40
    assert report.derived_candidate_unavailable_count == 11
    assert report.pipeline_failure_count == 51
    assert report.derived_pairs_immediately_follow_baseline_count == 11
    assert report.derived_pairs_copy_baseline_latency_count == 11
    assert report.physical_calls.standard_first_request_count == 108
    assert report.physical_calls.candidate_repair_request_count == 25
    assert report.physical_calls.minimum_physical_request_count == 133
    assert report.physical_calls.maximum_physical_request_count == 349
    assert report.physical_calls.exact_physical_request_count_known is False
    assert report.timeout_scope.retry_attempts_share_parent_deadline is True
    assert report.timeout_scope.candidate_whole_flow_has_single_deadline is False
    assert report.next_live_single_coordinate == "timeout_contract"
    rendered = report.model_dump_json()
    assert "SAFE_TEST" not in rendered
    assert report.raw_content_persisted is False


def test_default_diagnosis_rejects_non_authoritative_artifact_hashes() -> None:
    result_bytes, attempt_bytes = _fixed_sanitized_evidence()

    with pytest.raises(
        Rag89TimeoutDiagnosticError,
        match="rag89_result_artifact_hash_drift",
    ):
        build_rag89_timeout_diagnostic_report(result_bytes, attempt_bytes)


def test_delayed_headers_are_classified_as_timeout_with_fake_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(180.0)
        raise httpx.ReadTimeout("delayed_headers", request=request)

    _install_mock_post(monkeypatch, handler)

    with pytest.raises(AnswerGenerationError) as raised:
        _generator().generate(_request())

    assert raised.value.error_category == "timeout"
    assert clock.elapsed_seconds == 180.0


def test_slow_stream_is_closed_when_read_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    stream = _ScriptedStream(
        clock,
        ((90.0, b'{"choices":['), (90.0, httpx.ReadTimeout("slow_stream"))),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=stream)

    _install_mock_post(monkeypatch, handler)

    with pytest.raises(AnswerGenerationError) as raised:
        _generator().generate(_request())

    assert raised.value.error_category == "timeout"
    assert stream.closed is True
    assert clock.elapsed_seconds == 180.0


def test_partial_body_is_closed_and_classified_without_persisting_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    stream = _ScriptedStream(
        clock,
        ((179.9, b'{"choices":['), (0.1, httpx.ReadError("partial_body"))),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=stream)

    _install_mock_post(monkeypatch, handler)

    with pytest.raises(AnswerGenerationError) as raised:
        _generator().generate(_request())

    assert raised.value.error_category == "connection"
    assert stream.closed is True
    assert clock.elapsed_seconds == pytest.approx(180.0)


def test_timeout_cleanup_stops_transport_and_does_not_require_kill() -> None:
    transport = _FakeTransportState()
    process = _FakeProcess(transport)

    completed = _join_review_case_process(process, timeout_seconds=180.0)  # type: ignore[arg-type]

    assert completed is False
    assert process.is_alive() is False
    assert transport.stopped is True
    assert process.terminate_count == 1
    assert process.kill_count == 0


def test_next_request_is_not_blocked_after_timed_out_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _SequentialTransportState()

    def handler(request: httpx.Request) -> httpx.Response:
        state.request_count += 1
        if state.request_count == 1:
            stream = _ScriptedStream(
                state.clock,
                ((180.0, httpx.ReadTimeout("first_timeout")),),
                on_close=lambda: setattr(state, "first_request_closed", True),
            )
            return httpx.Response(200, request=request, stream=stream)
        return httpx.Response(200, request=request, json=_valid_payload())

    _install_mock_post(monkeypatch, handler)

    with pytest.raises(AnswerGenerationError):
        _generator().generate(_request())
    second = _generator().generate(_request())

    assert state.first_request_closed is True
    assert state.request_count == 2
    assert second.content


def test_standard_retry_path_has_three_physical_request_upper_bound() -> None:
    generator = _ThreeAttemptGenerator()

    result, _metadata = generate_evaluation_answer(
        _review_generation_settings(),
        generator,
        _request(),
    )

    assert generator.call_count == 3
    assert result.content


def test_repair_path_has_no_internal_retry() -> None:
    generator = _AlwaysEmptyGenerator()

    result = _generate_repair_with_generator(
        generator,
        question="SAFE_TEST_QUESTION",
        context_items=tuple(_request().context_items),
        pass1_answer="SAFE_TEST_PASS1",
    )

    assert generator.call_count == 1
    assert result.reason_code == "rag88_repair_empty_content"


def test_one_timed_out_baseline_derives_one_unavailable_candidate_without_duplicate_call() -> None:
    envelope = build_rag88_private_fixture(b"rag89-private-test-entropy-v1-000001")
    rag87 = build_rag87_private_fixture(b"rag89-reference-test-entropy-v1-0001")
    manifest = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=hashlib.sha256(canonical_json_bytes(envelope)).hexdigest(),
        reference_catalog=build_rag88_reference_catalog(rag87),
    )
    generator = _SingleBaselineTimeoutGenerator(envelope)
    inventory = _stable_inventory()

    result = run_rag88_experiment(
        manifest,
        envelope,
        prelive_commit_sha="1" * 40,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=generator,
    )

    direct = [
        item
        for item in result.observations
        if item.pipeline_failure_reason_code == "rag88_generation_timeout"
    ]
    derived = [
        item
        for item in result.observations
        if item.pipeline_failure_reason_code == "rag88_candidate_pass1_unavailable"
    ]
    assert len(direct) == 1
    assert len(derived) == 1
    assert direct[0].variant == "combined_baseline"
    assert derived[0].execution_ordinal == direct[0].execution_ordinal + 1
    assert result.pipeline_failure_count == 2
    assert generator.call_count == 143


def _fixed_sanitized_evidence() -> tuple[bytes, bytes]:
    direct = set(_DIRECT_TIMEOUT_ORDINALS)
    derived = set(_DERIVED_UNAVAILABLE_ORDINALS)
    observations: list[dict[str, object]] = []
    baseline_latency_by_group: dict[tuple[int, str], int] = {}
    for ordinal in range(1, 145):
        repeat = (ordinal - 1) // 48 + 1
        group_position = ((ordinal - 1) % 48) // 4 + 1
        group_id = f"R89-SAFE-{repeat:01d}-{group_position:02d}"
        slot = (ordinal - 1) % 4
        variant = (
            "single_a"
            if slot == 0
            else "single_b"
            if slot == 1
            else "combined_baseline"
            if slot == 2
            else "combined_candidate"
        )
        failure: str | None = None
        repair_latency: int | None = None
        latency = 100_000 + ordinal
        if ordinal in direct:
            failure = "rag88_review_generation_case_wall_clock_timeout"
            latency = 180_000 + ordinal
        if variant == "combined_baseline":
            baseline_latency_by_group[(repeat, group_id)] = latency
        if ordinal in derived:
            failure = "rag88_candidate_pass1_unavailable"
            latency = baseline_latency_by_group[(repeat, group_id)]
        elif variant == "combined_candidate":
            repair_latency = 10_000 + ordinal
            latency = baseline_latency_by_group[(repeat, group_id)] + repair_latency
        observations.append(
            {
                "group_id": group_id,
                "repeat": repeat,
                "variant": variant,
                "execution_ordinal": ordinal,
                "latency_ms": latency,
                "repair_latency_ms": repair_latency,
                "pipeline_failure_reason_code": failure,
            }
        )
    result = {
        "prelive_commit_sha": RAG88_PRELIVE_COMMIT,
        "model_call_count": 144,
        "pipeline_failure_count": 51,
        "raw_content_persisted": False,
        "chain_of_thought_persisted": False,
        "observations": observations,
    }
    attempt = {
        "status": "started",
        "prelive_commit_sha": RAG88_PRELIVE_COMMIT,
        "expected_model_call_count": 144,
        "repeat_replacement_or_rerun_allowed": False,
        "raw_content_persisted": False,
    }
    return (
        json.dumps(result, sort_keys=True).encode(),
        json.dumps(attempt, sort_keys=True).encode(),
    )


class _FakeClock:
    def __init__(self) -> None:
        self.elapsed_seconds = 0.0

    def advance(self, seconds: float) -> None:
        self.elapsed_seconds += seconds


class _ScriptedStream(httpx.SyncByteStream):
    def __init__(
        self,
        clock: _FakeClock,
        actions: tuple[tuple[float, bytes | httpx.HTTPError], ...],
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.clock = clock
        self.actions = actions
        self.on_close = on_close
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for seconds, action in self.actions:
            self.clock.advance(seconds)
            if isinstance(action, httpx.HTTPError):
                raise action
            yield action

    def close(self) -> None:
        self.closed = True
        if self.on_close is not None:
            self.on_close()


def _install_mock_post(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    transport = httpx.MockTransport(handler)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout")
        with httpx.Client(transport=transport, timeout=timeout) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(generation_module.httpx, "post", post)


def _valid_payload() -> dict[str, object]:
    return {"choices": [{"message": {"content": "SAFE_TEST_RESULT [1]"}}]}


def _request() -> GenerationRequest:
    return GenerationRequest(
        message="SAFE_TEST_MESSAGE",
        context_items=(
            GenerationContextItem(
                document_chunk_id=1,
                source_label="SAFE_TEST_SOURCE",
                text="SAFE_TEST_CONTEXT",
                local_citation_id=1,
            ),
        ),
        max_output_chars=12_000,
        temperature=0.0,
    )


def _generator() -> OpenAICompatibleChatAnswerGenerator:
    return OpenAICompatibleChatAnswerGenerator(
        api_key="SAFE_TEST_KEY",
        base_url="http://127.0.0.1:1",
        model_name="qwen/qwen3.5-9b",
        timeout_seconds=180,
        max_output_tokens=8192,
    )


class _FakeTransportState:
    def __init__(self) -> None:
        self.stopped = False


class _FakeProcess:
    def __init__(self, transport: _FakeTransportState) -> None:
        self.transport = transport
        self.alive = True
        self.terminate_count = 0
        self.kill_count = 0

    def join(self, timeout: float) -> None:
        del timeout

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_count += 1
        self.transport.stopped = True
        self.alive = False

    def kill(self) -> None:
        self.kill_count += 1
        self.transport.stopped = True
        self.alive = False


class _SequentialTransportState:
    def __init__(self) -> None:
        self.clock = _FakeClock()
        self.request_count = 0
        self.first_request_closed = False


class _ThreeAttemptGenerator(AnswerGenerator):
    def __init__(self) -> None:
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        self.call_count += 1
        if self.call_count == 1:
            raise AnswerGenerationError(error_category="empty_content")
        if self.call_count == 2:
            return GenerationResult(content="SAFE_TEST_WITHOUT_MARKER")
        if self.call_count == 3:
            return GenerationResult(content="SAFE_TEST_RESULT [1]")
        raise AssertionError("physical request upper bound exceeded")


class _AlwaysEmptyGenerator(AnswerGenerator):
    def __init__(self) -> None:
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        self.call_count += 1
        raise AnswerGenerationError(error_category="empty_content")


class _SingleBaselineTimeoutGenerator(AnswerGenerator):
    def __init__(self, envelope: Rag88PrivateFixtureEnvelope) -> None:
        dataset = envelope.dataset
        groups = envelope.groups
        self.cases = {item.case_key: item for item in dataset.cases}
        self.question_to_case = {item.question: item for item in dataset.cases}
        self.group_by_source = {group.source_ids[0]: group for group in groups}
        first_group = groups[0]
        self.timeout_case_key = first_group.combined_case_id
        self.timeout_emitted = False
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.call_count += 1
        group = self.group_by_source[request.context_items[0].source_label]
        combined = self.cases[group.combined_case_id]
        if request.task_instructions is not None:
            return GenerationResult(
                content=json.dumps(
                    {
                        "decision": "keep",
                        "coverage_status": "complete",
                        "incorrect_insufficiency_detected": False,
                        "citation_status": "complete",
                        "revised_answer": "",
                    }
                )
            )
        case = self.question_to_case[request.message]
        if case.case_key == self.timeout_case_key and not self.timeout_emitted:
            self.timeout_emitted = True
            raise AnswerGenerationError(error_category="timeout")
        supported_a = f"{combined.required_facts[0].statement} [2]"
        supported_b = f"{combined.required_facts[1].statement} [5]"
        if case.case_key == group.single_a_case_id:
            return GenerationResult(content=supported_a)
        if case.case_key == group.single_b_case_id:
            return GenerationResult(content=supported_b)
        return GenerationResult(content=f"{supported_a} {supported_b}")


def _stable_inventory() -> Rag86LMInventorySummary:
    return Rag86LMInventorySummary(
        available=True,
        full_inventory_fingerprint="a" * 64,
        target_model_id_fingerprint=_sha256("qwen/qwen3.5-9b"),
        target_entry_fingerprint="b" * 64,
        target_loaded_instance_count=1,
        target_loaded_context_length=12312,
        model_count=1,
        loaded_instance_count=1,
    )
