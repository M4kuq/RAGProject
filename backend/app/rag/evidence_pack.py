from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.context_budget import estimate_tokens
from app.rag.generation import GenerationContextItem
from app.rag.trace import TraceRedactor

EVIDENCE_PACK_SCHEMA_VERSION: Literal["phase2.context_compression.v1"] = (
    "phase2.context_compression.v1"
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


class CompressionDropReason(StrEnum):
    EXACT_DUPLICATE_REMOVED = "exact_duplicate_removed"
    NORMALIZED_DUPLICATE_REMOVED = "normalized_duplicate_removed"
    NEAR_DUPLICATE_REMOVED = "near_duplicate_removed"
    MAX_ITEMS_PER_SOURCE = "max_items_per_source"
    MAX_ITEMS_EXCEEDED = "max_items_exceeded"
    MAX_TOTAL_CHARS = "max_total_chars"
    MISSING_TEXT = "missing_text"
    UNKNOWN = "unknown"


class CompressionMethod(StrEnum):
    NONE = "none"
    BOUNDED_EXCERPT = "bounded_excerpt"
    SOURCE_GROUPED = "source_grouped"


DROP_REASON_VALUES = {reason.value for reason in CompressionDropReason}
COMPRESSION_METHOD_VALUES = {method.value for method in CompressionMethod}


@dataclass(frozen=True)
class EvidenceCandidate:
    retrieval_run_item_id: int
    document_chunk_id: int
    local_citation_id: int
    text: str | None
    source_label: str | None
    section_title: str | None
    page_from: int | None
    page_to: int | None
    score: float | None
    rerank_score: float | None
    rank: int | None
    rerank_order: int | None
    source_group_key: str
    citation_candidate: bool
    retrieval_source: str | None = None
    logical_document_id: int | None = None
    document_version_id: int | None = None


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EvidencePackPolicy(_EvidenceModel):
    enabled: bool = True
    max_items: int = Field(default=12, ge=1, le=100)
    max_items_per_source: int = Field(default=4, ge=1, le=100)
    max_chars_per_item: int = Field(default=1200, ge=20, le=50_000)
    max_total_chars: int = Field(default=12_000, ge=20, le=200_000)
    near_duplicate_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    preserve_citation_candidates: bool = True
    group_by_source: bool = True
    store_debug_trace: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> EvidencePackPolicy:
        if self.max_items_per_source > self.max_items:
            raise ValueError("max_items_per_source must be <= max_items")
        return self


class EvidenceItem(_EvidenceModel):
    evidence_item_id: str
    retrieval_run_item_id: int
    document_chunk_id: int
    local_citation_id: int = Field(ge=1)
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    score: float | None = None
    rerank_score: float | None = None
    rank: int | None = None
    rerank_order: int | None = None
    source_group_key: str
    evidence_text_for_generation: str = Field(exclude=True)
    evidence_text_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    original_char_count: int = Field(ge=0)
    output_char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    citation_candidate: bool
    compression_method: CompressionMethod
    compression_reason: str | None = None
    retrieval_source: str | None = None
    logical_document_id: int | None = None
    document_version_id: int | None = None

    @field_validator(
        "evidence_item_id",
        "source_label",
        "section_title",
        "source_group_key",
        "compression_reason",
        "retrieval_source",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_evidence_string(value, max_length=255)
        return safe or None


class DroppedEvidenceRef(_EvidenceModel):
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str | None = None
    rank: int | None = None
    rerank_order: int | None = None
    estimated_tokens: int = Field(ge=0)
    original_char_count: int = Field(ge=0)
    drop_reason: CompressionDropReason

    @field_validator("source_label", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_evidence_string(value, max_length=255)
        return safe or None


class EvidenceItemRef(_EvidenceModel):
    evidence_item_id: str
    retrieval_run_item_id: int
    document_chunk_id: int
    local_citation_id: int = Field(ge=1)
    source_label: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    score: float | None = None
    rerank_score: float | None = None
    rank: int | None = None
    rerank_order: int | None = None
    source_group_key: str
    evidence_text_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    original_char_count: int = Field(ge=0)
    output_char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    citation_candidate: bool
    compression_method: CompressionMethod
    compression_reason: str | None = None
    retrieval_source: str | None = None

    @field_validator(
        "evidence_item_id",
        "source_label",
        "section_title",
        "source_group_key",
        "compression_reason",
        "retrieval_source",
        mode="before",
    )
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_evidence_string(value, max_length=255)
        return safe or None


class EvidenceGroup(_EvidenceModel):
    source_group_key: str
    source_label: str | None = None
    document_version_id: int | None = None
    logical_document_id: int | None = None
    item_count: int = Field(ge=0)
    selected_item_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    top_score: float | None = None
    evidence_item_refs: list[str] = Field(default_factory=list)

    @field_validator("source_group_key", "source_label", mode="before")
    @classmethod
    def sanitize_string(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        safe = _safe_evidence_string(value, max_length=255)
        return safe or None


class EvidencePackInputSummary(_EvidenceModel):
    candidate_context_items: int = Field(ge=0)
    selected_context_items: int = Field(ge=0)
    input_estimated_tokens: int = Field(ge=0)
    input_char_count: int = Field(ge=0)


class EvidencePackOutputSummary(_EvidenceModel):
    evidence_group_count: int = Field(ge=0)
    evidence_item_count: int = Field(ge=0)
    output_estimated_tokens: int = Field(ge=0)
    output_char_count: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0)
    citation_candidate_count: int = Field(ge=0)


class EvidencePackTrace(_EvidenceModel):
    schema_version: Literal["phase2.context_compression.v1"] = EVIDENCE_PACK_SCHEMA_VERSION
    enabled: bool
    method: Literal["deterministic_evidence_pack"] = "deterministic_evidence_pack"
    policy: dict[str, object] = Field(default_factory=dict)
    input: EvidencePackInputSummary
    output: EvidencePackOutputSummary
    drops: dict[str, int] = Field(default_factory=dict)
    evidence_groups: list[EvidenceGroup] = Field(default_factory=list)
    evidence_item_refs: list[EvidenceItemRef] = Field(default_factory=list)
    dropped_item_refs: list[DroppedEvidenceRef] = Field(default_factory=list)

    @field_validator("policy", mode="before")
    @classmethod
    def sanitize_policy(cls, value: object) -> object:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "max_items",
            "max_items_per_source",
            "max_chars_per_item",
            "max_total_chars",
            "near_duplicate_threshold",
            "preserve_citation_candidates",
            "group_by_source",
        }
        safe: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text not in allowed:
                continue
            if isinstance(nested, bool):
                safe[key_text] = nested
            elif isinstance(nested, int | float) and not isinstance(nested, bool):
                safe[key_text] = nested
        return safe

    @field_validator("drops", mode="before")
    @classmethod
    def sanitize_drops(cls, value: object) -> object:
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


@dataclass(frozen=True)
class EvidencePack:
    items: list[EvidenceItem]
    groups: list[EvidenceGroup]
    trace: EvidencePackTrace

    @property
    def selected_item_ids(self) -> list[int]:
        return [item.retrieval_run_item_id for item in self.items]

    def to_generation_context_items(self) -> list[GenerationContextItem]:
        return [
            GenerationContextItem(
                document_chunk_id=item.document_chunk_id,
                source_label=item.source_label or f"chunk:{item.document_chunk_id}",
                text=item.evidence_text_for_generation,
                local_citation_id=item.local_citation_id,
                page_from=item.page_from,
                page_to=item.page_to,
            )
            for item in self.items
        ]


@dataclass(frozen=True)
class _CompressionCandidate:
    candidate: EvidenceCandidate
    clean_text: str
    normalized_text: str
    token_set: frozenset[str]
    original_char_count: int
    estimated_tokens: int


class ContextCompressor:
    def compress(
        self,
        candidates: list[EvidenceCandidate],
        *,
        policy: EvidencePackPolicy,
    ) -> tuple[list[EvidenceItem], list[DroppedEvidenceRef], dict[str, int]]:
        prepared = [_prepare_candidate(candidate) for candidate in candidates]
        drops: list[DroppedEvidenceRef] = []
        drop_counts: Counter[str] = Counter()
        accepted: list[EvidenceItem] = []
        seen_hashes: set[str] = set()
        seen_normalized: set[str] = set()
        items_per_source: Counter[str] = Counter()
        output_chars = 0

        for prepared_candidate in prepared:
            candidate = prepared_candidate.candidate
            if not prepared_candidate.clean_text:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.MISSING_TEXT,
                )
                continue
            text_hash = _sha256(prepared_candidate.clean_text)
            if text_hash in seen_hashes:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.EXACT_DUPLICATE_REMOVED,
                )
                continue
            if prepared_candidate.normalized_text in seen_normalized:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.NORMALIZED_DUPLICATE_REMOVED,
                )
                continue
            if _near_duplicate(prepared_candidate, accepted, policy.near_duplicate_threshold):
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.NEAR_DUPLICATE_REMOVED,
                )
                continue
            if len(accepted) >= policy.max_items:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.MAX_ITEMS_EXCEEDED,
                )
                continue
            if items_per_source[candidate.source_group_key] >= policy.max_items_per_source:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.MAX_ITEMS_PER_SOURCE,
                )
                continue
            remaining_total = policy.max_total_chars - output_chars
            if remaining_total <= 0:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.MAX_TOTAL_CHARS,
                )
                continue
            evidence_text = prepared_candidate.clean_text[
                : min(policy.max_chars_per_item, remaining_total)
            ]
            if not evidence_text:
                _drop(
                    drops,
                    drop_counts,
                    prepared_candidate,
                    CompressionDropReason.MAX_TOTAL_CHARS,
                )
                continue
            method = (
                CompressionMethod.BOUNDED_EXCERPT
                if len(evidence_text) < prepared_candidate.original_char_count
                else CompressionMethod.NONE
            )
            if policy.group_by_source and method == CompressionMethod.NONE:
                method = CompressionMethod.SOURCE_GROUPED
            output_chars += len(evidence_text)
            items_per_source[candidate.source_group_key] += 1
            seen_hashes.add(text_hash)
            seen_normalized.add(prepared_candidate.normalized_text)
            accepted.append(
                EvidenceItem(
                    evidence_item_id=f"e{len(accepted) + 1}",
                    retrieval_run_item_id=candidate.retrieval_run_item_id,
                    document_chunk_id=candidate.document_chunk_id,
                    local_citation_id=candidate.local_citation_id,
                    source_label=candidate.source_label,
                    section_title=candidate.section_title,
                    page_from=candidate.page_from,
                    page_to=candidate.page_to,
                    score=_rounded(candidate.score),
                    rerank_score=_rounded(candidate.rerank_score),
                    rank=candidate.rank,
                    rerank_order=candidate.rerank_order,
                    source_group_key=candidate.source_group_key,
                    evidence_text_for_generation=evidence_text,
                    evidence_text_hash=_sha256(evidence_text),
                    original_char_count=prepared_candidate.original_char_count,
                    output_char_count=len(evidence_text),
                    estimated_tokens=estimate_tokens(evidence_text),
                    citation_candidate=candidate.citation_candidate,
                    compression_method=method,
                    compression_reason=method.value,
                    retrieval_source=candidate.retrieval_source,
                    logical_document_id=candidate.logical_document_id,
                    document_version_id=candidate.document_version_id,
                )
            )

        return accepted, drops, dict(drop_counts)


class EvidencePackBuilder:
    def __init__(self, compressor: ContextCompressor | None = None) -> None:
        self.compressor = compressor or ContextCompressor()

    def build(
        self,
        candidates: list[EvidenceCandidate],
        *,
        policy: EvidencePackPolicy,
        candidate_context_items: int | None = None,
    ) -> EvidencePack:
        input_char_count = sum(len(_clean_text(candidate.text)) for candidate in candidates)
        input_estimated_tokens = sum(
            estimate_tokens(_clean_text(candidate.text)) for candidate in candidates
        )
        if not policy.enabled:
            items, drops, drop_counts = _passthrough_items(candidates, policy=policy)
        else:
            items, drops, drop_counts = self.compressor.compress(candidates, policy=policy)

        groups = _evidence_groups(items)
        trace = EvidencePackTrace(
            enabled=policy.enabled,
            policy=_policy_trace(policy),
            input=EvidencePackInputSummary(
                candidate_context_items=max(
                    0,
                    candidate_context_items
                    if candidate_context_items is not None
                    else len(candidates),
                ),
                selected_context_items=len(candidates),
                input_estimated_tokens=input_estimated_tokens,
                input_char_count=input_char_count,
            ),
            output=EvidencePackOutputSummary(
                evidence_group_count=len(groups),
                evidence_item_count=len(items),
                output_estimated_tokens=sum(item.estimated_tokens for item in items),
                output_char_count=sum(item.output_char_count for item in items),
                compression_ratio=_compression_ratio(
                    sum(item.output_char_count for item in items),
                    input_char_count,
                ),
                citation_candidate_count=sum(1 for item in items if item.citation_candidate),
            ),
            drops=drop_counts,
            evidence_groups=groups,
            evidence_item_refs=[_evidence_item_ref(item) for item in items],
            dropped_item_refs=drops,
        )
        return EvidencePack(items=items, groups=groups, trace=trace)


def _passthrough_items(
    candidates: list[EvidenceCandidate],
    *,
    policy: EvidencePackPolicy,
) -> tuple[list[EvidenceItem], list[DroppedEvidenceRef], dict[str, int]]:
    items: list[EvidenceItem] = []
    drops: list[DroppedEvidenceRef] = []
    drop_counts: Counter[str] = Counter()
    output_chars = 0
    for prepared_candidate in [_prepare_candidate(candidate) for candidate in candidates]:
        if not prepared_candidate.clean_text:
            _drop(
                drops,
                drop_counts,
                prepared_candidate,
                CompressionDropReason.MISSING_TEXT,
            )
            continue
        remaining_total = policy.max_total_chars - output_chars
        if remaining_total <= 0:
            _drop(
                drops,
                drop_counts,
                prepared_candidate,
                CompressionDropReason.MAX_TOTAL_CHARS,
            )
            continue
        candidate = prepared_candidate.candidate
        evidence_text = prepared_candidate.clean_text[:remaining_total]
        output_chars += len(evidence_text)
        items.append(
            EvidenceItem(
                evidence_item_id=f"e{len(items) + 1}",
                retrieval_run_item_id=candidate.retrieval_run_item_id,
                document_chunk_id=candidate.document_chunk_id,
                local_citation_id=candidate.local_citation_id,
                source_label=candidate.source_label,
                section_title=candidate.section_title,
                page_from=candidate.page_from,
                page_to=candidate.page_to,
                score=_rounded(candidate.score),
                rerank_score=_rounded(candidate.rerank_score),
                rank=candidate.rank,
                rerank_order=candidate.rerank_order,
                source_group_key=candidate.source_group_key,
                evidence_text_for_generation=evidence_text,
                evidence_text_hash=_sha256(evidence_text),
                original_char_count=prepared_candidate.original_char_count,
                output_char_count=len(evidence_text),
                estimated_tokens=estimate_tokens(evidence_text),
                citation_candidate=candidate.citation_candidate,
                compression_method=CompressionMethod.NONE,
                compression_reason="disabled",
                retrieval_source=candidate.retrieval_source,
                logical_document_id=candidate.logical_document_id,
                document_version_id=candidate.document_version_id,
            )
        )
    return items, drops, dict(drop_counts)


def sanitize_context_compression_json(value: dict[str, Any] | None) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        trace = EvidencePackTrace.model_validate(value)
    except ValueError:
        return None
    return trace.model_dump(mode="json", exclude_none=True)


def _prepare_candidate(candidate: EvidenceCandidate) -> _CompressionCandidate:
    clean_text = _clean_text(candidate.text)
    normalized_text = _normalize_text(clean_text)
    return _CompressionCandidate(
        candidate=candidate,
        clean_text=clean_text,
        normalized_text=normalized_text,
        token_set=frozenset(_TOKEN_RE.findall(normalized_text)),
        original_char_count=len(clean_text),
        estimated_tokens=estimate_tokens(clean_text),
    )


def _drop(
    drops: list[DroppedEvidenceRef],
    drop_counts: Counter[str],
    candidate: _CompressionCandidate,
    reason: CompressionDropReason,
) -> None:
    drop_counts[reason.value] += 1
    drops.append(
        DroppedEvidenceRef(
            retrieval_run_item_id=candidate.candidate.retrieval_run_item_id,
            document_chunk_id=candidate.candidate.document_chunk_id,
            source_label=candidate.candidate.source_label,
            rank=candidate.candidate.rank,
            rerank_order=candidate.candidate.rerank_order,
            estimated_tokens=candidate.estimated_tokens,
            original_char_count=candidate.original_char_count,
            drop_reason=reason,
        )
    )


def _near_duplicate(
    candidate: _CompressionCandidate,
    accepted: list[EvidenceItem],
    threshold: float,
) -> bool:
    if threshold <= 0 or not candidate.token_set:
        return False
    for item in accepted:
        accepted_tokens = frozenset(
            _TOKEN_RE.findall(_normalize_text(item.evidence_text_for_generation))
        )
        if not accepted_tokens:
            continue
        intersection = len(candidate.token_set & accepted_tokens)
        union = len(candidate.token_set | accepted_tokens)
        if union and intersection / union >= threshold:
            return True
    return False


def _evidence_groups(items: list[EvidenceItem]) -> list[EvidenceGroup]:
    grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in items:
        grouped[item.source_group_key].append(item)
    groups: list[EvidenceGroup] = []
    for source_group_key, group_items in sorted(grouped.items()):
        groups.append(
            EvidenceGroup(
                source_group_key=source_group_key,
                source_label=next(
                    (item.source_label for item in group_items if item.source_label),
                    None,
                ),
                document_version_id=next(
                    (
                        item.document_version_id
                        for item in group_items
                        if item.document_version_id is not None
                    ),
                    None,
                ),
                logical_document_id=next(
                    (
                        item.logical_document_id
                        for item in group_items
                        if item.logical_document_id is not None
                    ),
                    None,
                ),
                item_count=len(group_items),
                selected_item_count=len(group_items),
                estimated_tokens=sum(item.estimated_tokens for item in group_items),
                top_score=_rounded(
                    max(
                        (
                            item.rerank_score
                            if item.rerank_score is not None
                            else item.score
                            if item.score is not None
                            else 0.0
                        )
                        for item in group_items
                    )
                ),
                evidence_item_refs=[item.evidence_item_id for item in group_items],
            )
        )
    return groups


def _evidence_item_ref(item: EvidenceItem) -> EvidenceItemRef:
    return EvidenceItemRef(
        evidence_item_id=item.evidence_item_id,
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        local_citation_id=item.local_citation_id,
        source_label=item.source_label,
        section_title=item.section_title,
        page_from=item.page_from,
        page_to=item.page_to,
        score=item.score,
        rerank_score=item.rerank_score,
        rank=item.rank,
        rerank_order=item.rerank_order,
        source_group_key=item.source_group_key,
        evidence_text_hash=item.evidence_text_hash,
        original_char_count=item.original_char_count,
        output_char_count=item.output_char_count,
        estimated_tokens=item.estimated_tokens,
        citation_candidate=item.citation_candidate,
        compression_method=item.compression_method,
        compression_reason=item.compression_reason,
        retrieval_source=item.retrieval_source,
    )


def _policy_trace(policy: EvidencePackPolicy) -> dict[str, object]:
    return {
        "max_items": policy.max_items,
        "max_items_per_source": policy.max_items_per_source,
        "max_chars_per_item": policy.max_chars_per_item,
        "max_total_chars": policy.max_total_chars,
        "near_duplicate_threshold": round(policy.near_duplicate_threshold, 6),
        "preserve_citation_candidates": policy.preserve_citation_candidates,
        "group_by_source": policy.group_by_source,
    }


def _compression_ratio(output_char_count: int, input_char_count: int) -> float:
    if input_char_count <= 0:
        return 0.0
    return round(output_char_count / input_char_count, 6)


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return " ".join(text.replace("\x00", " ").split())


def _normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_evidence_string(value: str, *, max_length: int) -> str:
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    path_normalized = safe.replace("\\", "/")
    if path_normalized.startswith(("/", "//")) or _WINDOWS_PATH_RE.search(path_normalized):
        return "redacted"
    return safe


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), 6)
