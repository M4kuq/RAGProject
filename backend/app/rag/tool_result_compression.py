from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.context_budget import estimate_tokens
from app.rag.trace import TraceRedactor

TOOL_RESULT_COMPRESSION_SCHEMA_VERSION: Literal["phase2.tool_result_compression.v1"] = (
    "phase2.tool_result_compression.v1"
)
SEARCH_TOOL_NAMES = frozenset({"dense_search", "sparse_search", "hybrid_search"})

_WINDOWS_PATH_RE = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")
_UNC_PATH_RE = re.compile(r"\\\\[^\s]+")
_POSIX_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:^|\s)/(?:app/storage|storage|data|tmp|home|var/lib|Users|private|Volumes)(?:/|\s)"
)
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d ._-]{7,}\d)\b")
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


class ToolResultDropReason(StrEnum):
    MAX_ITEMS_LIMIT = "max_items_limit"
    MAX_TOTAL_ITEMS_LIMIT = "max_total_items_limit"
    MAX_TOKENS_LIMIT = "max_tokens_limit"
    MAX_TOTAL_TOKENS_LIMIT = "max_total_tokens_limit"
    EXACT_DUPLICATE_REMOVED = "exact_duplicate_removed"
    SAME_CHUNK_DEDUPED = "same_chunk_deduped"
    SAME_SOURCE_GROUPED = "same_source_grouped"
    LOW_SCORE_DROPPED = "low_score_dropped"
    OVERSIZED_REJECTED = "oversized_rejected"
    UNSAFE_REDACTED = "unsafe_redacted"
    REPEATED_RESULT = "repeated_result"
    MISSING_TEXT = "missing_text"
    UNKNOWN = "unknown"


class ToolResultCompressionMethod(StrEnum):
    NONE = "none"
    MAX_CHARS_PER_SNIPPET = "max_chars_per_snippet"
    EXACT_DUPLICATE_REMOVED = "exact_duplicate_removed"
    SAME_CHUNK_DEDUPED = "same_chunk_deduped"
    SAME_SOURCE_GROUPED = "same_source_grouped"
    LOW_SCORE_DROPPED = "low_score_dropped"
    UNSAFE_REDACTED = "unsafe_redacted"


DROP_REASON_VALUES = {reason.value for reason in ToolResultDropReason}
COMPRESSION_METHOD_VALUES = {method.value for method in ToolResultCompressionMethod}


@dataclass(frozen=True)
class ToolResultCandidate:
    tool_call_id: str
    tool_name: str
    document_chunk_id: int
    text: str | None
    source_label: str | None
    section_title: str | None
    page_from: int | None
    page_to: int | None
    rank: int | None
    retrieval_score: float | None
    rerank_score: float | None = None
    fusion_score: float | None = None
    citation_candidate: bool = True
    source_group_key: str | None = None


@dataclass(frozen=True)
class _PreparedToolResultCandidate:
    candidate: ToolResultCandidate
    clean_text: str
    normalized_text: str
    original_char_count: int
    estimated_tokens: int


class _ToolResultModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ToolResultCompressionPolicy(_ToolResultModel):
    enabled: bool = True
    max_items_per_tool: int = Field(default=8, ge=1, le=100)
    max_total_items_per_turn: int = Field(default=20, ge=1, le=200)
    max_snippet_chars: int = Field(default=500, ge=20, le=5000)
    max_tokens_per_tool: int = Field(default=1200, ge=1, le=200_000)
    max_total_tool_result_tokens: int = Field(default=3000, ge=1, le=200_000)
    drop_low_score_first: bool = True
    group_by_source: bool = True
    reject_oversized_output: bool = True
    store_debug_trace: bool = True
    token_estimator: Literal["heuristic"] = "heuristic"

    @model_validator(mode="after")
    def validate_policy(self) -> ToolResultCompressionPolicy:
        if self.max_items_per_tool > self.max_total_items_per_turn:
            raise ValueError("max_items_per_tool must be <= max_total_items_per_turn")
        if self.max_tokens_per_tool > self.max_total_tool_result_tokens:
            raise ValueError("max_tokens_per_tool must be <= max_total_tool_result_tokens")
        return self


class ToolResultItem(_ToolResultModel):
    tool_call_id: str
    tool_name: str
    retrieval_run_item_id: int | None = None
    document_chunk_id: int
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    rank: int | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    fusion_score: float | None = None
    citation_candidate: bool
    snippet: str = Field(exclude=True)
    snippet_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    original_char_count: int = Field(ge=0)
    snippet_char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    source_group_key: str
    compression_method: ToolResultCompressionMethod

    @field_validator(
        "tool_call_id",
        "tool_name",
        "source_label",
        "section_title",
        "source_group_key",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_tool_string(value, max_length=255)
        return safe or None

    def to_planner_payload(self) -> dict[str, object]:
        payload = {
            "tool_call_id": self.tool_call_id,
            "document_chunk_id": self.document_chunk_id,
            "source_label": self.source_label,
            "section_title": self.section_title,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "snippet": self.snippet,
            "retrieval_score": self.retrieval_score,
            "rerank_score": self.rerank_score,
            "fusion_score": self.fusion_score,
            "rank": self.rank,
            "citation_candidate": self.citation_candidate,
            "estimated_tokens": self.estimated_tokens,
            "source_group_key": self.source_group_key,
        }
        return TraceRedactor.safe_dict(payload)


class ToolResultItemRef(_ToolResultModel):
    tool_call_id: str
    tool_name: str
    retrieval_run_item_id: int | None = None
    document_chunk_id: int
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    rank: int | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    fusion_score: float | None = None
    citation_candidate: bool
    snippet_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    original_char_count: int = Field(ge=0)
    snippet_char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    source_group_key: str
    compression_method: ToolResultCompressionMethod

    @field_validator(
        "tool_call_id",
        "tool_name",
        "source_label",
        "section_title",
        "source_group_key",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_tool_string(value, max_length=255)
        return safe or None


class DroppedToolResultRef(_ToolResultModel):
    tool_call_id: str
    tool_name: str
    retrieval_run_item_id: int | None = None
    document_chunk_id: int | None = None
    source_label: str | None = None
    rank: int | None = None
    estimated_tokens: int = Field(ge=0)
    original_char_count: int = Field(ge=0)
    drop_reason: ToolResultDropReason

    @field_validator("tool_call_id", "tool_name", "source_label", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_tool_string(value, max_length=255)
        return safe or None


class CompressedToolResult(_ToolResultModel):
    tool_call_id: str
    tool_name: str
    status: Literal["succeeded", "failed"]
    original_item_count: int = Field(default=0, ge=0)
    output_item_count: int = Field(default=0, ge=0)
    dropped_item_count: int = Field(default=0, ge=0)
    estimated_tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    compression_ratio: float = Field(default=1.0, ge=0.0)
    items: list[ToolResultItem] = Field(default_factory=list)
    dropped_item_refs: list[DroppedToolResultRef] = Field(default_factory=list)
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    compression_methods: dict[str, int] = Field(default_factory=dict)
    budget_exhausted: bool = False
    repeated_result: bool = False
    oversized_rejected: bool = False
    error_code: str | None = None

    @field_validator("tool_call_id", "tool_name", "error_code", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_tool_string(value, max_length=255)
        return safe or None

    @field_validator("drop_reasons", mode="before")
    @classmethod
    def sanitize_drop_reasons(cls, value: object) -> object:
        return _safe_counter_mapping(value, DROP_REASON_VALUES)

    @field_validator("compression_methods", mode="before")
    @classmethod
    def sanitize_compression_methods(cls, value: object) -> object:
        return _safe_counter_mapping(value, COMPRESSION_METHOD_VALUES)

    def to_trace(self) -> ToolResultByToolTrace:
        return ToolResultByToolTrace(
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
            status=self.status,
            original_item_count=self.original_item_count,
            output_item_count=self.output_item_count,
            dropped_item_count=self.dropped_item_count,
            estimated_tokens_before=self.estimated_tokens_before,
            estimated_tokens_after=self.estimated_tokens_after,
            compression_ratio=self.compression_ratio,
            drop_reasons=self.drop_reasons,
            compression_methods=self.compression_methods,
            budget_exhausted=self.budget_exhausted,
            repeated_result=self.repeated_result,
            oversized_rejected=self.oversized_rejected,
            error_code=self.error_code,
        )


class ToolResultBudget(_ToolResultModel):
    max_items_per_tool: int = Field(ge=1)
    max_total_items_per_turn: int = Field(ge=1)
    max_snippet_chars: int = Field(ge=20)
    max_tokens_per_tool: int = Field(ge=1)
    max_total_tool_result_tokens: int = Field(ge=1)
    token_estimator: Literal["heuristic"] = "heuristic"
    drop_low_score_first: bool
    group_by_source: bool
    reject_oversized_output: bool


class ToolResultCompressionSummary(_ToolResultModel):
    tool_call_count: int = Field(ge=0)
    search_tool_call_count: int = Field(ge=0)
    original_item_count: int = Field(ge=0)
    output_item_count: int = Field(ge=0)
    dropped_item_count: int = Field(ge=0)
    estimated_tokens_before: int = Field(ge=0)
    estimated_tokens_after: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0)
    budget_exhausted: bool
    repeated_result_count: int = Field(ge=0)
    oversized_rejected_count: int = Field(ge=0)


class ToolResultByToolTrace(_ToolResultModel):
    tool_call_id: str
    tool_name: str
    status: Literal["succeeded", "failed"]
    original_item_count: int = Field(ge=0)
    output_item_count: int = Field(ge=0)
    dropped_item_count: int = Field(ge=0)
    estimated_tokens_before: int = Field(ge=0)
    estimated_tokens_after: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0)
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    compression_methods: dict[str, int] = Field(default_factory=dict)
    budget_exhausted: bool
    repeated_result: bool
    oversized_rejected: bool
    error_code: str | None = None

    @field_validator("tool_call_id", "tool_name", "error_code", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_tool_string(value, max_length=255)
        return safe or None

    @field_validator("drop_reasons", mode="before")
    @classmethod
    def sanitize_drop_reasons(cls, value: object) -> object:
        return _safe_counter_mapping(value, DROP_REASON_VALUES)

    @field_validator("compression_methods", mode="before")
    @classmethod
    def sanitize_compression_methods(cls, value: object) -> object:
        return _safe_counter_mapping(value, COMPRESSION_METHOD_VALUES)


class ToolResultCompressionTrace(_ToolResultModel):
    schema_version: Literal["phase2.tool_result_compression.v1"] = (
        TOOL_RESULT_COMPRESSION_SCHEMA_VERSION
    )
    enabled: bool
    budget: ToolResultBudget
    summary: ToolResultCompressionSummary
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    by_tool: list[ToolResultByToolTrace] = Field(default_factory=list)
    item_refs: list[ToolResultItemRef] = Field(default_factory=list)
    dropped_item_refs: list[DroppedToolResultRef] = Field(default_factory=list)

    @field_validator("drop_reasons", mode="before")
    @classmethod
    def sanitize_drop_reasons(cls, value: object) -> object:
        return _safe_counter_mapping(value, DROP_REASON_VALUES)


class ToolResultBudgetManager:
    def __init__(self, policy: ToolResultCompressionPolicy) -> None:
        self.policy = policy
        self._consumed_items = 0
        self._consumed_tokens = 0
        self._results: list[CompressedToolResult] = []
        self._seen_result_signatures: set[str] = set()

    @property
    def consumed_items(self) -> int:
        return self._consumed_items

    @property
    def consumed_tokens(self) -> int:
        return self._consumed_tokens

    @property
    def remaining_items(self) -> int:
        return max(0, self.policy.max_total_items_per_turn - self._consumed_items)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.policy.max_total_tool_result_tokens - self._consumed_tokens)

    def is_repeated_result(self, items: list[ToolResultItem]) -> bool:
        if not items:
            return False
        signature = _result_signature(items)
        return signature in self._seen_result_signatures

    def record(self, result: CompressedToolResult) -> None:
        if result.items and not result.repeated_result:
            self._seen_result_signatures.add(_result_signature(result.items))
        self._consumed_items += result.output_item_count
        self._consumed_tokens += result.estimated_tokens_after
        self._results.append(result)

    def trace(self) -> ToolResultCompressionTrace:
        drop_counts: Counter[str] = Counter()
        item_refs: list[ToolResultItemRef] = []
        dropped_refs: list[DroppedToolResultRef] = []
        for result in self._results:
            drop_counts.update(result.drop_reasons)
            item_refs.extend(_item_ref(item) for item in result.items)
            dropped_refs.extend(result.dropped_item_refs)
        tokens_before = sum(result.estimated_tokens_before for result in self._results)
        tokens_after = sum(result.estimated_tokens_after for result in self._results)
        return ToolResultCompressionTrace(
            enabled=self.policy.enabled,
            budget=ToolResultBudget(
                max_items_per_tool=self.policy.max_items_per_tool,
                max_total_items_per_turn=self.policy.max_total_items_per_turn,
                max_snippet_chars=self.policy.max_snippet_chars,
                max_tokens_per_tool=self.policy.max_tokens_per_tool,
                max_total_tool_result_tokens=self.policy.max_total_tool_result_tokens,
                token_estimator=self.policy.token_estimator,
                drop_low_score_first=self.policy.drop_low_score_first,
                group_by_source=self.policy.group_by_source,
                reject_oversized_output=self.policy.reject_oversized_output,
            ),
            summary=ToolResultCompressionSummary(
                tool_call_count=len(self._results),
                search_tool_call_count=sum(
                    1 for result in self._results if result.tool_name in SEARCH_TOOL_NAMES
                ),
                original_item_count=sum(result.original_item_count for result in self._results),
                output_item_count=sum(result.output_item_count for result in self._results),
                dropped_item_count=sum(result.dropped_item_count for result in self._results),
                estimated_tokens_before=tokens_before,
                estimated_tokens_after=tokens_after,
                compression_ratio=_compression_ratio(tokens_after, tokens_before),
                budget_exhausted=any(result.budget_exhausted for result in self._results),
                repeated_result_count=sum(1 for result in self._results if result.repeated_result),
                oversized_rejected_count=sum(
                    1 for result in self._results if result.oversized_rejected
                ),
            ),
            drop_reasons=dict(drop_counts),
            by_tool=[result.to_trace() for result in self._results],
            item_refs=item_refs,
            dropped_item_refs=dropped_refs[:200],
        )


class ToolResultCompressor:
    def compress(
        self,
        candidates: list[ToolResultCandidate],
        *,
        policy: ToolResultCompressionPolicy,
        budget_manager: ToolResultBudgetManager,
        tool_call_id: str,
        tool_name: str,
    ) -> CompressedToolResult:
        prepared = [_prepare_candidate(candidate) for candidate in candidates]
        if policy.drop_low_score_first:
            prepared = sorted(prepared, key=_candidate_order)
        token_count_before = sum(candidate.estimated_tokens for candidate in prepared)
        drop_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        dropped_refs: list[DroppedToolResultRef] = []
        accepted: list[ToolResultItem] = []
        seen_chunks: set[int] = set()
        seen_text: set[str] = set()
        seen_sources: set[str] = set()
        per_tool_tokens = 0
        budget_exhausted = False
        token_limit_drop = False

        for prepared_candidate in prepared:
            candidate = prepared_candidate.candidate
            if not prepared_candidate.clean_text:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.MISSING_TEXT,
                )
                continue
            if candidate.document_chunk_id in seen_chunks:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.SAME_CHUNK_DEDUPED,
                )
                continue
            if prepared_candidate.normalized_text in seen_text:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.EXACT_DUPLICATE_REMOVED,
                )
                continue
            if len(accepted) >= policy.max_items_per_tool:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.MAX_ITEMS_LIMIT,
                )
                budget_exhausted = True
                continue
            if len(accepted) >= budget_manager.remaining_items:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.MAX_TOTAL_ITEMS_LIMIT,
                )
                budget_exhausted = True
                continue

            source_key = _source_group_key(candidate)
            snippet = _safe_snippet(
                prepared_candidate.clean_text,
                max_chars=policy.max_snippet_chars,
            )
            method = ToolResultCompressionMethod.NONE
            if snippet == "redacted":
                method = ToolResultCompressionMethod.UNSAFE_REDACTED
            elif len(snippet) < prepared_candidate.original_char_count:
                method = ToolResultCompressionMethod.MAX_CHARS_PER_SNIPPET
            elif policy.group_by_source and source_key in seen_sources:
                method = ToolResultCompressionMethod.SAME_SOURCE_GROUPED
            item_tokens = estimate_tokens(snippet)
            if per_tool_tokens + item_tokens > policy.max_tokens_per_tool:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.MAX_TOKENS_LIMIT,
                )
                budget_exhausted = True
                token_limit_drop = True
                continue
            if per_tool_tokens + item_tokens > budget_manager.remaining_tokens:
                _drop(
                    dropped_refs,
                    drop_counts,
                    prepared_candidate,
                    ToolResultDropReason.MAX_TOTAL_TOKENS_LIMIT,
                )
                budget_exhausted = True
                token_limit_drop = True
                continue

            seen_chunks.add(candidate.document_chunk_id)
            seen_text.add(prepared_candidate.normalized_text)
            seen_sources.add(source_key)
            per_tool_tokens += item_tokens
            method_counts[method.value] += 1
            accepted.append(
                ToolResultItem(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    document_chunk_id=candidate.document_chunk_id,
                    source_label=candidate.source_label,
                    section_title=candidate.section_title,
                    page_from=candidate.page_from,
                    page_to=candidate.page_to,
                    rank=candidate.rank,
                    retrieval_score=_rounded(candidate.retrieval_score),
                    rerank_score=_rounded(candidate.rerank_score),
                    fusion_score=_rounded(candidate.fusion_score),
                    citation_candidate=candidate.citation_candidate,
                    snippet=snippet,
                    snippet_hash=_sha256(snippet),
                    original_char_count=prepared_candidate.original_char_count,
                    snippet_char_count=len(snippet),
                    estimated_tokens=item_tokens,
                    source_group_key=source_key,
                    compression_method=method,
                )
            )

        repeated_result = budget_manager.is_repeated_result(accepted)
        if repeated_result:
            for item in accepted:
                dropped_refs.append(_drop_ref_for_item(item, ToolResultDropReason.REPEATED_RESULT))
            drop_counts[ToolResultDropReason.REPEATED_RESULT.value] += len(accepted)
            accepted = []
            per_tool_tokens = 0
            budget_exhausted = True

        oversized_rejected = (
            policy.reject_oversized_output and not accepted and bool(prepared) and token_limit_drop
        )
        status: Literal["succeeded", "failed"] = "failed" if oversized_rejected else "succeeded"
        error_code = "oversized_tool_output" if oversized_rejected else None
        if oversized_rejected:
            drop_counts[ToolResultDropReason.OVERSIZED_REJECTED.value] += max(1, len(prepared))

        result = CompressedToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            original_item_count=len(prepared),
            output_item_count=len(accepted),
            dropped_item_count=max(0, len(prepared) - len(accepted)),
            estimated_tokens_before=token_count_before,
            estimated_tokens_after=per_tool_tokens,
            compression_ratio=_compression_ratio(per_tool_tokens, token_count_before),
            items=accepted,
            dropped_item_refs=dropped_refs[:200],
            drop_reasons=dict(drop_counts),
            compression_methods=dict(method_counts),
            budget_exhausted=budget_exhausted,
            repeated_result=repeated_result,
            oversized_rejected=oversized_rejected,
            error_code=error_code,
        )
        budget_manager.record(result)
        return result

    def error_result(
        self,
        *,
        policy: ToolResultCompressionPolicy,
        budget_manager: ToolResultBudgetManager,
        tool_call_id: str,
        tool_name: str,
        error_code: str,
    ) -> CompressedToolResult:
        del policy
        result = CompressedToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="failed",
            error_code=TraceRedactor.safe_string(error_code, max_length=100) or "tool_error",
        )
        budget_manager.record(result)
        return result


class OrchestratorContextGuard:
    def safe_trace_summary(self, value: dict[str, object]) -> dict[str, object]:
        allowed = {
            "retrieval_run_id",
            "strategy_type",
            "status",
            "latency_summary",
            "tool_result_compression",
        }
        safe = {key: nested for key, nested in value.items() if key in allowed}
        return TraceRedactor.safe_dict(safe)

    def safe_log_payload(self, payload: dict[str, object]) -> dict[str, object]:
        allowed = {
            "request_id",
            "retrieval_run_id",
            "strategy_type",
            "tool_name",
            "tool_call_id",
            "original_item_count",
            "output_item_count",
            "dropped_item_count",
            "estimated_tokens_before",
            "estimated_tokens_after",
            "compression_ratio",
            "drop_reason_counts",
            "budget_exhausted",
        }
        safe = {key: value for key, value in payload.items() if key in allowed}
        return TraceRedactor.safe_dict(safe)


def sanitize_tool_result_compression_json(
    value: dict[str, Any] | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        trace = ToolResultCompressionTrace.model_validate(value)
    except ValueError:
        return None
    return trace.model_dump(mode="json", exclude_none=True)


def attach_retrieval_run_item_ids(
    value: dict[str, Any] | None,
    *,
    item_id_by_chunk_id: dict[int, int],
) -> dict[str, object] | None:
    safe = sanitize_tool_result_compression_json(value)
    if safe is None:
        return None
    for key in ("item_refs", "dropped_item_refs"):
        refs = safe.get(key)
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            chunk_id = ref.get("document_chunk_id")
            if isinstance(chunk_id, int) and ref.get("retrieval_run_item_id") is None:
                item_id = item_id_by_chunk_id.get(chunk_id)
                if item_id is not None:
                    ref["retrieval_run_item_id"] = item_id
    return sanitize_tool_result_compression_json(safe)


def _prepare_candidate(candidate: ToolResultCandidate) -> _PreparedToolResultCandidate:
    clean_text = _clean_text(candidate.text)
    return _PreparedToolResultCandidate(
        candidate=candidate,
        clean_text=clean_text,
        normalized_text=_normalize_text(clean_text),
        original_char_count=len(clean_text),
        estimated_tokens=estimate_tokens(clean_text),
    )


def _candidate_order(candidate: _PreparedToolResultCandidate) -> tuple[int, float, int]:
    rank = candidate.candidate.rank or 1_000_000
    score = candidate.candidate.rerank_score
    if score is None:
        score = candidate.candidate.fusion_score
    if score is None:
        score = candidate.candidate.retrieval_score
    return (rank, -(score or 0.0), candidate.candidate.document_chunk_id)


def _drop(
    dropped_refs: list[DroppedToolResultRef],
    drop_counts: Counter[str],
    candidate: _PreparedToolResultCandidate,
    reason: ToolResultDropReason,
) -> None:
    drop_counts[reason.value] += 1
    dropped_refs.append(
        DroppedToolResultRef(
            tool_call_id=candidate.candidate.tool_call_id,
            tool_name=candidate.candidate.tool_name,
            document_chunk_id=candidate.candidate.document_chunk_id,
            source_label=candidate.candidate.source_label,
            rank=candidate.candidate.rank,
            estimated_tokens=candidate.estimated_tokens,
            original_char_count=candidate.original_char_count,
            drop_reason=reason,
        )
    )


def _drop_ref_for_item(item: ToolResultItem, reason: ToolResultDropReason) -> DroppedToolResultRef:
    return DroppedToolResultRef(
        tool_call_id=item.tool_call_id,
        tool_name=item.tool_name,
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        source_label=item.source_label,
        rank=item.rank,
        estimated_tokens=item.estimated_tokens,
        original_char_count=item.original_char_count,
        drop_reason=reason,
    )


def _item_ref(item: ToolResultItem) -> ToolResultItemRef:
    return ToolResultItemRef(
        tool_call_id=item.tool_call_id,
        tool_name=item.tool_name,
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        source_label=item.source_label,
        section_title=item.section_title,
        page_from=item.page_from,
        page_to=item.page_to,
        rank=item.rank,
        retrieval_score=item.retrieval_score,
        rerank_score=item.rerank_score,
        fusion_score=item.fusion_score,
        citation_candidate=item.citation_candidate,
        snippet_hash=item.snippet_hash,
        original_char_count=item.original_char_count,
        snippet_char_count=item.snippet_char_count,
        estimated_tokens=item.estimated_tokens,
        source_group_key=item.source_group_key,
        compression_method=item.compression_method,
    )


def _result_signature(items: list[ToolResultItem]) -> str:
    parts = [
        f"{item.document_chunk_id}:{item.snippet_hash}:{item.rank or 0}"
        for item in sorted(items, key=lambda item: (item.rank or 1_000_000, item.document_chunk_id))
    ]
    return _sha256("|".join(parts))


def _safe_tool_string(value: str, *, max_length: int) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    if (
        _WINDOWS_PATH_RE.search(normalized)
        or _UNC_PATH_RE.search(normalized)
        or _POSIX_PRIVATE_PATH_RE.search(normalized)
        or _PHONE_RE.search(normalized)
    ):
        return "redacted"
    safe = TraceRedactor.safe_string(normalized, max_length=max_length)
    return safe or "redacted"


def _safe_snippet(value: str, *, max_chars: int) -> str:
    safe = _safe_tool_string(value, max_length=max_chars)
    if not safe:
        return "redacted"
    return safe[:max_chars]


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\x00", " ").split())


def _normalize_text(value: str) -> str:
    return " ".join(token.lower() for token in _TOKEN_RE.findall(value))[:4000]


def _source_group_key(candidate: ToolResultCandidate) -> str:
    if candidate.source_group_key:
        return _safe_tool_string(candidate.source_group_key, max_length=255)
    return f"chunk:{candidate.document_chunk_id}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _compression_ratio(output_value: int, input_value: int) -> float:
    if input_value <= 0:
        return 0.0
    return round(output_value / input_value, 6)


def _safe_counter_mapping(value: object, allowed_values: set[str]) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key, count in value.items():
        key_text = str(key)
        if key_text not in allowed_values:
            continue
        if isinstance(count, bool) or not isinstance(count, int):
            continue
        safe[key_text] = max(0, count)
    return safe
