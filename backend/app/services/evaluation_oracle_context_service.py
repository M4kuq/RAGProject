from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.evaluation_models import (
    EvaluationCorpusSource,
    EvaluationReviewPayload,
)
from app.db.models import EvaluationCase, EvaluationDataset, EvaluationRun, EvaluationRunItem
from app.evaluation.rag_service import generate_evaluation_answer
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    create_answer_generator,
)
from app.services.evaluation_judge_service import (
    EvaluationClaimJudgeError,
    EvaluationClaimJudgeService,
)
from app.services.rag_service import (
    _is_insufficient_evidence_answer,
    _validate_generation_output_safety,
)

_ALLOWED_DATASET = "local_accuracy_dev_v1"
_EXPECTED_GENERATION_MODEL = "qwen/qwen3.5-9b"
_EXPECTED_SCOPE = "end_to_end"


class EvaluationOracleContextError(RuntimeError):
    pass


@dataclass(frozen=True)
class OracleContextCaseOutcome:
    case_id: str
    question_hash: str
    answerable: bool
    tags: tuple[str, ...]
    required_fact_ids: tuple[str, ...]
    oracle_source_keys: tuple[str, ...]
    required_fact_count: int
    r_retrieved_required_fact_count: int | None
    r_used_required_fact_count: int | None
    r_claim_recall: float | None
    r_context_utilization: float | None
    o_retrieved_required_fact_count: int | None
    o_used_required_fact_count: int | None
    o_claim_recall: float | None
    o_context_utilization: float | None
    r_auxiliary_pass: bool
    o_auxiliary_pass: bool | None
    r_answer_hash: str
    r_context_hash: str
    o_answer_hash: str | None
    o_context_hash: str
    o_answer_outcome: Literal["answered", "abstained"] | None
    o_judge_attempt_count: int
    o_judge_first_failure_code: str | None
    o_judge_terminal_reason_code: str
    o_judge_recovered_after_retry: bool
    metric_reason_codes: tuple[str, ...]
    pipeline_failure_code: str | None


@dataclass(frozen=True)
class OracleContextDiagnosticSummary:
    schema_version: Literal["phase3.oracle_context_diagnostic.v1"]
    source_evaluation_run_id: int
    dataset_name: str
    dataset_content_fingerprint: str
    corpus_fingerprint: str
    case_set_fingerprint: str
    generation_config_fingerprint: str
    r_judge_replay_fingerprint: str
    r_judge_replay_count: int
    resolved_generation_model: str
    generation_temperature: float
    generation_max_context_chars: int
    generation_max_output_chars: int
    generation_max_output_tokens: int | None
    expected_case_count: int
    comparable_case_count: int
    comparability_status: Literal["comparable"]
    r_auxiliary_pass_count: int
    o_auxiliary_pass_count: int
    r_auxiliary_pass_rate: float
    o_auxiliary_pass_rate: float
    absolute_percentage_point_delta: float
    answerable_retrieval_missing_gap_count: int
    answerable_context_utilization_or_noise_gap_count: int
    answerable_oracle_generation_gap_count: int
    unanswerable_r_pass_count: int
    unanswerable_o_pass_count: int
    r_mean_claim_recall: float | None
    o_mean_claim_recall: float | None
    r_mean_context_utilization: float | None
    o_mean_context_utilization: float | None
    primary_next_target: str
    pipeline_failure_count: int
    gate_passed: bool
    cases: tuple[OracleContextCaseOutcome, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class _OracleInput:
    item: EvaluationRunItem
    case: EvaluationCase
    payload: EvaluationReviewPayload
    answerable: bool
    tags: tuple[str, ...]
    required_facts: tuple[dict[str, object], ...]
    forbidden_claims: tuple[str, ...]
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class _OracleGeneration:
    answer_text: str
    answer_outcome: Literal["answered", "abstained"]
    citations: tuple[dict[str, object], ...]
    context: tuple[str, ...]


@dataclass(frozen=True)
class _StableRJudgment:
    auxiliary_pass: bool
    answer_hash: str
    context_hash: str
    decision_fingerprint: str


@dataclass(frozen=True)
class _StableRReplay:
    replay_count: int
    replay_fingerprint: str
    judgments: dict[str, _StableRJudgment]


class EvaluationOracleContextService:
    def __init__(
        self,
        settings: Settings,
        *,
        generator_factory: Callable[..., AnswerGenerator] | None = None,
        judge_factory: Callable[[Settings], EvaluationClaimJudgeService] | None = None,
    ) -> None:
        self.settings = settings
        self.generator_factory = generator_factory or create_answer_generator
        self.judge_factory = judge_factory or EvaluationClaimJudgeService

    def run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        expected_case_count: int,
        r_judge_replay: dict[str, object],
    ) -> OracleContextDiagnosticSummary:
        if expected_case_count < 1:
            raise EvaluationOracleContextError("oracle_expected_case_count_invalid")
        run = self._require_source_run(db, evaluation_run_id=evaluation_run_id)
        config = run.metrics_config if isinstance(run.metrics_config, dict) else {}
        retrieval = (
            run.retrieval_settings_json if isinstance(run.retrieval_settings_json, dict) else {}
        )
        dataset = self._require_dataset(db, run)
        inputs = self._load_inputs(
            db,
            run=run,
            expected_case_count=expected_case_count,
        )
        stable_r_replay = _validate_r_judge_replay(
            r_judge_replay,
            run=run,
            inputs=inputs,
            expected_case_count=expected_case_count,
        )
        sources = self._load_sources(db, dataset=dataset, inputs=inputs)
        generation_settings = _generation_settings(self.settings, retrieval)
        generator = self.generator_factory(
            generation_settings,
            provider="lmstudio",
            model_name=_EXPECTED_GENERATION_MODEL,
        )
        judge = self.judge_factory(generation_settings)

        outcomes = tuple(
            self._run_case(
                oracle_input,
                sources=sources,
                settings=generation_settings,
                generator=generator,
                judge=judge,
                r_judgment=stable_r_replay.judgments[oracle_input.case.case_key],
            )
            for oracle_input in inputs
        )
        case_set_fingerprint = _case_set_fingerprint(inputs)
        generation_config_fingerprint = _generation_config_fingerprint(generation_settings)
        pipeline_failure_count = sum(
            outcome.pipeline_failure_code is not None for outcome in outcomes
        )
        r_pass_count = sum(outcome.r_auxiliary_pass for outcome in outcomes)
        o_pass_count = sum(outcome.o_auxiliary_pass is True for outcome in outcomes)
        retrieval_missing = sum(
            outcome.answerable
            and not outcome.r_auxiliary_pass
            and outcome.o_auxiliary_pass is True
            and outcome.r_claim_recall is not None
            and outcome.r_claim_recall < 1.0
            for outcome in outcomes
        )
        utilization_or_noise = sum(
            outcome.answerable
            and not outcome.r_auxiliary_pass
            and outcome.o_auxiliary_pass is True
            and outcome.r_claim_recall == 1.0
            for outcome in outcomes
        )
        oracle_generation_gap = sum(
            outcome.answerable and outcome.o_auxiliary_pass is False for outcome in outcomes
        )
        unanswerable = tuple(outcome for outcome in outcomes if not outcome.answerable)

        r_claim_recall = _mean(outcome.r_claim_recall for outcome in outcomes if outcome.answerable)
        o_claim_recall = _mean(outcome.o_claim_recall for outcome in outcomes if outcome.answerable)
        r_context_utilization = _mean(
            outcome.r_context_utilization for outcome in outcomes if outcome.answerable
        )
        o_context_utilization = _mean(
            outcome.o_context_utilization for outcome in outcomes if outcome.answerable
        )
        primary_next_target = _primary_next_target(
            retrieval_missing=retrieval_missing,
            utilization_or_noise=utilization_or_noise,
            oracle_generation_gap=oracle_generation_gap,
        )
        dataset_fingerprint = _required_hash(
            retrieval.get("dataset_content_fingerprint"),
            "oracle_dataset_fingerprint_missing",
        )
        corpus_fingerprint = _required_hash(
            run.corpus_fingerprint,
            "oracle_corpus_fingerprint_missing",
        )
        if config.get("dataset_name") != _ALLOWED_DATASET:
            raise EvaluationOracleContextError("oracle_dataset_config_mismatch")

        return OracleContextDiagnosticSummary(
            schema_version="phase3.oracle_context_diagnostic.v1",
            source_evaluation_run_id=run.evaluation_run_id,
            dataset_name=_ALLOWED_DATASET,
            dataset_content_fingerprint=dataset_fingerprint,
            corpus_fingerprint=corpus_fingerprint,
            case_set_fingerprint=case_set_fingerprint,
            generation_config_fingerprint=generation_config_fingerprint,
            r_judge_replay_fingerprint=stable_r_replay.replay_fingerprint,
            r_judge_replay_count=stable_r_replay.replay_count,
            resolved_generation_model=_EXPECTED_GENERATION_MODEL,
            generation_temperature=0.0,
            generation_max_context_chars=generation_settings.generation_max_context_chars,
            generation_max_output_chars=generation_settings.generation_max_output_chars,
            generation_max_output_tokens=generation_settings.generation_max_output_tokens,
            expected_case_count=expected_case_count,
            comparable_case_count=len(outcomes) - pipeline_failure_count,
            comparability_status="comparable",
            r_auxiliary_pass_count=r_pass_count,
            o_auxiliary_pass_count=o_pass_count,
            r_auxiliary_pass_rate=_rate(r_pass_count, expected_case_count),
            o_auxiliary_pass_rate=_rate(o_pass_count, expected_case_count),
            absolute_percentage_point_delta=round(
                100.0
                * (
                    _rate(o_pass_count, expected_case_count)
                    - _rate(r_pass_count, expected_case_count)
                ),
                3,
            ),
            answerable_retrieval_missing_gap_count=retrieval_missing,
            answerable_context_utilization_or_noise_gap_count=utilization_or_noise,
            answerable_oracle_generation_gap_count=oracle_generation_gap,
            unanswerable_r_pass_count=sum(outcome.r_auxiliary_pass for outcome in unanswerable),
            unanswerable_o_pass_count=sum(
                outcome.o_auxiliary_pass is True for outcome in unanswerable
            ),
            r_mean_claim_recall=r_claim_recall,
            o_mean_claim_recall=o_claim_recall,
            r_mean_context_utilization=r_context_utilization,
            o_mean_context_utilization=o_context_utilization,
            primary_next_target=primary_next_target,
            pipeline_failure_count=pipeline_failure_count,
            gate_passed=pipeline_failure_count == 0 and len(outcomes) == expected_case_count,
            cases=outcomes,
        )

    def _require_source_run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationRun:
        run = db.get(EvaluationRun, evaluation_run_id)
        if run is None:
            raise EvaluationOracleContextError("oracle_source_run_not_found")
        config = run.metrics_config if isinstance(run.metrics_config, dict) else {}
        retrieval = (
            run.retrieval_settings_json if isinstance(run.retrieval_settings_json, dict) else {}
        )
        if config.get("dataset_name") != _ALLOWED_DATASET:
            raise EvaluationOracleContextError("oracle_dataset_not_allowed")
        if config.get("evaluation_scope") != _EXPECTED_SCOPE:
            raise EvaluationOracleContextError("oracle_scope_not_allowed")
        if run.trigger_type != "manual" or run.status != "succeeded":
            raise EvaluationOracleContextError("oracle_source_run_not_terminal_manual")
        if retrieval.get("resolved_generation_model") != _EXPECTED_GENERATION_MODEL:
            raise EvaluationOracleContextError("oracle_generation_model_mismatch")
        if retrieval.get("generation_temperature") != 0.0:
            raise EvaluationOracleContextError("oracle_generation_temperature_mismatch")
        _required_hash(
            retrieval.get("dataset_content_fingerprint"),
            "oracle_dataset_fingerprint_missing",
        )
        _required_hash(run.corpus_fingerprint, "oracle_corpus_fingerprint_missing")
        return run

    def _require_dataset(
        self,
        db: Session,
        run: EvaluationRun,
    ) -> EvaluationDataset:
        if run.evaluation_dataset_id is None:
            raise EvaluationOracleContextError("oracle_dataset_reference_missing")
        dataset = db.get(EvaluationDataset, run.evaluation_dataset_id)
        if dataset is None or dataset.dataset_name != _ALLOWED_DATASET:
            raise EvaluationOracleContextError("oracle_dataset_reference_invalid")
        retrieval = cast(dict[str, object], run.retrieval_settings_json)
        if (
            dataset.status != "active"
            or dataset.corpus_mode != "isolated"
            or dataset.corpus_status != "ready"
            or dataset.content_fingerprint != retrieval.get("dataset_content_fingerprint")
            or dataset.corpus_fingerprint != run.corpus_fingerprint
        ):
            raise EvaluationOracleContextError("oracle_dataset_state_mismatch")
        return dataset

    def _load_inputs(
        self,
        db: Session,
        *,
        run: EvaluationRun,
        expected_case_count: int,
    ) -> tuple[_OracleInput, ...]:
        items = tuple(
            db.scalars(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.evaluation_run_id == run.evaluation_run_id)
                .order_by(EvaluationRunItem.evaluation_run_item_id.asc())
            ).all()
        )
        if len(items) != expected_case_count:
            raise EvaluationOracleContextError("oracle_case_count_mismatch")
        item_ids = [item.evaluation_run_item_id for item in items]
        payloads = {
            payload.evaluation_run_item_id: payload
            for payload in db.scalars(
                select(EvaluationReviewPayload).where(
                    EvaluationReviewPayload.evaluation_run_item_id.in_(item_ids)
                )
            ).all()
        }
        now = datetime.now(UTC)
        result: list[_OracleInput] = []
        seen_case_ids: set[int] = set()
        for item in items:
            if item.status != "succeeded" or item.evaluation_case_id is None:
                raise EvaluationOracleContextError("oracle_source_item_invalid")
            if item.evaluation_case_id in seen_case_ids:
                raise EvaluationOracleContextError("oracle_duplicate_case")
            seen_case_ids.add(item.evaluation_case_id)
            case = db.get(EvaluationCase, item.evaluation_case_id)
            payload = payloads.get(item.evaluation_run_item_id)
            if case is None or payload is None:
                raise EvaluationOracleContextError("oracle_source_evidence_missing")
            if (
                payload.purged_at is not None
                or _aware_utc(payload.expires_at) <= now
                or payload.answer_text is None
                or payload.context_json is None
            ):
                raise EvaluationOracleContextError("oracle_source_evidence_unavailable")
            metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
            answerable = metadata.get("answerable")
            if not isinstance(answerable, bool):
                raise EvaluationOracleContextError("oracle_answerability_missing")
            required_facts = _dict_tuple(metadata.get("required_facts"))
            source_keys = _expected_source_keys(metadata.get("expected_evidence"))
            if not source_keys:
                raise EvaluationOracleContextError("oracle_expected_sources_missing")
            if answerable and not required_facts:
                raise EvaluationOracleContextError("oracle_required_facts_missing")
            if not answerable and required_facts:
                raise EvaluationOracleContextError("oracle_unanswerable_facts_invalid")
            if _sha256(payload.answer_text) != payload.answer_hash or payload.context_hash not in {
                _sha256("\x00".join(_string_tuple(payload.context_json))),
                _sha256("\\x00".join(_string_tuple(payload.context_json))),
            }:
                raise EvaluationOracleContextError("oracle_source_payload_hash_mismatch")
            result.append(
                _OracleInput(
                    item=item,
                    case=case,
                    payload=payload,
                    answerable=answerable,
                    tags=_string_tuple(case.tags),
                    required_facts=required_facts,
                    forbidden_claims=_optional_string_tuple(metadata.get("forbidden_claims")),
                    source_keys=source_keys,
                )
            )
        return tuple(result)

    def _load_sources(
        self,
        db: Session,
        *,
        dataset: EvaluationDataset,
        inputs: tuple[_OracleInput, ...],
    ) -> dict[str, EvaluationCorpusSource]:
        required_keys = {source_key for item in inputs for source_key in item.source_keys}
        rows = tuple(
            db.scalars(
                select(EvaluationCorpusSource).where(
                    EvaluationCorpusSource.evaluation_dataset_id == dataset.evaluation_dataset_id,
                    EvaluationCorpusSource.source_key.in_(sorted(required_keys)),
                )
            ).all()
        )
        sources = {row.source_key: row for row in rows}
        if set(sources) != required_keys:
            raise EvaluationOracleContextError("oracle_corpus_source_missing")
        for oracle_input in inputs:
            expected_fact_ids = set(_required_fact_ids(oracle_input.required_facts))
            available_fact_ids = {
                fact_id
                for source_key in oracle_input.source_keys
                for fact_id in _required_fact_ids(_dict_tuple(sources[source_key].facts_json))
            }
            if any(
                sources[source_key].status != "ready" for source_key in oracle_input.source_keys
            ) or not expected_fact_ids.issubset(available_fact_ids):
                raise EvaluationOracleContextError("oracle_corpus_source_not_ready")
        return sources

    def _run_case(
        self,
        oracle_input: _OracleInput,
        *,
        sources: dict[str, EvaluationCorpusSource],
        settings: Settings,
        generator: AnswerGenerator,
        judge: EvaluationClaimJudgeService,
        r_judgment: _StableRJudgment,
    ) -> OracleContextCaseOutcome:
        selected_sources = tuple(sources[key] for key in oracle_input.source_keys)
        context_items, citation_sources = _oracle_context_items(
            selected_sources,
            max_context_chars=settings.generation_max_context_chars,
        )
        context = tuple(item.text for item in context_items)
        o_context_hash = _sha256("\x00".join(context))
        r_context = _string_tuple(oracle_input.payload.context_json)
        required_fact_count = len(oracle_input.required_facts)
        r_retrieved = _fact_count(r_context, oracle_input.required_facts)
        r_used = _used_fact_count(
            oracle_input.payload.answer_text or "",
            r_context,
            oracle_input.required_facts,
        )
        o_retrieved = _fact_count(context, oracle_input.required_facts)
        metric_reason_codes: list[str] = []
        if not oracle_input.answerable:
            r_retrieved_value: int | None = None
            r_used_value: int | None = None
            r_claim_recall = None
            r_context_utilization = None
            o_retrieved_value: int | None = None
            metric_reason_codes.append("required_fact_metrics_not_applicable_unanswerable")
        else:
            r_retrieved_value = r_retrieved
            r_used_value = r_used
            r_claim_recall = _ratio(r_retrieved, required_fact_count)
            r_context_utilization = _ratio(r_used, r_retrieved) if r_retrieved > 0 else None
            o_retrieved_value = o_retrieved
            metric_reason_codes.append("fact_detection_deterministic_statement_match")
            if r_retrieved == 0:
                metric_reason_codes.append("r_context_utilization_denominator_zero")

        generation: _OracleGeneration | None = None
        judged = None
        pipeline_failure_code: str | None = None
        judge_attempt_count: int
        judge_first_failure_code: str | None
        judge_terminal_reason_code: str
        judge_recovered_after_retry: bool
        try:
            generation = _generate_oracle_answer(
                settings,
                generator=generator,
                question=oracle_input.case.question,
                context_items=context_items,
                citation_sources=citation_sources,
            )
            judged = judge.judge(
                case_id=oracle_input.case.case_key,
                answerable=oracle_input.answerable,
                required_citation=oracle_input.case.required_citation,
                tags=list(oracle_input.tags),
                answer_outcome=generation.answer_outcome,
                answer_text=generation.answer_text,
                citations=list(generation.citations),
                context=list(generation.context),
                required_facts=list(oracle_input.required_facts),
                forbidden_claims=list(oracle_input.forbidden_claims),
            )
        except EvaluationClaimJudgeError as exc:
            pipeline_failure_code = exc.terminal_reason_code
            judge_attempt_count = exc.attempt_count
            judge_first_failure_code = exc.first_failure_code
            judge_terminal_reason_code = exc.terminal_reason_code
            judge_recovered_after_retry = False
        except AnswerGenerationError as exc:
            pipeline_failure_code = f"oracle_generation_{exc.error_category}"
            judge_attempt_count = 0
            judge_first_failure_code = None
            judge_terminal_reason_code = "oracle_judge_not_run"
            judge_recovered_after_retry = False
        except CitationBuildError as exc:
            pipeline_failure_code = f"oracle_{exc.detail_code}"
            judge_attempt_count = 0
            judge_first_failure_code = None
            judge_terminal_reason_code = "oracle_judge_not_run"
            judge_recovered_after_retry = False
        except Exception:
            pipeline_failure_code = "oracle_unexpected_error"
            judge_attempt_count = 0
            judge_first_failure_code = None
            judge_terminal_reason_code = "oracle_judge_not_run"
            judge_recovered_after_retry = False
        else:
            judge_attempt_count = judged.attempt_count
            judge_first_failure_code = judged.first_failure_code
            judge_terminal_reason_code = judged.terminal_reason_code
            judge_recovered_after_retry = judged.recovered_after_retry

        o_used = (
            _used_fact_count(
                generation.answer_text,
                generation.context,
                oracle_input.required_facts,
            )
            if generation is not None
            else 0
        )
        if oracle_input.answerable:
            o_used_value: int | None = o_used
            o_claim_recall = _ratio(o_retrieved, required_fact_count)
            o_context_utilization = _ratio(o_used, o_retrieved) if o_retrieved > 0 else None
            if o_retrieved == 0:
                metric_reason_codes.append("o_context_utilization_denominator_zero")
        else:
            o_used_value = None
            o_claim_recall = None
            o_context_utilization = None

        return OracleContextCaseOutcome(
            case_id=oracle_input.case.case_key,
            question_hash=_sha256(oracle_input.case.question),
            answerable=oracle_input.answerable,
            tags=oracle_input.tags,
            required_fact_ids=_required_fact_ids(oracle_input.required_facts),
            oracle_source_keys=oracle_input.source_keys,
            required_fact_count=required_fact_count,
            r_retrieved_required_fact_count=r_retrieved_value,
            r_used_required_fact_count=r_used_value,
            r_claim_recall=r_claim_recall,
            r_context_utilization=r_context_utilization,
            o_retrieved_required_fact_count=o_retrieved_value,
            o_used_required_fact_count=o_used_value,
            o_claim_recall=o_claim_recall,
            o_context_utilization=o_context_utilization,
            r_auxiliary_pass=r_judgment.auxiliary_pass,
            o_auxiliary_pass=judged.auxiliary_pass if judged is not None else None,
            r_answer_hash=oracle_input.payload.answer_hash,
            r_context_hash=oracle_input.payload.context_hash,
            o_answer_hash=_sha256(generation.answer_text) if generation is not None else None,
            o_context_hash=o_context_hash,
            o_answer_outcome=generation.answer_outcome if generation is not None else None,
            o_judge_attempt_count=judge_attempt_count,
            o_judge_first_failure_code=judge_first_failure_code,
            o_judge_terminal_reason_code=judge_terminal_reason_code,
            o_judge_recovered_after_retry=judge_recovered_after_retry,
            metric_reason_codes=tuple(metric_reason_codes),
            pipeline_failure_code=pipeline_failure_code,
        )


def _generate_oracle_answer(
    settings: Settings,
    *,
    generator: AnswerGenerator,
    question: str,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
) -> _OracleGeneration:
    generation, _metadata = generate_evaluation_answer(
        settings,
        generator,
        GenerationRequest(
            message=question,
            context_items=context_items,
            max_output_chars=settings.generation_max_output_chars,
            temperature=0.0,
        ),
    )
    parsed = parse_generation_output(generation.content)
    context = tuple(item.text for item in context_items)
    if _is_insufficient_evidence_answer(parsed.answer_text):
        return _OracleGeneration(
            answer_text=parsed.answer_text,
            answer_outcome="abstained",
            citations=(),
            context=context,
        )
    _validate_generation_output_safety(
        parsed.answer_text,
        context_items=list(context_items),
    )
    cited_sources = validate_generation_citations(
        parsed,
        source_map=list(citation_sources),
    )
    return _OracleGeneration(
        answer_text=parsed.answer_text,
        answer_outcome="answered",
        citations=tuple(
            {
                "citation_id": source.local_citation_id,
                "local_citation_id": source.local_citation_id,
                "source_label": source.source_label,
                "snippet": source.snippet,
            }
            for source in cited_sources
        ),
        context=context,
    )


def _oracle_context_items(
    sources: Sequence[EvaluationCorpusSource],
    *,
    max_context_chars: int,
) -> tuple[tuple[GenerationContextItem, ...], tuple[CitationSource, ...]]:
    remaining = max_context_chars
    context_items: list[GenerationContextItem] = []
    citation_sources: list[CitationSource] = []
    for index, source in enumerate(sources, start=1):
        if remaining <= 0:
            break
        text = _normalize(source.body_text)
        if not text:
            raise EvaluationOracleContextError("oracle_corpus_source_empty")
        clipped = text[:remaining]
        remaining -= len(clipped)
        context_items.append(
            GenerationContextItem(
                document_chunk_id=source.evaluation_corpus_source_id,
                source_label=source.source_key,
                text=clipped,
                local_citation_id=index,
            )
        )
        citation_sources.append(
            CitationSource(
                local_citation_id=index,
                retrieval_run_item_id=source.evaluation_corpus_source_id,
                document_chunk_id=source.evaluation_corpus_source_id,
                source_label=source.source_key,
                snippet=clipped,
                page_from=None,
                page_to=None,
                section_title=source.title,
            )
        )
    if len(context_items) != len(sources):
        raise EvaluationOracleContextError("oracle_context_budget_exhausted")
    return tuple(context_items), tuple(citation_sources)


def _generation_settings(settings: Settings, retrieval: dict[str, object]) -> Settings:
    updates: dict[str, object] = {
        "generation_provider": "lmstudio",
        "generation_model_name": _EXPECTED_GENERATION_MODEL,
    }
    for field in (
        "generation_max_context_chars",
        "generation_max_output_chars",
        "generation_max_output_tokens",
    ):
        value = retrieval.get(field)
        if value is None and field == "generation_max_output_tokens":
            updates[field] = None
        elif isinstance(value, int) and not isinstance(value, bool) and value > 0:
            updates[field] = value
        else:
            raise EvaluationOracleContextError(f"oracle_{field}_invalid")
    return settings.model_copy(update=updates)


def _validate_r_judge_replay(
    replay: dict[str, object],
    *,
    run: EvaluationRun,
    inputs: tuple[_OracleInput, ...],
    expected_case_count: int,
) -> _StableRReplay:
    retrieval = cast(dict[str, object], run.retrieval_settings_json)
    if (
        replay.get("schema_version") != "phase3.judge_replay.v1"
        or replay.get("source_evaluation_run_id") != run.evaluation_run_id
        or replay.get("dataset_name") != _ALLOWED_DATASET
        or replay.get("resolved_generation_model") != _EXPECTED_GENERATION_MODEL
        or replay.get("dataset_content_fingerprint") != retrieval.get("dataset_content_fingerprint")
        or replay.get("corpus_fingerprint") != run.corpus_fingerprint
        or replay.get("expected_case_count") != expected_case_count
        or replay.get("gate_passed") is not True
        or replay.get("stable_case_count") != expected_case_count
        or replay.get("unstable_case_ids") != []
    ):
        raise EvaluationOracleContextError("oracle_r_judge_replay_not_comparable")
    replay_count = replay.get("repeats")
    summaries = replay.get("repeat_summaries")
    if (
        not isinstance(replay_count, int)
        or isinstance(replay_count, bool)
        or replay_count < 3
        or not isinstance(summaries, list)
        or len(summaries) != replay_count
    ):
        raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
    if replay.get("answer_set_fingerprint") != _r_answer_set_fingerprint(inputs):
        raise EvaluationOracleContextError("oracle_r_judge_replay_answer_set_mismatch")

    expected_case_ids = {item.case.case_key for item in inputs}
    expected_hashes = {
        item.case.case_key: (
            item.payload.answer_hash,
            _sha256("\x00".join(_string_tuple(item.payload.context_json))),
        )
        for item in inputs
    }
    stable: dict[str, _StableRJudgment] | None = None
    for repeat_index, summary in enumerate(summaries, start=1):
        if (
            not isinstance(summary, dict)
            or summary.get("repeat") != repeat_index
            or summary.get("applicable_count") != expected_case_count
            or summary.get("judged_count") != expected_case_count
            or summary.get("judge_failure_count") != 0
        ):
            raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
        raw_outcomes = summary.get("outcomes")
        if not isinstance(raw_outcomes, list) or len(raw_outcomes) != expected_case_count:
            raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
        current: dict[str, _StableRJudgment] = {}
        for outcome in raw_outcomes:
            if not isinstance(outcome, dict):
                raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
            case_id = outcome.get("case_id")
            auxiliary_pass = outcome.get("auxiliary_pass")
            answer_hash = outcome.get("answer_hash")
            context_hash = outcome.get("context_hash")
            decision_fingerprint = outcome.get("decision_fingerprint")
            if (
                not isinstance(case_id, str)
                or case_id not in expected_case_ids
                or case_id in current
                or not isinstance(auxiliary_pass, bool)
                or outcome.get("status") != "succeeded"
                or outcome.get("terminal_reason_code")
                not in {
                    "judge_succeeded_first_attempt",
                    "judge_recovered_after_retry",
                }
                or not isinstance(answer_hash, str)
                or not isinstance(context_hash, str)
                or not isinstance(decision_fingerprint, str)
                or len(decision_fingerprint) != 64
                or (answer_hash, context_hash) != expected_hashes[case_id]
            ):
                raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
            current[case_id] = _StableRJudgment(
                auxiliary_pass=auxiliary_pass,
                answer_hash=answer_hash,
                context_hash=context_hash,
                decision_fingerprint=decision_fingerprint,
            )
        if set(current) != expected_case_ids:
            raise EvaluationOracleContextError("oracle_r_judge_replay_case_set_mismatch")
        if stable is None:
            stable = current
        elif current != stable:
            raise EvaluationOracleContextError("oracle_r_judge_replay_unstable")
    if stable is None:
        raise EvaluationOracleContextError("oracle_r_judge_replay_invalid")
    return _StableRReplay(
        replay_count=replay_count,
        replay_fingerprint=_sha256(json.dumps(replay, sort_keys=True, separators=(",", ":"))),
        judgments=stable,
    )


def _r_answer_set_fingerprint(inputs: tuple[_OracleInput, ...]) -> str:
    payload = [
        {
            "case_id": item.case.case_key,
            "answer_hash": item.payload.answer_hash,
            "context_hash": item.payload.context_hash,
        }
        for item in inputs
    ]
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _case_set_fingerprint(inputs: tuple[_OracleInput, ...]) -> str:
    payload = [
        {
            "case_id": item.case.case_key,
            "question_hash": _sha256(item.case.question),
            "required_fact_ids": _required_fact_ids(item.required_facts),
            "source_keys": item.source_keys,
        }
        for item in inputs
    ]
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _generation_config_fingerprint(settings: Settings) -> str:
    payload = {
        "provider": settings.generation_provider,
        "model": settings.generation_model_name,
        "temperature": 0.0,
        "max_context_chars": settings.generation_max_context_chars,
        "max_output_chars": settings.generation_max_output_chars,
        "max_output_tokens": settings.generation_max_output_tokens,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _primary_next_target(
    *,
    retrieval_missing: int,
    utilization_or_noise: int,
    oracle_generation_gap: int,
) -> str:
    counts = {
        "retrieval_reranker_chunking": retrieval_missing,
        "context_structure_pruning_generation_prompt": utilization_or_noise,
        "generation_prompt_output_budget": oracle_generation_gap,
    }
    largest = max(counts.values(), default=0)
    if largest == 0:
        return "no_answerable_gap_detected"
    winners = sorted(name for name, count in counts.items() if count == largest)
    return winners[0] if len(winners) == 1 else "mixed_" + "_and_".join(winners)


def _expected_source_keys(value: object) -> tuple[str, ...]:
    evidence = _dict_tuple(value)
    result: list[str] = []
    for item in evidence:
        source_key = item.get("source_key")
        if not isinstance(source_key, str) or not source_key.strip():
            raise EvaluationOracleContextError("oracle_expected_source_shape_invalid")
        if source_key not in result:
            result.append(source_key)
    return tuple(result)


def _required_fact_ids(facts: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    result: list[str] = []
    for fact in facts:
        fact_id = fact.get("fact_id")
        if not isinstance(fact_id, str) or not fact_id.strip():
            raise EvaluationOracleContextError("oracle_required_fact_shape_invalid")
        result.append(fact_id)
    return tuple(result)


def _fact_count(
    texts: Sequence[str],
    required_facts: tuple[dict[str, object], ...],
) -> int:
    normalized_text = "\n".join(_normalize(text) for text in texts)
    return sum(
        _fact_statement(fact).casefold() in normalized_text.casefold() for fact in required_facts
    )


def _used_fact_count(
    answer: str,
    context: Sequence[str],
    required_facts: tuple[dict[str, object], ...],
) -> int:
    normalized_answer = _normalize(answer).casefold()
    normalized_context = "\n".join(_normalize(text) for text in context).casefold()
    return sum(
        statement.casefold() in normalized_context and statement.casefold() in normalized_answer
        for statement in (_fact_statement(fact) for fact in required_facts)
    )


def _fact_statement(fact: dict[str, object]) -> str:
    statement = fact.get("statement")
    if not isinstance(statement, str) or not _normalize(statement):
        raise EvaluationOracleContextError("oracle_required_fact_shape_invalid")
    return _normalize(statement)


def _dict_tuple(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise EvaluationOracleContextError("oracle_payload_shape_invalid")
    return tuple(cast(dict[str, object], item) for item in value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise EvaluationOracleContextError("oracle_payload_shape_invalid")
    return tuple(cast(str, item) for item in value)


def _optional_string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_tuple(value)


def _required_hash(value: object, reason_code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise EvaluationOracleContextError(reason_code)
    return value


def _normalize(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise EvaluationOracleContextError("oracle_metric_denominator_invalid")
    return round(numerator / denominator, 6)


def _rate(numerator: int, denominator: int) -> float:
    return _ratio(numerator, denominator)


def _mean(values: Iterable[float | None]) -> float | None:
    materialized = [value for value in values if value is not None]
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
