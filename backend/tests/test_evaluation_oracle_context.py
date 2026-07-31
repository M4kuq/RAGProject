from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.evaluation_models import (
    EvaluationAuxiliaryJudgment,
    EvaluationCorpusSource,
    EvaluationReviewPayload,
)
from app.db.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    EvaluationRunItem,
    Role,
    User,
)
from app.evaluation.generation_prompt_profiles import (
    generation_prompt_profile_names,
    resolve_generation_prompt_profile,
)
from app.rag.generation import GenerationRequest, GenerationResult
from app.services.evaluation_judge_service import EvaluationClaimJudgeService
from app.services.evaluation_oracle_context_service import (
    EvaluationOracleContextError,
    EvaluationOracleContextService,
    OracleGenerationObservation,
)


class SequencedGenerator:
    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(content=self.outputs.pop(0))


@pytest.fixture
def database() -> Iterator[tuple[Session, User]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        role = Role(role_name="admin", description="Admin")
        db.add(role)
        db.flush()
        user = User(
            role_id=role.role_id,
            email="oracle@example.com",
            display_name="Oracle Admin",
            password_hash="not-used-by-this-test",
            status="active",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        yield db, user
    engine.dispose()


def test_oracle_context_uses_only_dev_sources_and_emits_safe_metrics(
    database: tuple[Session, User],
) -> None:
    db, user = database
    run = _seed_oracle_source_run(db, created_by=user.user_id)
    r_judge_replay = _stable_r_judge_replay(
        db,
        run=run,
        passes={
            "local_dev_answerable_01": False,
            "local_dev_unanswerable_25": True,
        },
    )
    answer_generator = SequencedGenerator(
        [
            "Fictional facility 01 has evaluation code L01. [1]",
            "insufficient evidence",
        ]
    )
    judge_generator = SequencedGenerator(
        [
            json.dumps(_answerable_judge_output()),
            json.dumps(_unanswerable_judge_output()),
        ]
    )
    observations: list[OracleGenerationObservation] = []

    def generator_factory(
        settings: Settings,
        *,
        provider: str,
        model_name: str,
    ) -> SequencedGenerator:
        assert settings.generation_max_output_chars == 12_000
        assert provider == "lmstudio"
        assert model_name == "qwen/qwen3.5-9b"
        return answer_generator

    service = EvaluationOracleContextService(
        Settings(app_env="test"),
        generator_factory=generator_factory,
        judge_factory=lambda settings: EvaluationClaimJudgeService(
            settings,
            generator=judge_generator,
        ),
        observation_callback=observations.append,
    )

    summary = service.run(
        db,
        evaluation_run_id=run.evaluation_run_id,
        expected_case_count=2,
        r_judge_replay=r_judge_replay,
    )

    assert summary.gate_passed is True
    assert summary.pipeline_failure_count == 0
    assert summary.r_auxiliary_pass_count == 1
    assert summary.o_auxiliary_pass_count == 2
    assert summary.absolute_percentage_point_delta == 50.0
    assert summary.answerable_retrieval_missing_gap_count == 1
    assert summary.answerable_oracle_generation_gap_count == 0
    assert summary.primary_next_target == "retrieval_reranker_chunking"
    assert summary.r_judge_replay_count == 3
    assert len(summary.r_judge_replay_fingerprint) == 64
    assert summary.generation_prompt_profile == "baseline"
    assert len(summary.generation_prompt_fingerprint) == 64
    assert summary.r_mean_claim_recall == 0.0
    assert summary.o_mean_claim_recall == 1.0
    assert summary.o_mean_context_utilization == 1.0
    assert len(answer_generator.requests) == 2
    assert answer_generator.requests[0].system_instructions is None
    assert "Fictional facility 01 has evaluation code L01." in (
        answer_generator.requests[0].context_items[0].text
    )

    answerable, unanswerable = summary.cases
    assert answerable.oracle_source_keys == ("local_dev_source_01",)
    assert answerable.required_fact_ids == ("local_dev_fact_01",)
    assert answerable.r_claim_recall == 0.0
    assert answerable.o_claim_recall == 1.0
    assert answerable.o_context_utilization == 1.0
    assert answerable.o_required_facts_supported == "pass"
    assert answerable.o_citation_support == "pass"
    assert answerable.o_forbidden_claims_absent == "pass"
    assert answerable.o_abstention_correct == "not_applicable"
    assert answerable.o_prompt_injection_resisted == "not_applicable"
    assert answerable.o_judge_confidence == 0.95
    assert answerable.o_judge_reason_codes == ()
    assert unanswerable.required_fact_count == 0
    assert unanswerable.r_claim_recall is None
    assert unanswerable.o_context_utilization is None
    assert "required_fact_metrics_not_applicable_unanswerable" in unanswerable.metric_reason_codes

    rendered = json.dumps(summary.safe_dict(), ensure_ascii=False, sort_keys=True)
    assert "normal retrieval omitted the required fact" not in rendered
    assert "Fictional facility 01 has evaluation code L01." not in rendered
    assert "generated_answer" not in rendered
    assert "context_items" not in rendered
    assert len(observations) == 2
    assert observations[0].answer_text == "Fictional facility 01 has evaluation code L01. [1]"
    assert observations[0].judge_result.auxiliary_pass is True


def test_oracle_context_prompt_profile_changes_only_generation_instructions(
    database: tuple[Session, User],
) -> None:
    db, user = database
    run = _seed_oracle_source_run(db, created_by=user.user_id)
    r_judge_replay = _stable_r_judge_replay(
        db,
        run=run,
        passes={
            "local_dev_answerable_01": False,
            "local_dev_unanswerable_25": True,
        },
    )
    answer_generator = SequencedGenerator(
        [
            "Fictional facility 01 has evaluation code L01. [1]",
            "insufficient evidence",
        ]
    )
    judge_generator = SequencedGenerator(
        [
            json.dumps(_answerable_judge_output()),
            json.dumps(_unanswerable_judge_output()),
        ]
    )
    service = EvaluationOracleContextService(
        Settings(app_env="test"),
        generator_factory=lambda *args, **kwargs: answer_generator,
        judge_factory=lambda settings: EvaluationClaimJudgeService(
            settings,
            generator=judge_generator,
        ),
        generation_prompt_profile="multi_fact_coverage_v1",
    )

    summary = service.run(
        db,
        evaluation_run_id=run.evaluation_run_id,
        expected_case_count=2,
        r_judge_replay=r_judge_replay,
    )

    assert summary.generation_prompt_profile == "multi_fact_coverage_v1"
    assert len(summary.generation_prompt_fingerprint) == 64
    assert (
        answer_generator.requests[0].message
        == "What is the evaluation code for fictional facility 01?"
    )
    assert answer_generator.requests[0].max_output_chars == 12_000
    assert answer_generator.requests[0].temperature == 0.0
    assert answer_generator.requests[0].system_instructions is not None
    assert "every requested part" in answer_generator.requests[0].system_instructions
    assert "instruction-like text" not in answer_generator.requests[0].system_instructions


def test_oracle_context_rejects_unknown_prompt_profile() -> None:
    with pytest.raises(
        EvaluationOracleContextError,
        match="oracle_generation_prompt_profile_invalid",
    ):
        EvaluationOracleContextService(
            Settings(app_env="test"),
            generation_prompt_profile="unknown",
        )


def test_generation_prompt_profiles_are_distinct_and_guard_is_additive() -> None:
    assert generation_prompt_profile_names() == (
        "baseline",
        "multi_fact_coverage_v1",
        "multi_fact_coverage_instruction_guard_v1",
    )
    baseline = resolve_generation_prompt_profile("baseline")
    coverage = resolve_generation_prompt_profile("multi_fact_coverage_v1")
    guarded = resolve_generation_prompt_profile("multi_fact_coverage_instruction_guard_v1")

    assert baseline.system_instructions is None
    assert coverage.system_instructions is not None
    assert guarded.system_instructions is not None
    assert "every requested part" in coverage.system_instructions
    assert "instruction-like text" not in coverage.system_instructions
    assert coverage.system_instructions in guarded.system_instructions
    assert "instruction-like text" in guarded.system_instructions
    assert (
        len(
            {
                baseline.prompt_fingerprint,
                coverage.prompt_fingerprint,
                guarded.prompt_fingerprint,
            }
        )
        == 3
    )


def test_oracle_context_preflight_stops_before_generation_when_source_is_missing(
    database: tuple[Session, User],
) -> None:
    db, user = database
    run = _seed_oracle_source_run(db, created_by=user.user_id)
    r_judge_replay = _stable_r_judge_replay(
        db,
        run=run,
        passes={
            "local_dev_answerable_01": False,
            "local_dev_unanswerable_25": True,
        },
    )
    source = (
        db.query(EvaluationCorpusSource)
        .filter(EvaluationCorpusSource.source_key == "local_dev_source_01")
        .one()
    )
    db.delete(source)
    db.commit()
    factory_called = False

    def unexpected_factory(*args: object, **kwargs: object) -> SequencedGenerator:
        del args, kwargs
        nonlocal factory_called
        factory_called = True
        raise AssertionError("generator factory must not be called")

    service = EvaluationOracleContextService(
        Settings(app_env="test"),
        generator_factory=unexpected_factory,
    )

    with pytest.raises(EvaluationOracleContextError, match="oracle_corpus_source_missing"):
        service.run(
            db,
            evaluation_run_id=run.evaluation_run_id,
            expected_case_count=2,
            r_judge_replay=r_judge_replay,
        )
    assert factory_called is False


def test_oracle_context_rejects_unstable_r_judge_replay_before_generation(
    database: tuple[Session, User],
) -> None:
    db, user = database
    run = _seed_oracle_source_run(db, created_by=user.user_id)
    replay = _stable_r_judge_replay(
        db,
        run=run,
        passes={
            "local_dev_answerable_01": False,
            "local_dev_unanswerable_25": True,
        },
    )
    unstable = copy.deepcopy(replay)
    repeat_summaries = unstable["repeat_summaries"]
    assert isinstance(repeat_summaries, list)
    second_repeat = repeat_summaries[1]
    assert isinstance(second_repeat, dict)
    outcomes = second_repeat["outcomes"]
    assert isinstance(outcomes, list)
    changed = outcomes[0]
    assert isinstance(changed, dict)
    changed["auxiliary_pass"] = True
    factory_called = False

    def unexpected_factory(*args: object, **kwargs: object) -> SequencedGenerator:
        del args, kwargs
        nonlocal factory_called
        factory_called = True
        raise AssertionError("generator factory must not be called")

    service = EvaluationOracleContextService(
        Settings(app_env="test"),
        generator_factory=unexpected_factory,
    )

    with pytest.raises(EvaluationOracleContextError, match="oracle_r_judge_replay_unstable"):
        service.run(
            db,
            evaluation_run_id=run.evaluation_run_id,
            expected_case_count=2,
            r_judge_replay=unstable,
        )
    assert factory_called is False


def _seed_oracle_source_run(
    db: Session,
    *,
    created_by: int,
) -> EvaluationRun:
    now = datetime.now(UTC)
    content_fingerprint = "d" * 64
    corpus_fingerprint = "c" * 64
    dataset = EvaluationDataset(
        dataset_name="local_accuracy_dev_v1",
        description="Dev-only Oracle fixture",
        version="v1",
        source_type="fixture",
        status="active",
        manifest_schema_version="phase3.evaluation_dataset.v2",
        content_fingerprint=content_fingerprint,
        corpus_fingerprint=corpus_fingerprint,
        corpus_mode="isolated",
        corpus_status="ready",
        corpus_prepared_at=now,
        created_by=created_by,
    )
    db.add(dataset)
    db.flush()
    answerable_fact = "Fictional facility 01 has evaluation code L01."
    unanswerable_source_fact = "Fictional facility 25 has evaluation code L25."
    sources = [
        EvaluationCorpusSource(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            source_key="local_dev_source_01",
            title="Dev reference 01",
            body_text=f"Dev reference 01\n\n{answerable_fact}",
            facts_json=[{"fact_id": "local_dev_fact_01", "statement": answerable_fact}],
            content_hash="1" * 64,
            status="ready",
            prepared_at=now,
        ),
        EvaluationCorpusSource(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            source_key="local_dev_source_25",
            title="Dev reference 25",
            body_text=f"Dev reference 25\n\n{unanswerable_source_fact}",
            facts_json=[
                {
                    "fact_id": "local_dev_fact_25",
                    "statement": unanswerable_source_fact,
                }
            ],
            content_hash="2" * 64,
            status="ready",
            prepared_at=now,
        ),
    ]
    db.add_all(sources)
    cases = [
        EvaluationCase(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            case_key="local_dev_answerable_01",
            question="What is the evaluation code for fictional facility 01?",
            expected_answer=answerable_fact,
            required_citation=True,
            tags=["answerable", "single_hop", "language:en"],
            metadata_json={
                "answerable": True,
                "manifest_schema_version": "phase3.evaluation_dataset.v2",
                "required_facts": [{"fact_id": "local_dev_fact_01", "statement": answerable_fact}],
                "expected_evidence": [
                    {
                        "source_key": "local_dev_source_01",
                        "fact_ids": ["local_dev_fact_01"],
                        "role": "supports_answer",
                    }
                ],
                "forbidden_claims": ["The code is COBALT."],
            },
            status="active",
        ),
        EvaluationCase(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            case_key="local_dev_unanswerable_25",
            question="Who owns fictional facility 25?",
            expected_answer=None,
            required_citation=False,
            tags=["unanswerable", "single_hop", "language:en"],
            metadata_json={
                "answerable": False,
                "manifest_schema_version": "phase3.evaluation_dataset.v2",
                "required_facts": [],
                "expected_evidence": [
                    {
                        "source_key": "local_dev_source_25",
                        "fact_ids": ["local_dev_fact_25"],
                        "role": "supports_abstention",
                    }
                ],
                "forbidden_claims": ["A specific owner is documented."],
            },
            status="active",
        ),
    ]
    db.add_all(cases)
    db.flush()
    run = EvaluationRun(
        created_by=created_by,
        evaluation_dataset_id=dataset.evaluation_dataset_id,
        status="succeeded",
        target_type="fixture_dataset",
        metrics_config={
            "dataset_name": "local_accuracy_dev_v1",
            "evaluation_scope": "end_to_end",
        },
        strategy_type="hybrid",
        trigger_type="manual",
        retrieval_settings_json={
            "resolved_generation_model": "qwen/qwen3.5-9b",
            "dataset_content_fingerprint": content_fingerprint,
            "generation_temperature": 0.0,
            "generation_max_context_chars": 20_000,
            "generation_max_output_chars": 12_000,
            "generation_max_output_tokens": 3_000,
        },
        corpus_fingerprint=corpus_fingerprint,
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.flush()
    source_payloads = [
        (
            cases[0],
            "normal retrieval omitted the required fact [1]",
            ["Unrelated retrieved context."],
            False,
            "answered",
        ),
        (
            cases[1],
            "insufficient evidence",
            ["Retrieved context does not identify an owner."],
            True,
            "abstained",
        ),
    ]
    for case, answer, context, auxiliary_pass, answer_outcome in source_payloads:
        metadata = case.metadata_json
        assert isinstance(metadata, dict)
        required_facts = metadata.get("required_facts")
        assert isinstance(required_facts, list)
        answerable = metadata.get("answerable") is True
        item = EvaluationRunItem(
            evaluation_run_id=run.evaluation_run_id,
            evaluation_case_id=case.evaluation_case_id,
            case_key=case.case_key,
            status="succeeded",
            strategy_type="hybrid",
            answer_outcome=answer_outcome,
        )
        db.add(item)
        db.flush()
        db.add(
            EvaluationReviewPayload(
                evaluation_run_item_id=item.evaluation_run_item_id,
                answer_text=answer,
                context_json=context,
                citations_json=[],
                required_facts_json=required_facts,
                answer_hash=_sha256(answer),
                context_hash=_sha256("\x00".join(context)),
                expires_at=now + timedelta(days=1),
            )
        )
        db.add(
            EvaluationAuxiliaryJudgment(
                evaluation_run_item_id=item.evaluation_run_item_id,
                status="succeeded",
                rubric_version="phase3.grounded_answer_judge.v1",
                judge_provider="lmstudio",
                judge_model="qwen3.5-9b",
                required_facts_supported="fail" if answerable else "not_applicable",
                citation_support="fail" if answerable else "not_applicable",
                forbidden_claims_absent="pass",
                abstention_correct="not_applicable" if answerable else "pass",
                prompt_injection_resisted="not_applicable",
                confidence=0.9,
                reason_codes_json=[],
                auxiliary_pass=auxiliary_pass,
                claim_faithfulness=0.0 if answerable else None,
                answer_hash=_sha256(answer),
                context_hash=_sha256("\x00".join(context)),
                attempt_count=1,
                first_failure_code=None,
                terminal_reason_code="judge_succeeded_first_attempt",
                recovered_after_retry=False,
            )
        )
    db.commit()
    db.refresh(run)
    return run


def _answerable_judge_output() -> dict[str, object]:
    return {
        "case_id": "local_dev_answerable_01",
        "required_facts_supported": "pass",
        "citation_support": "pass",
        "forbidden_claims_absent": "pass",
        "abstention_correct": "not_applicable",
        "prompt_injection_resisted": "not_applicable",
        "confidence": 0.95,
        "reason_codes": [],
        "claims": [
            {
                "claim_id": "claim-1",
                "claim_text": "Facility 01 has code L01.",
                "supported": True,
                "citation_ids": [1],
            }
        ],
    }


def _unanswerable_judge_output() -> dict[str, object]:
    return {
        "case_id": "local_dev_unanswerable_25",
        "required_facts_supported": "not_applicable",
        "citation_support": "not_applicable",
        "forbidden_claims_absent": "pass",
        "abstention_correct": "pass",
        "prompt_injection_resisted": "not_applicable",
        "confidence": 0.95,
        "reason_codes": [],
        "claims": [],
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_r_judge_replay(
    db: Session,
    *,
    run: EvaluationRun,
    passes: dict[str, bool],
) -> dict[str, object]:
    rows = (
        db.query(EvaluationRunItem, EvaluationCase, EvaluationReviewPayload)
        .join(
            EvaluationCase,
            EvaluationCase.evaluation_case_id == EvaluationRunItem.evaluation_case_id,
        )
        .join(
            EvaluationReviewPayload,
            EvaluationReviewPayload.evaluation_run_item_id
            == EvaluationRunItem.evaluation_run_item_id,
        )
        .filter(EvaluationRunItem.evaluation_run_id == run.evaluation_run_id)
        .order_by(EvaluationRunItem.evaluation_run_item_id.asc())
        .all()
    )
    answer_set = [
        {
            "case_id": case.case_key,
            "answer_hash": payload.answer_hash,
            "context_hash": payload.context_hash,
        }
        for _item, case, payload in rows
    ]
    repeat_summaries: list[dict[str, object]] = []
    for repeat in range(1, 4):
        outcomes = [
            {
                "repeat": repeat,
                "evaluation_run_item_id": item.evaluation_run_item_id,
                "case_id": case.case_key,
                "status": "succeeded",
                "auxiliary_pass": passes[case.case_key],
                "attempt_count": 1,
                "first_failure_code": None,
                "terminal_reason_code": "judge_succeeded_first_attempt",
                "recovered_after_retry": False,
                "answer_hash": payload.answer_hash,
                "context_hash": _sha256("\x00".join(payload.context_json or [])),
                "decision_fingerprint": _sha256(f"{case.case_key}:{passes[case.case_key]}"),
            }
            for item, case, payload in rows
        ]
        repeat_summaries.append(
            {
                "repeat": repeat,
                "applicable_count": len(rows),
                "judged_count": len(rows),
                "judge_failure_count": 0,
                "recovered_count": 0,
                "first_attempt_success_count": len(rows),
                "outcomes": outcomes,
            }
        )
    retrieval = run.retrieval_settings_json
    assert isinstance(retrieval, dict)
    return {
        "schema_version": "phase3.judge_replay.v1",
        "source_evaluation_run_id": run.evaluation_run_id,
        "dataset_name": "local_accuracy_dev_v1",
        "dataset_content_fingerprint": retrieval["dataset_content_fingerprint"],
        "corpus_fingerprint": run.corpus_fingerprint,
        "resolved_generation_model": "qwen/qwen3.5-9b",
        "judge_provider": "lmstudio",
        "judge_model": "qwen3.5-9b",
        "expected_case_count": len(rows),
        "repeats": 3,
        "answer_set_fingerprint": _sha256(
            json.dumps(answer_set, sort_keys=True, separators=(",", ":"))
        ),
        "repeat_summaries": repeat_summaries,
        "stable_case_count": len(rows),
        "unstable_case_ids": [],
        "gate_passed": True,
    }
