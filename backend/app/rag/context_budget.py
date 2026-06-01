from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.trace import TraceRedactor

CONTEXT_BUDGET_SCHEMA_VERSION: Literal["phase2.context_budget.v1"] = "phase2.context_budget.v1"


class ContextDropReason(StrEnum):
    OVER_BUDGET = "over_budget"
    MAX_ITEMS_EXCEEDED = "max_items_exceeded"
    LOW_SCORE = "low_score"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_CHUNK = "duplicate_chunk"
    MISSING_TEXT = "missing_text"
    UNSAFE_CONTENT = "unsafe_content"
    NOT_SELECTED_BY_RERANK = "not_selected_by_rerank"
    SOURCE_DIVERSITY_LIMIT = "source_diversity_limit"
    UNKNOWN = "unknown"


DROP_REASON_VALUES = {reason.value for reason in ContextDropReason}
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")


@dataclass(frozen=True)
class ContextBudgetCandidate:
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str | None
    section_title: str | None
    page_from: int | None
    page_to: int | None
    score: float | None
    rank: int | None
    rerank_score: float | None
    rerank_order: int | None
    text: str | None
    citation_candidate: bool
    source_group_key: str
    retrieval_source: str | None = None


class _ContextBudgetModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ContextBudgetPolicy(_ContextBudgetModel):
    enabled: bool = True
    max_context_tokens: int = Field(default=6000, ge=1, le=200_000)
    reserve_answer_tokens: int = Field(default=1000, ge=0, le=200_000)
    max_context_items: int = Field(default=12, ge=1, le=100)
    max_tokens_per_item: int = Field(default=1200, ge=1, le=200_000)
    min_citation_candidates: int = Field(default=1, ge=0, le=100)
    drop_low_score_first: bool = True
    preserve_source_diversity: bool = True
    token_estimator: Literal["heuristic"] = "heuristic"
    store_debug_trace: bool = True

    @model_validator(mode="after")
    def validate_budget(self) -> ContextBudgetPolicy:
        if self.max_tokens_per_item > self.max_context_tokens:
            raise ValueError("max_tokens_per_item must be <= max_context_tokens")
        if self.min_citation_candidates > self.max_context_items:
            raise ValueError("min_citation_candidates must be <= max_context_items")
        return self


class ContextItem(_ContextBudgetModel):
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    score: float | None = None
    rank: int | None = None
    rerank_score: float | None = None
    rerank_order: int | None = None
    char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    citation_candidate: bool
    source_group_key: str
    retrieval_source: str | None = None
    selected: bool = False
    reason: str | None = None
    drop_reason: ContextDropReason | None = None

    @field_validator(
        "source_label",
        "section_title",
        "source_group_key",
        "retrieval_source",
        "reason",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_context_string(value, max_length=255)
        return safe or None


class ContextBudgetItemRef(_ContextBudgetModel):
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    score: float | None = None
    rank: int | None = None
    rerank_score: float | None = None
    rerank_order: int | None = None
    estimated_tokens: int = Field(ge=0)
    char_count: int = Field(ge=0)
    retrieval_source: str | None = None
    reason: str | None = None
    drop_reason: ContextDropReason | None = None

    @field_validator(
        "source_label",
        "section_title",
        "retrieval_source",
        "reason",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_context_string(value, max_length=255)
        return safe or None


class ContextBudgetBudget(_ContextBudgetModel):
    max_context_tokens: int = Field(ge=1)
    reserve_answer_tokens: int = Field(ge=0)
    max_context_items: int = Field(ge=1)
    max_tokens_per_item: int = Field(ge=1)
    min_citation_candidates: int = Field(ge=0)
    token_estimator: Literal["heuristic"] = "heuristic"
    preserve_source_diversity: bool
    drop_low_score_first: bool


class ContextBudgetUsage(_ContextBudgetModel):
    estimated_prompt_tokens: int = Field(ge=0)
    estimated_context_tokens: int = Field(ge=0)
    estimated_total_input_tokens: int = Field(ge=0)
    reserve_answer_tokens: int = Field(ge=0)
    remaining_context_tokens: int = Field(ge=0)
    budget_exhausted: bool


class ContextBudgetItemsSummary(_ContextBudgetModel):
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    dropped_count: int = Field(ge=0)
    citation_candidate_count: int = Field(ge=0)
    source_count: int = Field(ge=0)


class ContextBudgetSourceRef(_ContextBudgetModel):
    source_group_key: str
    source_label: str | None = None
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    dropped_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)

    @field_validator("source_group_key", "source_label", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_context_string(value, max_length=255)
        return safe or None


class ContextBudgetSourcesSummary(_ContextBudgetModel):
    source_count: int = Field(ge=0)
    by_source: list[ContextBudgetSourceRef] = Field(default_factory=list)


class ContextBudgetStrategySummary(_ContextBudgetModel):
    strategy_type: str | None = None
    selected_strategy: str | None = None
    execution_strategy: str | None = None
    tools_used: list[str] = Field(default_factory=list)

    @field_validator(
        "strategy_type",
        "selected_strategy",
        "execution_strategy",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_context_string(value, max_length=100)
        return safe or None

    @field_validator("tools_used", mode="before")
    @classmethod
    def sanitize_tools(cls, value: object) -> object:
        if not isinstance(value, list):
            return []
        return [
            safe
            for item in value
            if isinstance(item, str)
            for safe in [_safe_context_string(item, max_length=100)]
            if safe
        ]


class ContextBudgetTrace(_ContextBudgetModel):
    schema_version: Literal["phase2.context_budget.v1"] = CONTEXT_BUDGET_SCHEMA_VERSION
    enabled: bool
    budget: ContextBudgetBudget
    usage: ContextBudgetUsage
    items: ContextBudgetItemsSummary
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    sources: ContextBudgetSourcesSummary
    strategy: ContextBudgetStrategySummary | None = None
    selected_item_refs: list[ContextBudgetItemRef] = Field(default_factory=list)
    dropped_item_refs: list[ContextBudgetItemRef] = Field(default_factory=list)

    @field_validator("drop_reasons", mode="before")
    @classmethod
    def sanitize_drop_reasons(cls, value: object) -> object:
        if not isinstance(value, dict):
            return {}
        safe: dict[str, int] = {}
        for key, count in value.items():
            key_text = str(key)
            if key_text not in DROP_REASON_VALUES:
                continue
            if isinstance(count, bool) or not isinstance(count, int):
                continue
            safe[key_text] = max(0, count)
        return safe


class ContextBudgetDecision(_ContextBudgetModel):
    selected_item_ids: list[int]
    trace: ContextBudgetTrace
    items: list[ContextItem]


class ContextBudgetManager:
    def apply(
        self,
        candidates: list[ContextBudgetCandidate],
        *,
        policy: ContextBudgetPolicy,
        estimated_prompt_tokens: int = 0,
        strategy: ContextBudgetStrategySummary | None = None,
    ) -> ContextBudgetDecision:
        items = [_context_item(candidate) for candidate in candidates]
        if not policy.enabled:
            selected = [
                item.retrieval_run_item_id
                for item in items
                if item.citation_candidate and item.char_count > 0
            ]
            decided_items = [
                item.model_copy(
                    update={
                        "selected": item.retrieval_run_item_id in selected,
                        "reason": "budget_disabled"
                        if item.retrieval_run_item_id in selected
                        else None,
                        "drop_reason": None
                        if item.retrieval_run_item_id in selected
                        else ContextDropReason.NOT_SELECTED_BY_RERANK,
                    }
                )
                for item in items
            ]
            return _decision(
                decided_items,
                policy=policy,
                estimated_prompt_tokens=estimated_prompt_tokens,
                strategy=strategy,
                budget_exhausted=False,
            )

        decided_items, budget_exhausted = self._select_items(items, policy)
        return _decision(
            decided_items,
            policy=policy,
            estimated_prompt_tokens=estimated_prompt_tokens,
            strategy=strategy,
            budget_exhausted=budget_exhausted,
        )

    def _select_items(
        self,
        items: list[ContextItem],
        policy: ContextBudgetPolicy,
    ) -> tuple[list[ContextItem], bool]:
        decided: list[ContextItem] = []
        eligible_indices: list[int] = []
        seen_chunks: set[int] = set()

        for index, item in enumerate(items):
            drop_reason: ContextDropReason | None = None
            if item.document_chunk_id in seen_chunks:
                drop_reason = ContextDropReason.DUPLICATE_CHUNK
            elif item.char_count <= 0:
                drop_reason = ContextDropReason.MISSING_TEXT
            elif not item.citation_candidate:
                drop_reason = ContextDropReason.NOT_SELECTED_BY_RERANK
            elif item.estimated_tokens > policy.max_tokens_per_item:
                drop_reason = ContextDropReason.OVER_BUDGET
            else:
                eligible_indices.append(index)
            seen_chunks.add(item.document_chunk_id)
            decided.append(item.model_copy(update={"drop_reason": drop_reason}))

        ordered_indices = _selection_order(decided, eligible_indices, policy=policy)
        selected_count = 0
        selected_tokens = 0
        budget_exhausted = False
        source_diversity_first = _first_indices_by_source(decided, ordered_indices)

        for index in ordered_indices:
            item = decided[index]
            if item.drop_reason is not None:
                continue
            if selected_count >= policy.max_context_items:
                decided[index] = item.model_copy(
                    update={"drop_reason": ContextDropReason.MAX_ITEMS_EXCEEDED}
                )
                continue
            if selected_tokens + item.estimated_tokens > policy.max_context_tokens:
                budget_exhausted = True
                decided[index] = item.model_copy(
                    update={"drop_reason": ContextDropReason.OVER_BUDGET}
                )
                continue
            reason = (
                "source_diversity"
                if policy.preserve_source_diversity and index in source_diversity_first
                else "high_score"
            )
            selected_count += 1
            selected_tokens += item.estimated_tokens
            decided[index] = item.model_copy(
                update={"selected": True, "reason": reason, "drop_reason": None}
            )

        return decided, budget_exhausted


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(math.ceil(len(text) / 4))


def sanitize_context_budget_json(value: dict[str, Any] | None) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        trace = ContextBudgetTrace.model_validate(value)
    except ValueError:
        return None
    return trace.model_dump(mode="json", exclude_none=True)


def _context_item(candidate: ContextBudgetCandidate) -> ContextItem:
    text = _clean_text(candidate.text)
    return ContextItem(
        retrieval_run_item_id=candidate.retrieval_run_item_id,
        document_chunk_id=candidate.document_chunk_id,
        source_label=candidate.source_label,
        section_title=candidate.section_title,
        page_from=candidate.page_from,
        page_to=candidate.page_to,
        score=_rounded(candidate.score),
        rank=candidate.rank,
        rerank_score=_rounded(candidate.rerank_score),
        rerank_order=candidate.rerank_order,
        char_count=len(text),
        estimated_tokens=estimate_tokens(text),
        citation_candidate=candidate.citation_candidate,
        source_group_key=candidate.source_group_key,
        retrieval_source=candidate.retrieval_source,
    )


def _decision(
    items: list[ContextItem],
    *,
    policy: ContextBudgetPolicy,
    estimated_prompt_tokens: int,
    strategy: ContextBudgetStrategySummary | None,
    budget_exhausted: bool,
) -> ContextBudgetDecision:
    selected = [item for item in items if item.selected]
    dropped = [item for item in items if not item.selected]
    selected_tokens = sum(item.estimated_tokens for item in selected)
    drop_reasons = Counter(
        (item.drop_reason or ContextDropReason.UNKNOWN).value for item in dropped
    )
    source_summary = _source_summary(items)
    trace = ContextBudgetTrace(
        enabled=policy.enabled,
        budget=ContextBudgetBudget(
            max_context_tokens=policy.max_context_tokens,
            reserve_answer_tokens=policy.reserve_answer_tokens,
            max_context_items=policy.max_context_items,
            max_tokens_per_item=policy.max_tokens_per_item,
            min_citation_candidates=policy.min_citation_candidates,
            token_estimator=policy.token_estimator,
            preserve_source_diversity=policy.preserve_source_diversity,
            drop_low_score_first=policy.drop_low_score_first,
        ),
        usage=ContextBudgetUsage(
            estimated_prompt_tokens=max(0, estimated_prompt_tokens),
            estimated_context_tokens=selected_tokens,
            estimated_total_input_tokens=max(0, estimated_prompt_tokens) + selected_tokens,
            reserve_answer_tokens=policy.reserve_answer_tokens,
            remaining_context_tokens=max(0, policy.max_context_tokens - selected_tokens),
            budget_exhausted=budget_exhausted or bool(drop_reasons.get("over_budget")),
        ),
        items=ContextBudgetItemsSummary(
            candidate_count=len(items),
            selected_count=len(selected),
            dropped_count=len(dropped),
            citation_candidate_count=sum(1 for item in items if item.citation_candidate),
            source_count=len({item.source_group_key for item in selected}),
        ),
        drop_reasons=dict(drop_reasons),
        sources=source_summary,
        strategy=strategy,
        selected_item_refs=[_item_ref(item) for item in selected],
        dropped_item_refs=[_item_ref(item) for item in dropped],
    )
    return ContextBudgetDecision(
        selected_item_ids=[item.retrieval_run_item_id for item in selected],
        trace=trace,
        items=items,
    )


def _selection_order(
    items: list[ContextItem],
    eligible_indices: list[int],
    *,
    policy: ContextBudgetPolicy,
) -> list[int]:
    ordered = list(eligible_indices)
    if policy.drop_low_score_first:
        ordered.sort(key=lambda index: _score_order(items[index]))
    if not policy.preserve_source_diversity:
        return ordered

    first_by_source: list[int] = []
    rest: list[int] = []
    seen_sources: set[str] = set()
    for index in ordered:
        source = items[index].source_group_key
        if source not in seen_sources:
            seen_sources.add(source)
            first_by_source.append(index)
        else:
            rest.append(index)
    return first_by_source + rest


def _first_indices_by_source(items: list[ContextItem], ordered_indices: list[int]) -> set[int]:
    first: set[int] = set()
    seen: set[str] = set()
    for index in ordered_indices:
        source = items[index].source_group_key
        if source in seen:
            continue
        seen.add(source)
        first.add(index)
    return first


def _score_order(item: ContextItem) -> tuple[int, float, int, int]:
    rank = item.rerank_order or item.rank or 1_000_000
    score = item.rerank_score if item.rerank_score is not None else item.score
    return (rank, -(score or 0.0), item.rank or 1_000_000, item.document_chunk_id)


def _source_summary(items: list[ContextItem]) -> ContextBudgetSourcesSummary:
    grouped: dict[str, list[ContextItem]] = defaultdict(list)
    for item in items:
        grouped[item.source_group_key].append(item)
    refs = [
        ContextBudgetSourceRef(
            source_group_key=source,
            source_label=next(
                (item.source_label for item in grouped_items if item.source_label), None
            ),
            candidate_count=len(grouped_items),
            selected_count=sum(1 for item in grouped_items if item.selected),
            dropped_count=sum(1 for item in grouped_items if not item.selected),
            estimated_tokens=sum(item.estimated_tokens for item in grouped_items if item.selected),
        )
        for source, grouped_items in sorted(grouped.items())
    ]
    return ContextBudgetSourcesSummary(source_count=len(refs), by_source=refs)


def _item_ref(item: ContextItem) -> ContextBudgetItemRef:
    return ContextBudgetItemRef(
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        source_label=item.source_label,
        section_title=item.section_title,
        page_from=item.page_from,
        page_to=item.page_to,
        score=item.score,
        rank=item.rank,
        rerank_score=item.rerank_score,
        rerank_order=item.rerank_order,
        estimated_tokens=item.estimated_tokens,
        char_count=item.char_count,
        retrieval_source=item.retrieval_source,
        reason=item.reason,
        drop_reason=item.drop_reason,
    )


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return " ".join(text.replace("\x00", " ").split())


def _safe_context_string(value: str, *, max_length: int) -> str:
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    path_normalized = safe.replace("\\", "/")
    if path_normalized.startswith(("/", "//")) or _WINDOWS_PATH_RE.search(path_normalized):
        return "redacted"
    return safe


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), 6)
