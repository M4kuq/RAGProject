from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from app.core.config import Settings, get_settings
from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.local_chunk_ablation import (
    CHUNK_ABLATION_DATASET_NAME,
    ChunkAblationCase,
    ProfileChunk,
    build_chunk_ablation_inputs,
    build_profile_chunks,
    chunk_ablation_profiles,
    collection_name_for_profile,
)
from app.evaluation.rag_service import generate_evaluation_answer
from app.ingest.embedding import (
    EmbeddingAdapterError,
    create_embedding_adapter,
    probe_lmstudio_embedding_dimension,
)
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.generation import (
    AnswerGenerationError,
    GenerationContextItem,
    GenerationRequest,
    check_lmstudio_model_readiness,
    create_answer_generator,
)
from app.rag.rerank import RerankCandidate, RerankError, create_reranker
from app.rag.retrieval import HttpQdrantSearchClient, RetrievalFilters
from app.scripts.run_local_chunk_ablation import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RERANKER_MODEL,
    ChunkAblationRunError,
    _append_event,
    _embed_batched,
    _percentile_95,
    _safe_git_sha,
    _source_number,
    _validate_local_settings,
    _write_safe_json,
)
from app.services.evaluation_judge_service import (
    EvaluationClaimJudgeError,
    EvaluationClaimJudgeService,
)
from app.services.rag_service import (
    _is_insufficient_evidence_answer,
    _validate_generation_output_safety,
)

EXPECTED_GENERATION_MODEL = "qwen/qwen3.5-9b"
EXPECTED_PROFILE_IDS = ("C0", "C3", "C1")
GENERATION_PROMPT_PROFILE = "baseline"
GENERATION_MAX_CONTEXT_CHARS = 6_000
GENERATION_MAX_OUTPUT_CHARS = 12_000
GENERATION_MAX_OUTPUT_TOKENS = 8_192
TOP_K = 20
RERANK_TOP_N = 3


@dataclass(frozen=True)
class RetrievedCaseContext:
    case: ChunkAblationCase
    context_items: tuple[GenerationContextItem, ...]
    citation_sources: tuple[CitationSource, ...]
    context_hash: str
    context_point_ids: tuple[int, ...]
    retrieval_latency_ms: int
    top1_retrieval_score: float | None
    top1_rerank_score: float | None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run raw-free Qwen3.5 9B/A3 confirmation for RAG-60 C0/C3/C1 "
            "without changing the default ingest profile."
        )
    )
    parser.add_argument("--confirm-local-only", action="store_true")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--git-sha", default="unknown")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    activity_path = output_dir / "activity.jsonl"
    _append_event(
        activity_path,
        run_id=run_id,
        event="end_to_end_run_started",
        status="running",
        git_sha=_safe_git_sha(args.git_sha),
        dataset_name=CHUNK_ABLATION_DATASET_NAME,
        profile_ids=list(EXPECTED_PROFILE_IDS),
        repeats=args.repeats,
        generation_profile="A3",
        generation_model_requested=EXPECTED_GENERATION_MODEL,
        top_k=TOP_K,
        rerank_top_n=RERANK_TOP_N,
        raw_content_persisted=False,
    )

    try:
        _validate_cli(args)
        dataset_fingerprint, stress_fingerprint, sources, cases = (
            build_chunk_ablation_inputs()
        )
        settings, model_payload = _experiment_settings(args)
        _append_event(
            activity_path,
            run_id=run_id,
            event="end_to_end_model_preflight_completed",
            status="succeeded",
            **model_payload,
        )
        embedding_adapter = create_embedding_adapter(settings)
        reranker = create_reranker(settings)
        answer_generator = create_answer_generator(
            settings,
            provider="lmstudio",
            model_name=EXPECTED_GENERATION_MODEL,
            max_output_tokens=GENERATION_MAX_OUTPUT_TOKENS,
        )
        judge = EvaluationClaimJudgeService(
            settings,
            provider="lmstudio",
            model=EXPECTED_GENERATION_MODEL,
        )
        query_vectors = _embed_batched(
            embedding_adapter,
            [case.question for case in cases],
        )
        prompt_profile = resolve_generation_prompt_profile(GENERATION_PROMPT_PROFILE)
        profile_by_id = {
            profile.profile_id: profile for profile in chunk_ablation_profiles()
        }
        profile_results: list[dict[str, object]] = []
        for profile_id in EXPECTED_PROFILE_IDS:
            profile = profile_by_id[profile_id]
            records, geometry = build_profile_chunks(profile, sources)
            collection_name = collection_name_for_profile(
                base_name=settings.qdrant_collection_name,
                dataset_fingerprint=dataset_fingerprint,
                corpus_fingerprint=geometry.corpus_fingerprint,
                profile_fingerprint=profile.fingerprint,
                embedding_model=cast(str, model_payload["embedding_model_resolved"]),
                embedding_dimension=cast(int, model_payload["embedding_dimension"]),
            )
            _append_event(
                activity_path,
                run_id=run_id,
                event="end_to_end_profile_started",
                status="running",
                profile_id=profile_id,
                chunk_profile_fingerprint=profile.fingerprint,
                corpus_fingerprint=geometry.corpus_fingerprint,
                collection_name=collection_name,
            )
            contexts = _retrieve_contexts(
                records=records,
                cases=cases,
                query_vectors=query_vectors,
                reranker=reranker,
                collection_name=collection_name,
                qdrant_url=settings.qdrant_url,
                qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
            )
            context_set_fingerprint = _fingerprint(
                [
                    {
                        "case_hash": item.case.case_hash,
                        "context_hash": item.context_hash,
                        "point_ids": item.context_point_ids,
                    }
                    for item in contexts
                ]
            )
            outcomes: list[dict[str, object]] = []
            for repeat in range(1, args.repeats + 1):
                for context in contexts:
                    outcome = _run_case(
                        context,
                        repeat=repeat,
                        settings=settings,
                        answer_generator=answer_generator,
                        judge=judge,
                        system_instructions=prompt_profile.system_instructions,
                    )
                    outcomes.append(outcome)
                    _append_event(
                        activity_path,
                        run_id=run_id,
                        event="end_to_end_case_completed",
                        status=(
                            "succeeded"
                            if outcome["pipeline_failure_code"] is None
                            else "completed_with_failure"
                        ),
                        profile_id=profile_id,
                        **_safe_case_event(outcome),
                    )
            result = _aggregate_profile(
                profile_id=profile_id,
                profile_fingerprint=profile.fingerprint,
                corpus_fingerprint=geometry.corpus_fingerprint,
                collection_name=collection_name,
                context_set_fingerprint=context_set_fingerprint,
                outcomes=outcomes,
                repeats=args.repeats,
                expected_case_count=len(cases),
            )
            profile_results.append(result)
            _append_event(
                activity_path,
                run_id=run_id,
                event="end_to_end_profile_completed",
                status="succeeded",
                **_safe_profile_event(result),
            )

        repeat_candidates = _select_repeat_candidates(profile_results)
        recommendation = _recommend_profile(profile_results, args.repeats)
        promotion_decision = _promotion_decision(
            repeats=args.repeats,
            repeat_candidates=repeat_candidates,
            recommendation=recommendation,
        )
        summary = {
            "schema_version": "rag60.chunk_ablation.end_to_end.v1",
            "run_id": run_id,
            "git_sha": _safe_git_sha(args.git_sha),
            "dataset_name": CHUNK_ABLATION_DATASET_NAME,
            "dataset_fingerprint": dataset_fingerprint,
            "base_stress_corpus_fingerprint": stress_fingerprint,
            "case_count": len(cases),
            "profile_ids": list(EXPECTED_PROFILE_IDS),
            "repeats": args.repeats,
            "generation_profile": "A3",
            "generation_provider": "lmstudio",
            "generation_model_requested": EXPECTED_GENERATION_MODEL,
            "generation_model_resolved": model_payload["generation_model_resolved"],
            "judge_provider": "lmstudio",
            "judge_model_requested": EXPECTED_GENERATION_MODEL,
            "judge_model_resolved": model_payload["generation_model_resolved"],
            "generation_temperature": 0.0,
            "generation_max_context_chars": GENERATION_MAX_CONTEXT_CHARS,
            "generation_max_output_chars": GENERATION_MAX_OUTPUT_CHARS,
            "generation_max_output_tokens": GENERATION_MAX_OUTPUT_TOKENS,
            "generation_retry_policy": "evaluation_empty_or_citation_retry_v1",
            "generation_retry_on_insufficient_evidence_setting": True,
            "generation_prompt_profile": prompt_profile.name,
            "generation_prompt_fingerprint": prompt_profile.prompt_fingerprint,
            "embedding_model_requested": model_payload["embedding_model_requested"],
            "embedding_model_resolved": model_payload["embedding_model_resolved"],
            "embedding_dimension": model_payload["embedding_dimension"],
            "reranker_model": DEFAULT_RERANKER_MODEL,
            "retrieval_strategy": "dense",
            "top_k": TOP_K,
            "rerank_top_n": RERANK_TOP_N,
            "cache_enabled": False,
            "profiles": profile_results,
            "selected_for_additional_repeats": repeat_candidates,
            "recommended_profile": recommendation,
            "promotion_decision": promotion_decision,
            "decision_basis": (
                "same-model auxiliary Judge only; raw-free run has no manual "
                "calibration; Gold v2 was not opened"
            ),
            "raw_content_persisted": False,
            "external_content_transmission": False,
            "gold_v2_opened": False,
            "default_profile_changed": False,
        }
        _write_safe_json(output_dir / f"{run_id}-end-to-end.json", summary)
        _write_markdown(output_dir / f"{run_id}-end-to-end.md", summary)
        _append_event(
            activity_path,
            run_id=run_id,
            event="end_to_end_run_completed",
            status="succeeded",
            selected_for_additional_repeats=repeat_candidates,
            recommended_profile=recommendation,
            promotion_decision=summary["promotion_decision"],
            raw_content_persisted=False,
        )
        print(
            json.dumps(
                {
                    "status": "succeeded",
                    "run_id": run_id,
                    "selected_for_additional_repeats": repeat_candidates,
                    "recommended_profile": recommendation,
                    "promotion_decision": summary["promotion_decision"],
                    "output_dir": str(output_dir),
                },
                sort_keys=True,
            )
        )
        return 0
    except ChunkAblationRunError as exc:
        reason_code = exc.reason_code
    except EmbeddingAdapterError as exc:
        reason_code = exc.error_code
    except RerankError:
        reason_code = "reranker_unavailable"
    except Exception:
        reason_code = "chunk_end_to_end_unexpected_failure"

    _append_event(
        activity_path,
        run_id=run_id,
        event="end_to_end_run_completed",
        status="blocked",
        reason_codes=[reason_code],
        raw_content_persisted=False,
    )
    print(
        json.dumps(
            {
                "status": "blocked",
                "run_id": run_id,
                "reason_codes": [reason_code],
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 2


def _validate_cli(args: argparse.Namespace) -> None:
    if not args.confirm_local_only:
        raise ChunkAblationRunError("local_confirmation_required")
    if args.repeats < 1 or args.repeats > 3:
        raise ChunkAblationRunError("repeat_count_invalid")
    if not args.allow_model_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _experiment_settings(
    args: argparse.Namespace,
) -> tuple[Settings, dict[str, object]]:
    settings = get_settings()
    _validate_local_settings(settings)
    embedding_probe = probe_lmstudio_embedding_dimension(
        settings,
        model_name=DEFAULT_EMBEDDING_MODEL,
    )
    generation_readiness = check_lmstudio_model_readiness(
        settings,
        EXPECTED_GENERATION_MODEL,
    )
    if not generation_readiness.ready:
        raise ChunkAblationRunError(f"generation_{generation_readiness.reason_code}")
    if generation_readiness.resolved_model != EXPECTED_GENERATION_MODEL:
        raise ChunkAblationRunError("generation_model_not_frozen")
    experiment_settings = settings.model_copy(
        update={
            "embedding_provider": "lmstudio",
            "embedding_model": DEFAULT_EMBEDDING_MODEL,
            "embedding_vector_dimension": embedding_probe.dimension,
            "rerank_provider": "local",
            "reranker_model": DEFAULT_RERANKER_MODEL,
            "retrieval_cache_enabled": False,
            "trace_export_enabled": False,
            "trace_export_provider": "none",
            "generation_provider": "lmstudio",
            "generation_model_name": EXPECTED_GENERATION_MODEL,
            "generation_max_context_chars": GENERATION_MAX_CONTEXT_CHARS,
            "generation_max_output_chars": GENERATION_MAX_OUTPUT_CHARS,
            "generation_max_output_tokens": GENERATION_MAX_OUTPUT_TOKENS,
            "generation_retry_on_insufficient_evidence": True,
            "generation_prompt_profile": GENERATION_PROMPT_PROFILE,
        }
    )
    return experiment_settings, {
        "embedding_model_requested": embedding_probe.requested_model,
        "embedding_model_resolved": embedding_probe.resolved_model,
        "embedding_dimension": embedding_probe.dimension,
        "reranker_model": DEFAULT_RERANKER_MODEL,
        "model_download_allowed": args.allow_model_download,
        "generation_model_requested": generation_readiness.requested_model,
        "generation_model_resolved": generation_readiness.resolved_model,
        "generation_readiness": generation_readiness.reason_code,
    }


def _retrieve_contexts(
    *,
    records: tuple[ProfileChunk, ...],
    cases: tuple[ChunkAblationCase, ...],
    query_vectors: Sequence[Sequence[float]],
    reranker: Any,
    collection_name: str,
    qdrant_url: str,
    qdrant_timeout_seconds: float,
) -> tuple[RetrievedCaseContext, ...]:
    search_client = HttpQdrantSearchClient(
        url=qdrant_url,
        timeout_seconds=qdrant_timeout_seconds,
    )
    by_point_id = {record.point_id: record for record in records}
    retrieved: list[RetrievedCaseContext] = []
    for case, query_vector in zip(cases, query_vectors, strict=True):
        started = time.perf_counter()
        try:
            dense = search_client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=TOP_K,
                filters=RetrievalFilters(),
            )
        except Exception as exc:
            raise ChunkAblationRunError("isolated_collection_not_ready") from exc
        candidates: list[RerankCandidate] = []
        retrieval_scores: dict[int, float] = {}
        for item in dense:
            if item.document_chunk_id is None:
                continue
            record = by_point_id.get(item.document_chunk_id)
            if record is None:
                continue
            retrieval_scores[record.point_id] = item.retrieval_score
            candidates.append(
                RerankCandidate(
                    document_chunk_id=record.point_id,
                    text=record.chunk.content_text,
                    retrieval_score=item.retrieval_score,
                )
            )
        try:
            reranked = reranker.rerank(query=case.question, candidates=candidates)
        except (OSError, RerankError) as exc:
            raise ChunkAblationRunError("reranker_model_unavailable") from exc
        selected = reranked[:RERANK_TOP_N]
        selected_records = tuple(
            by_point_id[item.document_chunk_id]
            for item in selected
            if item.document_chunk_id in by_point_id
        )
        if not selected_records:
            raise ChunkAblationRunError("no_context_found")
        context_items, citation_sources = _context_payload(selected_records)
        if not context_items:
            raise ChunkAblationRunError("no_context_found")
        context_texts = tuple(item.text for item in context_items)
        retrieved.append(
            RetrievedCaseContext(
                case=case,
                context_items=context_items,
                citation_sources=citation_sources,
                context_hash=_sha256("\x00".join(context_texts)),
                context_point_ids=tuple(
                    item.document_chunk_id for item in context_items
                ),
                retrieval_latency_ms=max(
                    0, int(round((time.perf_counter() - started) * 1000))
                ),
                top1_retrieval_score=(
                    max(retrieval_scores.values()) if retrieval_scores else None
                ),
                top1_rerank_score=selected[0].rerank_score if selected else None,
            )
        )
    return tuple(retrieved)


def _context_payload(
    records: tuple[ProfileChunk, ...],
) -> tuple[tuple[GenerationContextItem, ...], tuple[CitationSource, ...]]:
    remaining = GENERATION_MAX_CONTEXT_CHARS
    context_items: list[GenerationContextItem] = []
    citation_sources: list[CitationSource] = []
    for index, record in enumerate(records, start=1):
        if remaining <= 0:
            break
        text = record.chunk.content_text[:remaining].strip()
        if not text:
            continue
        source_label = f"dev-fixture-{_source_number(record.source_key):02d}"
        context_items.append(
            GenerationContextItem(
                document_chunk_id=record.point_id,
                source_label=source_label,
                text=text,
                local_citation_id=index,
                page_from=record.chunk.page_from,
                page_to=record.chunk.page_to,
            )
        )
        citation_sources.append(
            CitationSource(
                local_citation_id=index,
                retrieval_run_item_id=record.point_id,
                document_chunk_id=record.point_id,
                source_label=source_label,
                snippet=text,
                page_from=record.chunk.page_from,
                page_to=record.chunk.page_to,
                section_title=record.chunk.section_title,
                source_type="evaluation_fixture",
            )
        )
        remaining -= len(text)
    return tuple(context_items), tuple(citation_sources)


def _run_case(
    retrieved: RetrievedCaseContext,
    *,
    repeat: int,
    settings: Settings,
    answer_generator: Any,
    judge: EvaluationClaimJudgeService,
    system_instructions: str | None,
) -> dict[str, object]:
    case = retrieved.case
    started = time.perf_counter()
    generation_latency_ms = 0
    judge_latency_ms = 0
    answer_hash: str | None = None
    output_tokens: int | None = None
    input_tokens: int | None = None
    answer_outcome: Literal["answered", "abstained"] | None = None
    citation_count = 0
    pipeline_failure_code: str | None
    auxiliary_pass: bool | None
    judge_attempt_count: int
    judge_first_failure_code: str | None
    judge_terminal_reason_code: str
    judge_recovered_after_retry: bool
    judge_confidence: float | None
    required_facts_supported: str | None
    citation_support: str | None
    forbidden_claims_absent: str | None
    abstention_correct: str | None
    prompt_injection_resisted: str | None
    judge_reason_codes: tuple[str, ...]
    claim_faithfulness: float | None
    try:
        generation, metadata = generate_evaluation_answer(
            settings,
            answer_generator,
            GenerationRequest(
                message=case.question,
                context_items=list(retrieved.context_items),
                max_output_chars=GENERATION_MAX_OUTPUT_CHARS,
                system_instructions=system_instructions,
                temperature=0.0,
            ),
        )
        generation_latency_ms = metadata.latency_ms or 0
        output_tokens = metadata.output_tokens
        input_tokens = metadata.input_tokens
        parsed = parse_generation_output(generation.content)
        answer_hash = _sha256(parsed.answer_text)
        citations: list[dict[str, object]]
        if _is_insufficient_evidence_answer(parsed.answer_text):
            answer_outcome = "abstained"
            citations = []
        else:
            answer_outcome = "answered"
            _validate_generation_output_safety(
                parsed.answer_text,
                context_items=list(retrieved.context_items),
            )
            cited_sources = validate_generation_citations(
                parsed,
                source_map=list(retrieved.citation_sources),
            )
            citations = [
                {
                    "citation_id": source.local_citation_id,
                    "local_citation_id": source.local_citation_id,
                    "source_label": source.source_label,
                    "snippet": source.snippet,
                }
                for source in cited_sources
            ]
            citation_count = len(citations)
        judge_started = time.perf_counter()
        judged = judge.judge(
            case_id=case.case_id,
            answerable=case.answerable,
            required_citation=case.required_citation,
            tags=list(case.tags),
            answer_outcome=answer_outcome,
            answer_text=parsed.answer_text,
            citations=citations,
            context=[item.text for item in retrieved.context_items],
            required_facts=list(case.required_facts),
            forbidden_claims=list(case.forbidden_claims),
        )
        judge_latency_ms = max(
            0, int(round((time.perf_counter() - judge_started) * 1000))
        )
        decision = judged.decision
        pipeline_failure_code = None
        auxiliary_pass = judged.auxiliary_pass
        judge_attempt_count = judged.attempt_count
        judge_first_failure_code = judged.first_failure_code
        judge_terminal_reason_code = judged.terminal_reason_code
        judge_recovered_after_retry = judged.recovered_after_retry
        judge_confidence = decision.confidence
        required_facts_supported = decision.required_facts_supported.value
        citation_support = decision.citation_support.value
        forbidden_claims_absent = decision.forbidden_claims_absent.value
        abstention_correct = decision.abstention_correct.value
        prompt_injection_resisted = decision.prompt_injection_resisted.value
        judge_reason_codes = tuple(
            sorted(code.value for code in decision.reason_codes)
        )
        claim_faithfulness = judged.claim_faithfulness
    except EvaluationClaimJudgeError as exc:
        pipeline_failure_code = exc.terminal_reason_code
        auxiliary_pass = None
        judge_attempt_count = exc.attempt_count
        judge_first_failure_code = exc.first_failure_code
        judge_terminal_reason_code = exc.terminal_reason_code
        judge_recovered_after_retry = False
        judge_confidence = None
        required_facts_supported = None
        citation_support = None
        forbidden_claims_absent = None
        abstention_correct = None
        prompt_injection_resisted = None
        judge_reason_codes = ()
        claim_faithfulness = None
    except AnswerGenerationError as exc:
        pipeline_failure_code = f"generation_{exc.error_category}"
        auxiliary_pass = None
        judge_attempt_count = 0
        judge_first_failure_code = None
        judge_terminal_reason_code = "judge_not_run"
        judge_recovered_after_retry = False
        judge_confidence = None
        required_facts_supported = None
        citation_support = None
        forbidden_claims_absent = None
        abstention_correct = None
        prompt_injection_resisted = None
        judge_reason_codes = ()
        claim_faithfulness = None
    except CitationBuildError as exc:
        pipeline_failure_code = exc.detail_code
        auxiliary_pass = None
        judge_attempt_count = 0
        judge_first_failure_code = None
        judge_terminal_reason_code = "judge_not_run"
        judge_recovered_after_retry = False
        judge_confidence = None
        required_facts_supported = None
        citation_support = None
        forbidden_claims_absent = None
        abstention_correct = None
        prompt_injection_resisted = None
        judge_reason_codes = ()
        claim_faithfulness = None
    total_latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    return {
        "case_hash": case.case_hash,
        "repeat": repeat,
        "answerable": case.answerable,
        "language": case.language,
        "prompt_injection": "prompt_injection" in case.tags,
        "context_hash": retrieved.context_hash,
        "answer_hash": answer_hash,
        "answer_outcome": answer_outcome,
        "citation_count": citation_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "retrieval_latency_ms": retrieved.retrieval_latency_ms,
        "generation_latency_ms": generation_latency_ms,
        "judge_latency_ms": judge_latency_ms,
        "end_to_end_latency_ms": retrieved.retrieval_latency_ms + total_latency_ms,
        "top1_retrieval_score": retrieved.top1_retrieval_score,
        "top1_rerank_score": retrieved.top1_rerank_score,
        "auxiliary_pass": auxiliary_pass,
        "claim_faithfulness": claim_faithfulness,
        "judge_attempt_count": judge_attempt_count,
        "judge_first_failure_code": judge_first_failure_code,
        "judge_terminal_reason_code": judge_terminal_reason_code,
        "judge_recovered_after_retry": judge_recovered_after_retry,
        "judge_confidence": judge_confidence,
        "required_facts_supported": required_facts_supported,
        "citation_support": citation_support,
        "forbidden_claims_absent": forbidden_claims_absent,
        "abstention_correct": abstention_correct,
        "prompt_injection_resisted": prompt_injection_resisted,
        "judge_reason_codes": judge_reason_codes,
        "pipeline_failure_code": pipeline_failure_code,
    }


def _aggregate_profile(
    *,
    profile_id: str,
    profile_fingerprint: str,
    corpus_fingerprint: str,
    collection_name: str,
    context_set_fingerprint: str,
    outcomes: Sequence[dict[str, object]],
    repeats: int,
    expected_case_count: int,
) -> dict[str, object]:
    expected_outcome_count = expected_case_count * repeats
    if len(outcomes) != expected_outcome_count:
        raise ChunkAblationRunError("end_to_end_case_count_mismatch")
    failures = [
        item for item in outcomes if item["pipeline_failure_code"] is not None
    ]
    answerable = [item for item in outcomes if item["answerable"] is True]
    unanswerable = [item for item in outcomes if item["answerable"] is False]
    injection = [item for item in outcomes if item["prompt_injection"] is True]
    pass_count = sum(item["auxiliary_pass"] is True for item in outcomes)
    majority_pass_count = _majority_pass_count(outcomes, repeats)
    return {
        "profile_id": profile_id,
        "chunk_profile_fingerprint": profile_fingerprint,
        "corpus_fingerprint": corpus_fingerprint,
        "collection_name": collection_name,
        "context_set_fingerprint": context_set_fingerprint,
        "expected_case_count": expected_case_count,
        "repeats": repeats,
        "expected_outcome_count": expected_outcome_count,
        "judged_outcome_count": sum(
            item["auxiliary_pass"] is not None for item in outcomes
        ),
        "pipeline_failure_count": len(failures),
        "pipeline_failure_reason_counts": _reason_counts(
            cast(str, item["pipeline_failure_code"]) for item in failures
        ),
        "auxiliary_pass_count": pass_count,
        "auxiliary_pass_rate_conservative": pass_count / expected_outcome_count,
        "majority_auxiliary_pass_count": majority_pass_count,
        "majority_auxiliary_pass_rate_conservative": (
            majority_pass_count / expected_case_count
        ),
        "answerable_auxiliary_pass_rate_conservative": _pass_rate(answerable),
        "unanswerable_auxiliary_pass_rate_conservative": _pass_rate(unanswerable),
        "prompt_injection_auxiliary_pass_rate_conservative": _pass_rate(injection),
        "required_facts_supported_rate": _dimension_pass_rate(
            outcomes, "required_facts_supported"
        ),
        "citation_support_rate": _dimension_pass_rate(outcomes, "citation_support"),
        "forbidden_claims_absent_rate": _dimension_pass_rate(
            outcomes, "forbidden_claims_absent"
        ),
        "abstention_correct_rate": _dimension_pass_rate(
            outcomes, "abstention_correct"
        ),
        "prompt_injection_resisted_rate": _dimension_pass_rate(
            outcomes, "prompt_injection_resisted"
        ),
        "mean_claim_faithfulness": _optional_mean(outcomes, "claim_faithfulness"),
        "mean_judge_confidence": _optional_mean(outcomes, "judge_confidence"),
        "retrieval_p95_latency_ms": _percentile_95(
            [cast(int, item["retrieval_latency_ms"]) for item in outcomes]
        ),
        "generation_p95_latency_ms": _percentile_95(
            [cast(int, item["generation_latency_ms"]) for item in outcomes]
        ),
        "judge_p95_latency_ms": _percentile_95(
            [cast(int, item["judge_latency_ms"]) for item in outcomes]
        ),
        "end_to_end_p95_latency_ms": _percentile_95(
            [cast(int, item["end_to_end_latency_ms"]) for item in outcomes]
        ),
        "input_token_total": sum(
            cast(int, item["input_tokens"])
            for item in outcomes
            if item["input_tokens"] is not None
        ),
        "output_token_total": sum(
            cast(int, item["output_tokens"])
            for item in outcomes
            if item["output_tokens"] is not None
        ),
        "cases": list(outcomes),
    }


def _majority_pass_count(
    outcomes: Sequence[dict[str, object]],
    repeats: int,
) -> int:
    by_case: dict[str, list[bool]] = {}
    for item in outcomes:
        by_case.setdefault(cast(str, item["case_hash"]), []).append(
            item["auxiliary_pass"] is True
        )
    threshold = (repeats // 2) + 1
    return sum(sum(values) >= threshold for values in by_case.values())


def _select_repeat_candidates(results: Sequence[dict[str, object]]) -> list[str]:
    baseline = next(
        (item for item in results if item.get("profile_id") == "C0"),
        None,
    )
    if baseline is None:
        return []
    eligible: list[dict[str, object]] = []
    for item in results:
        if item.get("profile_id") == "C0":
            continue
        if cast(int, item["pipeline_failure_count"]) != 0:
            continue
        if _metric(item, "auxiliary_pass_rate_conservative") <= _metric(
            baseline, "auxiliary_pass_rate_conservative"
        ):
            continue
        if any(
            _metric(item, key) < _metric(baseline, key)
            for key in (
                "unanswerable_auxiliary_pass_rate_conservative",
                "prompt_injection_auxiliary_pass_rate_conservative",
                "required_facts_supported_rate",
                "citation_support_rate",
            )
        ):
            continue
        if _metric(item, "end_to_end_p95_latency_ms") > (
            2 * _metric(baseline, "end_to_end_p95_latency_ms")
        ):
            continue
        eligible.append(item)
    ranked = sorted(
        eligible,
        key=lambda item: (
            -_metric(item, "auxiliary_pass_rate_conservative"),
            -_metric(item, "citation_support_rate"),
            -_metric(item, "required_facts_supported_rate"),
            _metric(item, "end_to_end_p95_latency_ms"),
            str(item["profile_id"]),
        ),
    )
    return [cast(str, item["profile_id"]) for item in ranked]


def _recommend_profile(
    results: Sequence[dict[str, object]],
    repeats: int,
) -> str | None:
    if repeats < 3:
        return None
    candidates = _select_repeat_candidates(results)
    return candidates[0] if candidates else None


def _promotion_decision(
    *,
    repeats: int,
    repeat_candidates: Sequence[str],
    recommendation: str | None,
) -> str:
    if repeats < 3:
        return (
            "not_promoted_requires_three_repeats_and_manual_calibration"
            if repeat_candidates
            else "not_promoted_no_candidate_passed_single_repeat_gate"
        )
    return (
        "not_promoted_requires_manual_calibration"
        if recommendation is not None
        else "not_promoted_no_candidate_passed_three_repeat_gate"
    )


def _dimension_pass_rate(
    outcomes: Sequence[dict[str, object]],
    key: str,
) -> float:
    applicable = [
        item for item in outcomes if item[key] not in {None, "not_applicable"}
    ]
    if not applicable:
        return 1.0
    return sum(item[key] == "pass" for item in applicable) / len(applicable)


def _pass_rate(outcomes: Sequence[dict[str, object]]) -> float:
    if not outcomes:
        return 1.0
    return sum(item["auxiliary_pass"] is True for item in outcomes) / len(outcomes)


def _optional_mean(
    outcomes: Sequence[dict[str, object]],
    key: str,
) -> float | None:
    values = [
        float(cast(float | int, item[key]))
        for item in outcomes
        if item[key] is not None
    ]
    return statistics.fmean(values) if values else None


def _metric(item: dict[str, object], key: str) -> float:
    value = item[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ChunkAblationRunError("end_to_end_metric_invalid")
    return float(value)


def _reason_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _safe_profile_event(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "profile_id",
        "chunk_profile_fingerprint",
        "corpus_fingerprint",
        "collection_name",
        "context_set_fingerprint",
        "expected_case_count",
        "repeats",
        "expected_outcome_count",
        "judged_outcome_count",
        "pipeline_failure_count",
        "pipeline_failure_reason_counts",
        "auxiliary_pass_count",
        "auxiliary_pass_rate_conservative",
        "majority_auxiliary_pass_count",
        "majority_auxiliary_pass_rate_conservative",
        "answerable_auxiliary_pass_rate_conservative",
        "unanswerable_auxiliary_pass_rate_conservative",
        "prompt_injection_auxiliary_pass_rate_conservative",
        "required_facts_supported_rate",
        "citation_support_rate",
        "prompt_injection_resisted_rate",
        "end_to_end_p95_latency_ms",
    }
    return {key: result[key] for key in allowed if key in result}


def _safe_case_event(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "case_hash",
        "repeat",
        "answerable",
        "language",
        "prompt_injection",
        "context_hash",
        "answer_hash",
        "answer_outcome",
        "citation_count",
        "input_tokens",
        "output_tokens",
        "retrieval_latency_ms",
        "generation_latency_ms",
        "judge_latency_ms",
        "end_to_end_latency_ms",
        "top1_retrieval_score",
        "top1_rerank_score",
        "auxiliary_pass",
        "claim_faithfulness",
        "judge_attempt_count",
        "judge_first_failure_code",
        "judge_terminal_reason_code",
        "judge_recovered_after_retry",
        "judge_confidence",
        "required_facts_supported",
        "citation_support",
        "forbidden_claims_absent",
        "abstention_correct",
        "prompt_injection_resisted",
        "judge_reason_codes",
        "pipeline_failure_code",
    }
    return {key: result[key] for key in allowed if key in result}


def _write_markdown(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# RAG-60 chunk end-to-end auxiliary evaluation",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Git SHA: `{summary['git_sha']}`",
        f"- Dataset fingerprint: `{summary['dataset_fingerprint']}`",
        f"- Generation / Judge: `{summary['generation_model_resolved']}`",
        f"- Embedding: `{summary['embedding_model_resolved']}`",
        f"- Repeats: `{summary['repeats']}`",
        f"- Promotion: `{summary['promotion_decision']}`",
        f"- Additional repeat candidates: `{summary['selected_for_additional_repeats']}`",
        "- Raw question, chunk, answer, and context text persisted: `false`",
        "- External content transmission: `false`",
        "- Gold v2 opened: `false`",
        "- Default profile changed: `false`",
        "",
        "| Profile | Aux pass | Answerable | Unanswerable | Injection | "
        "Fact support | Citation | p95 ms | Failures |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    raw_profiles = summary.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ChunkAblationRunError("summary_profiles_invalid")
    for item in raw_profiles:
        if not isinstance(item, dict):
            raise ChunkAblationRunError("summary_profiles_invalid")
        row = cast(dict[str, Any], item)
        lines.append(
            "| {profile_id} | {auxiliary_pass_rate_conservative:.6f} | "
            "{answerable_auxiliary_pass_rate_conservative:.6f} | "
            "{unanswerable_auxiliary_pass_rate_conservative:.6f} | "
            "{prompt_injection_auxiliary_pass_rate_conservative:.6f} | "
            "{required_facts_supported_rate:.6f} | "
            "{citation_support_rate:.6f} | "
            "{end_to_end_p95_latency_ms:.3f} | "
            "{pipeline_failure_count} |".format(**row)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fingerprint(value: object) -> str:
    return _sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
