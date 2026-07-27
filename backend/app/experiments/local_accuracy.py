from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.experiments.schemas import ExperimentManifest, ExperimentRetrievalProfile


@dataclass(frozen=True)
class LocalAccuracyCandidate:
    candidate_id: str
    profile_id: str
    embedding_model: str
    reranker_model: str | None
    strategy: str
    router_mode: str | None
    top_k: int
    rerank_top_n: int
    dense_weight: float
    sparse_weight: float
    rrf_k: int
    agentic_sufficiency_threshold: float
    graph_depth: int
    graph_router_signal_threshold: float
    supplemental: bool


@dataclass(frozen=True)
class RetrievalScreenResult:
    candidate_id: str
    recall_at_k: float
    mrr: float
    no_context_rate: float
    pipeline_failure_count: int = 0


@dataclass(frozen=True)
class EndToEndResult:
    candidate_id: str
    grounded_answer_pass_rate: float
    citation_correctness: float
    answer_completeness: float
    p95_latency_ms: float


@dataclass(frozen=True)
class PromotionGateInput:
    baseline_mean_pass_rate: float
    candidate_mean_pass_rate: float
    baseline_unanswerable_accuracy: float
    candidate_unanswerable_accuracy: float
    baseline_prompt_injection_resistance: float
    candidate_prompt_injection_resistance: float
    baseline_citation_correctness: float
    candidate_citation_correctness: float
    baseline_answer_completeness: float
    candidate_answer_completeness: float
    baseline_p95_latency_ms: float
    candidate_p95_latency_ms: float
    pipeline_failure_count: int


@dataclass(frozen=True)
class PromotionGateResult:
    promote: bool
    absolute_percentage_point_delta: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RepeatCaseOutcome:
    case_id: str
    repeat_number: int
    passed: bool
    result_changed: bool = False
    hard_gate_failed: bool = False
    judge_confidence: float | None = None
    judge_human_disagreement: bool = False


def build_coordinate_search_candidates(
    manifest: ExperimentManifest,
) -> list[LocalAccuracyCandidate]:
    if manifest.schema_version != "phase2.experiment.v2" or manifest.tuning_grid is None:
        raise ValueError("coordinate search requires an experiment v2 manifest")
    grid = manifest.tuning_grid
    candidates: list[LocalAccuracyCandidate] = []
    for profile in manifest.retrieval_profiles:
        candidates.append(_candidate(profile, variant="base"))

    baseline = next(
        (
            profile
            for profile in manifest.retrieval_profiles
            if profile.profile_id == manifest.baseline_profile
        ),
        manifest.retrieval_profiles[0],
    )
    top_k_variant = next((value for value in grid.top_k if value != 10), None)
    if top_k_variant is not None:
        candidates.append(
            _candidate(baseline, variant=f"top{top_k_variant}", top_k=top_k_variant)
        )

    reranked = next(
        (
            profile
            for profile in manifest.retrieval_profiles
            if profile.reranker_model is not None and not profile.supplemental
        ),
        None,
    )
    rerank_variant = next((value for value in grid.rerank_top_n if value != 3), None)
    if reranked is not None and rerank_variant is not None:
        candidates.append(
            _candidate(
                reranked,
                variant=f"rerank{rerank_variant}",
                rerank_top_n=rerank_variant,
            )
        )

    hybrid = next(
        (
            profile
            for profile in manifest.retrieval_profiles
            if profile.strategy == "hybrid"
        ),
        None,
    )
    weight_variant = next(
        (
            weights
            for weights in grid.dense_sparse_weights
            if weights != (0.5, 0.5)
        ),
        None,
    )
    if hybrid is not None and weight_variant is not None:
        candidates.append(
            _candidate(
                hybrid,
                variant=f"weights_{weight_variant[0]:.1f}_{weight_variant[1]:.1f}",
                dense_weight=weight_variant[0],
                sparse_weight=weight_variant[1],
            )
        )
    rrf_variant = next((value for value in grid.rrf_k if value != 60), None)
    if hybrid is not None and rrf_variant is not None:
        candidates.append(
            _candidate(hybrid, variant=f"rrf{rrf_variant}", rrf_k=rrf_variant)
        )

    agentic_profiles = [
        profile
        for profile in manifest.retrieval_profiles
        if profile.strategy == "agentic_router"
    ]
    threshold_variants = [
        value for value in grid.agentic_sufficiency_threshold if value != 0.2
    ]
    for profile, threshold in zip(agentic_profiles, threshold_variants, strict=False):
        candidates.append(
            _candidate(
                profile,
                variant=f"sufficiency_{threshold:.2f}",
                agentic_sufficiency_threshold=threshold,
            )
        )

    graph = next(
        (
            profile
            for profile in manifest.retrieval_profiles
            if profile.strategy == "graph_postgres"
        ),
        None,
    )
    depth_variant = next((value for value in grid.graph_depth if value != 2), None)
    if graph is not None and depth_variant is not None:
        candidates.append(
            _candidate(graph, variant=f"depth{depth_variant}", graph_depth=depth_variant)
        )
    signal_variant = next(
        (value for value in grid.graph_router_signal_threshold if value != 0.5),
        None,
    )
    if graph is not None and signal_variant is not None:
        candidates.append(
            _candidate(
                graph,
                variant=f"graph_signal_{signal_variant:.1f}",
                graph_router_signal_threshold=signal_variant,
            )
        )
    return candidates[: grid.max_profiles]


def select_retrieval_finalists(
    results: Sequence[RetrievalScreenResult],
    *,
    limit: int = 3,
) -> list[str]:
    eligible = [result for result in results if result.pipeline_failure_count == 0]
    ranked = sorted(
        eligible,
        key=lambda result: (
            -result.recall_at_k,
            -result.mrr,
            result.no_context_rate,
            result.candidate_id,
        ),
    )
    return [result.candidate_id for result in ranked[:limit]]


def select_end_to_end_winner(results: Sequence[EndToEndResult]) -> str | None:
    if not results:
        return None
    return min(
        results,
        key=lambda result: (
            -result.grounded_answer_pass_rate,
            -result.citation_correctness,
            -result.answer_completeness,
            result.p95_latency_ms,
            result.candidate_id,
        ),
    ).candidate_id


def evaluate_local_accuracy_promotion_gate(
    values: PromotionGateInput,
) -> PromotionGateResult:
    delta_points = (
        values.candidate_mean_pass_rate - values.baseline_mean_pass_rate
    ) * 100.0
    reasons: list[str] = []
    if delta_points < 6.0:
        reasons.append("pass_rate_delta_below_6_points")
    guardrails: Mapping[str, tuple[float, float]] = {
        "unanswerable_accuracy_regressed": (
            values.baseline_unanswerable_accuracy,
            values.candidate_unanswerable_accuracy,
        ),
        "prompt_injection_resistance_regressed": (
            values.baseline_prompt_injection_resistance,
            values.candidate_prompt_injection_resistance,
        ),
        "citation_correctness_regressed": (
            values.baseline_citation_correctness,
            values.candidate_citation_correctness,
        ),
        "answer_completeness_regressed": (
            values.baseline_answer_completeness,
            values.candidate_answer_completeness,
        ),
    }
    for reason_code, (baseline, candidate) in guardrails.items():
        if candidate < baseline:
            reasons.append(reason_code)
    if values.pipeline_failure_count != 0:
        reasons.append("pipeline_failures_present")
    if values.candidate_p95_latency_ms > values.baseline_p95_latency_ms * 2.0:
        reasons.append("p95_latency_exceeds_2x_baseline")
    return PromotionGateResult(
        promote=not reasons,
        absolute_percentage_point_delta=round(delta_points, 6),
        reason_codes=tuple(reasons),
    )


def majority_vote_case_outcomes(
    outcomes: Sequence[RepeatCaseOutcome],
) -> dict[str, bool]:
    grouped: dict[str, list[bool]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.case_id, []).append(outcome.passed)
    majority: dict[str, bool] = {}
    for case_id, values in grouped.items():
        if len(values) != 3:
            raise ValueError("majority vote requires exactly three repeats per case")
        majority[case_id] = sum(values) >= 2
    return majority


def select_manual_review_case_ids(
    outcomes: Sequence[RepeatCaseOutcome],
    *,
    low_confidence_threshold: float = 0.6,
    stable_sample_rate: float = 0.15,
) -> set[tuple[str, int]]:
    selected: set[tuple[str, int]] = set()
    for outcome in outcomes:
        key = (outcome.case_id, outcome.repeat_number)
        if outcome.repeat_number == 1:
            selected.add(key)
            continue
        if (
            outcome.result_changed
            or outcome.hard_gate_failed
            or outcome.judge_human_disagreement
            or (
                outcome.judge_confidence is not None
                and outcome.judge_confidence < low_confidence_threshold
            )
            or _stable_case_sample(outcome.case_id, stable_sample_rate)
        ):
            selected.add(key)
    return selected


def _stable_case_sample(case_id: str, sample_rate: float) -> bool:
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample rate must be between zero and one")
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return bucket < sample_rate


def _candidate(
    profile: ExperimentRetrievalProfile,
    *,
    variant: str,
    top_k: int = 10,
    rerank_top_n: int = 3,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
    rrf_k: int = 60,
    agentic_sufficiency_threshold: float = 0.2,
    graph_depth: int = 2,
    graph_router_signal_threshold: float = 0.5,
) -> LocalAccuracyCandidate:
    return LocalAccuracyCandidate(
        candidate_id=f"{profile.profile_id}__{variant}",
        profile_id=profile.profile_id,
        embedding_model=profile.embedding_model,
        reranker_model=profile.reranker_model,
        strategy=profile.strategy,
        router_mode=profile.router_mode,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        dense_weight=dense_weight,
        sparse_weight=sparse_weight,
        rrf_k=rrf_k,
        agentic_sufficiency_threshold=agentic_sufficiency_threshold,
        graph_depth=graph_depth,
        graph_router_signal_threshold=graph_router_signal_threshold,
        supplemental=profile.supplemental,
    )
