from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Final, TypeVar

from app.core.config import Settings
from app.rag.retrieval import RetrievalFilters
from app.rag.strategy import QueryIntent, RetrievalStrategy
from app.rag.trace import TraceRedactor
from app.schemas.rag_strategy import (
    QueryAnalysisTrace,
    QueryMetadataFilterCandidate,
    QueryPlannerTrace,
    QuerySubQueryTrace,
)

QUERY_PLAN_SCHEMA_VERSION: Final = "phase2.query_plan.v1"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u3040-\u30ff\u3400-\u9fff]+")
_WHITESPACE_RE = re.compile(r"\s+")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d ._-]{7,}\d)\b")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|token|credential|cookie|csrf|session)"
)
_VERSION_RE = re.compile(r"(?i)\b(?:v(?:ersion)?\.?\s*\d+(?:\.\d+){0,3}|\d+\.\d+(?:\.\d+)*)\b")
_ERROR_CODE_RE = re.compile(r"\b(?:[A-Z][A-Z0-9_]{2,}|[A-Z]+-\d{2,}|(?i:HTTP)\s*[45]\d{2})\b")
_ENDPOINT_RE = re.compile(r"(?i)(?:^|\s)/[A-Za-z0-9_./{}:-]+")
_FILE_EXTENSION_RE = re.compile(r"(?i)\.[a-z0-9]{1,8}\b")
_QUOTED_RE = re.compile(r"[\"'`「『](.*?)[\"'`」』]")
_STOPWORDS: Final = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "how",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "why",
    "with",
}

_COMPARISON_KEYWORDS: Final = (
    "compare",
    "comparison",
    "difference",
    "differences",
    "vs",
    "versus",
    "比較",
    "違い",
    "差",
)
_PROCEDURAL_KEYWORDS: Final = (
    "how",
    "procedure",
    "steps",
    "setup",
    "configure",
    "方法",
    "手順",
    "やり方",
    "設定",
)
_SUMMARIZATION_KEYWORDS: Final = (
    "summary",
    "summarize",
    "overview",
    "要約",
    "まとめ",
    "概要",
)
_TROUBLESHOOTING_KEYWORDS: Final = (
    "error",
    "failed",
    "failure",
    "exception",
    "traceback",
    "not working",
    "エラー",
    "失敗",
    "動かない",
    "例外",
)
_DEFINITION_KEYWORDS: Final = (
    "define",
    "definition",
    "what is",
    "meaning",
    "とは",
    "定義",
    "意味",
)
_VERSION_KEYWORDS: Final = (
    "version",
    "latest",
    "current",
    "old",
    "new",
    "before",
    "after",
    "revision",
    "revised",
    "changelog",
    "最新版",
    "旧版",
    "改訂",
    "差分",
    "変更点",
    "現行",
    "過去",
)
_TEMPORAL_KEYWORDS: Final = (
    "today",
    "yesterday",
    "tomorrow",
    "latest",
    "current",
    "now",
    "今日",
    "昨日",
    "明日",
    "最新",
    "現在",
)
_AMBIGUOUS_REFERENCES: Final = (
    "this",
    "that",
    "it",
    "these",
    "those",
    "これ",
    "それ",
    "あれ",
    "この",
    "その",
    "あの",
)
T = TypeVar("T", bound=Hashable)


@dataclass(frozen=True)
class QueryPlanBuildResult:
    analysis: QueryAnalysisTrace | None
    planner: QueryPlannerTrace | None
    trace_metadata: dict[str, object]
    retrieval_query: str


class QueryAnalyzer:
    def __init__(self, *, max_preview_chars: int = 160, store_query_preview: bool = True) -> None:
        self.max_preview_chars = max_preview_chars
        self.store_query_preview = store_query_preview

    def analyze(self, query: str, *, filters: RetrievalFilters) -> QueryAnalysisTrace:
        normalized = normalize_query(query)
        query_hash = _hash_text(query)
        tokens = _tokens(normalized)
        ambiguity_flags = _ambiguity_flags(normalized, tokens)
        keyword_signals = _keyword_signals(normalized, tokens)
        version_hints = _version_hints(normalized)
        metadata_hints = _metadata_filter_candidates(normalized, self.max_preview_chars)
        intent = _intent(normalized, version_hints=version_hints)
        keyword_score = _keyword_heavy_score(tokens, keyword_signals)
        ambiguity_score = _ambiguity_score(ambiguity_flags)
        reason_codes = [
            f"intent:{intent.value}",
            f"ambiguity_flags:{len(ambiguity_flags)}",
            f"keyword_signals:{len(keyword_signals)}",
        ]
        if filters.logical_document_ids:
            reason_codes.append("request_filter:logical_document_ids")
        if metadata_hints:
            reason_codes.append(f"metadata_hints:{len(metadata_hints)}")

        return QueryAnalysisTrace(
            query_hash=query_hash,
            normalized_query_preview=None,
            intent=intent,
            ambiguity_score=ambiguity_score,
            ambiguity_flags=ambiguity_flags,
            needs_clarification_candidate=ambiguity_score >= 0.6,
            keyword_heavy_score=keyword_score,
            keyword_signals=keyword_signals,
            version_specific_flag=bool(version_hints),
            version_hints=version_hints,
            temporal_reference_flag=_contains_any(normalized, _TEMPORAL_KEYWORDS),
            metadata_filter_hints=metadata_hints,
            recommended_candidate_strategies=_candidate_strategies(
                intent=intent,
                ambiguity_score=ambiguity_score,
                keyword_heavy_score=keyword_score,
                version_specific=bool(version_hints),
                metadata_filter_count=len(metadata_hints),
            ),
            reason_codes=reason_codes,
        )


class QueryPlanner:
    def __init__(
        self,
        *,
        max_preview_chars: int = 160,
        max_sub_queries: int = 3,
        store_query_preview: bool = True,
        apply_rewrite_to_retrieval: bool = False,
    ) -> None:
        self.max_preview_chars = max_preview_chars
        self.max_sub_queries = max_sub_queries
        self.store_query_preview = store_query_preview
        self.apply_rewrite_to_retrieval = apply_rewrite_to_retrieval

    def plan(
        self,
        query: str,
        *,
        analysis: QueryAnalysisTrace,
        requested_strategy: RetrievalStrategy,
    ) -> QueryPlannerTrace:
        rewritten = rewrite_query(query)
        rewrite_applied = rewritten != query
        sub_queries = _sub_queries(
            rewritten,
            intent=analysis.intent,
            max_count=self.max_sub_queries,
            max_preview_chars=self.max_preview_chars,
            store_query_preview=self.store_query_preview,
        )
        candidate_strategies = list(analysis.recommended_candidate_strategies)
        recommended_strategy = (
            candidate_strategies[0] if candidate_strategies else requested_strategy
        )
        safety_flags = ["planned_only", "router_not_executed"]
        if not self.apply_rewrite_to_retrieval:
            safety_flags.append("rewrite_not_applied_to_retrieval")
        if analysis.needs_clarification_candidate:
            safety_flags.append("clarification_not_requested_in_pr27")

        return QueryPlannerTrace(
            query_hash=_hash_text(query),
            intent=analysis.intent,
            rewrite_applied=rewrite_applied,
            rewritten_query_hash=_hash_text(rewritten) if rewrite_applied else None,
            rewritten_query_preview=_preview(
                rewritten,
                max_chars=self.max_preview_chars,
                enabled=self.store_query_preview,
            )
            if rewrite_applied
            else None,
            sub_queries=sub_queries,
            metadata_filter_candidates=analysis.metadata_filter_hints,
            candidate_strategies=candidate_strategies,
            recommended_strategy=recommended_strategy,
            disabled_reason="strategy_router_not_implemented",
            safety_flags=safety_flags,
            reason_codes=[
                "rule_based_query_planner",
                f"requested_strategy:{requested_strategy.value}",
                f"recommended_strategy:{recommended_strategy.value}",
            ],
        )


class QueryPlanBuilder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analyzer = QueryAnalyzer(
            max_preview_chars=settings.query_planner_max_preview_chars,
            store_query_preview=settings.query_planner_store_query_preview,
        )
        self.planner = QueryPlanner(
            max_preview_chars=settings.query_planner_max_preview_chars,
            max_sub_queries=settings.query_planner_max_sub_queries,
            store_query_preview=settings.query_planner_store_query_preview,
            apply_rewrite_to_retrieval=settings.query_planner_apply_rewrite_to_retrieval,
        )

    def build(
        self,
        query: str,
        *,
        filters: RetrievalFilters,
        requested_strategy: RetrievalStrategy,
    ) -> QueryPlanBuildResult:
        if not self.settings.query_analyzer_enabled:
            return QueryPlanBuildResult(
                analysis=None,
                planner=None,
                trace_metadata={
                    "analysis_enabled": False,
                    "planner_enabled": False,
                    "disabled_reason": "query_analyzer_disabled",
                },
                retrieval_query=query,
            )
        analysis = self.analyzer.analyze(query, filters=filters)
        if not self.settings.query_planner_enabled:
            return QueryPlanBuildResult(
                analysis=analysis,
                planner=None,
                trace_metadata=_trace_metadata(analysis=analysis, planner=None),
                retrieval_query=query,
            )
        planner = self.planner.plan(
            query,
            analysis=analysis,
            requested_strategy=requested_strategy,
        )
        retrieval_query = rewrite_query(query) if self.planner.apply_rewrite_to_retrieval else query
        return QueryPlanBuildResult(
            analysis=analysis,
            planner=planner,
            trace_metadata=_trace_metadata(analysis=analysis, planner=planner),
            retrieval_query=retrieval_query,
        )


def normalize_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query.replace("\x00", " "))
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def rewrite_query(query: str) -> str:
    return normalize_query(query)


def _trace_metadata(
    *,
    analysis: QueryAnalysisTrace,
    planner: QueryPlannerTrace | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "analysis": analysis.model_dump(mode="json", exclude_none=True),
        "intent": analysis.intent.value,
        "ambiguity_score": analysis.ambiguity_score,
        "ambiguity_flags": analysis.ambiguity_flags,
        "needs_clarification_candidate": analysis.needs_clarification_candidate,
        "keyword_heavy_score": analysis.keyword_heavy_score,
        "keyword_signals": analysis.keyword_signals,
        "version_specific_flag": analysis.version_specific_flag,
        "version_hints": analysis.version_hints,
        "temporal_reference_flag": analysis.temporal_reference_flag,
        "metadata_filter_candidate_count": len(analysis.metadata_filter_hints),
    }
    if planner is None:
        payload.update(
            {
                "planner_enabled": False,
                "disabled_reason": "query_planner_disabled",
            }
        )
        return TraceRedactor.safe_dict(_drop_none(payload))

    payload.update(
        {
            "planner": planner.model_dump(mode="json", exclude_none=True),
            "rewrite_applied": planner.rewrite_applied,
            "rewritten_query_hash": planner.rewritten_query_hash,
            "rewritten_query_preview": planner.rewritten_query_preview,
            "sub_query_count": len(planner.sub_queries),
            "sub_queries": [
                sub_query.model_dump(mode="json", exclude_none=True)
                for sub_query in planner.sub_queries
            ],
            "metadata_filter_candidates": [
                candidate.model_dump(mode="json", exclude_none=True)
                for candidate in planner.metadata_filter_candidates
            ],
            "candidate_strategies": [strategy.value for strategy in planner.candidate_strategies],
            "recommended_strategy": (
                planner.recommended_strategy.value if planner.recommended_strategy else None
            ),
            "disabled_reason": planner.disabled_reason,
            "safety_flags": planner.safety_flags,
        }
    )
    return TraceRedactor.safe_dict(_drop_none(payload))


def _intent(normalized: str, *, version_hints: list[str]) -> QueryIntent:
    lowered = normalized.lower()
    if version_hints:
        return QueryIntent.VERSION_SPECIFIC
    if _contains_any(lowered, _COMPARISON_KEYWORDS):
        return QueryIntent.COMPARISON
    if _contains_any(lowered, _TROUBLESHOOTING_KEYWORDS):
        return QueryIntent.TROUBLESHOOTING
    if _contains_any(lowered, _SUMMARIZATION_KEYWORDS):
        return QueryIntent.SUMMARIZATION
    if _contains_any(lowered, _PROCEDURAL_KEYWORDS):
        return QueryIntent.PROCEDURAL
    if _contains_any(lowered, _DEFINITION_KEYWORDS):
        return QueryIntent.DEFINITION
    return QueryIntent.FACTUAL_LOOKUP if len(_tokens(normalized)) >= 2 else QueryIntent.UNKNOWN


def _ambiguity_flags(normalized: str, tokens: list[str]) -> list[str]:
    lowered = normalized.lower()
    flags: list[str] = []
    if len(tokens) <= 2:
        flags.append("short_query")
    english_references = {"this", "that", "it", "these", "those"}
    lowered_tokens = {token.lower() for token in tokens}
    if english_references & lowered_tokens or any(
        reference in lowered for reference in _AMBIGUOUS_REFERENCES if not reference.isascii()
    ):
        flags.append("deictic_reference")
    if lowered in {"latest", "current", "最新版", "最新", "これ", "それ"}:
        flags.append("missing_subject")
    if " or " in lowered or "どちら" in lowered:
        flags.append("multiple_possible_targets")
    return _dedupe(flags)


def _ambiguity_score(flags: list[str]) -> float:
    weights = {
        "short_query": 0.3,
        "deictic_reference": 0.35,
        "missing_subject": 0.45,
        "multiple_possible_targets": 0.25,
    }
    return round(min(1.0, sum(weights.get(flag, 0.2) for flag in flags)), 3)


def _keyword_signals(normalized: str, tokens: list[str]) -> list[str]:
    signals: list[str] = []
    if _ENDPOINT_RE.search(normalized):
        signals.append("api_endpoint")
    if _FILE_EXTENSION_RE.search(normalized):
        signals.append("file_extension")
    if _ERROR_CODE_RE.search(normalized):
        signals.append("error_or_code_token")
    if _QUOTED_RE.search(normalized):
        signals.append("quoted_phrase")
    if any("_" in token or "." in token or ":" in token for token in tokens):
        signals.append("code_like_token")
    if any(token.isupper() and len(token) >= 3 for token in tokens):
        signals.append("uppercase_token")
    if tokens:
        stopword_count = sum(1 for token in tokens if token.lower() in _STOPWORDS)
        if stopword_count / len(tokens) <= 0.2 and len(tokens) >= 3:
            signals.append("low_stopword_ratio")
    return _dedupe(signals)


def _keyword_heavy_score(tokens: list[str], signals: list[str]) -> float:
    if not tokens:
        return 0.0
    score = min(1.0, len(signals) * 0.18)
    if len(tokens) <= 5 and signals:
        score += 0.15
    return round(min(1.0, score), 3)


def _version_hints(normalized: str) -> list[str]:
    lowered = normalized.lower()
    hints: list[str] = []
    if _VERSION_RE.search(normalized):
        hints.append("version_token")
    for keyword in _VERSION_KEYWORDS:
        if keyword.lower() in lowered:
            hints.append(f"version_keyword:{TraceRedactor.safe_string(keyword, max_length=40)}")
    return _dedupe(hints)


def _metadata_filter_candidates(
    normalized: str,
    max_preview_chars: int,
) -> list[QueryMetadataFilterCandidate]:
    candidates: list[QueryMetadataFilterCandidate] = []
    for extension in _dedupe(
        match.group(0).lower() for match in _FILE_EXTENSION_RE.finditer(normalized)
    ):
        candidates.append(
            QueryMetadataFilterCandidate(
                filter_type="file_extension",
                field="source_label",
                operator="ends_with",
                value_preview=_preview(extension, max_chars=max_preview_chars, enabled=True),
                value_hash=_hash_text(extension),
                confidence=0.7,
                reason_code="file_extension_signal",
            )
        )
    if "section:" in normalized.lower():
        section_hint = normalized.split(":", 1)[1].strip()
        if section_hint:
            candidates.append(
                QueryMetadataFilterCandidate(
                    filter_type="section_title",
                    field="section_title",
                    operator="contains",
                    value_preview=_preview(section_hint, max_chars=max_preview_chars, enabled=True),
                    value_hash=_hash_text(section_hint),
                    confidence=0.45,
                    reason_code="section_hint_signal",
                )
            )
    return candidates[:5]


def _candidate_strategies(
    *,
    intent: QueryIntent,
    ambiguity_score: float,
    keyword_heavy_score: float,
    version_specific: bool,
    metadata_filter_count: int,
) -> list[RetrievalStrategy]:
    strategies: list[RetrievalStrategy] = []
    if ambiguity_score >= 0.6:
        strategies.extend([RetrievalStrategy.FALLBACK_DENSE, RetrievalStrategy.HYBRID])
    if version_specific:
        strategies.extend([RetrievalStrategy.VERSION_AWARE, RetrievalStrategy.HYBRID])
    if metadata_filter_count:
        strategies.extend([RetrievalStrategy.METADATA_FILTERED, RetrievalStrategy.HYBRID])
    if intent == QueryIntent.COMPARISON:
        strategies.extend([RetrievalStrategy.MULTI_QUERY_HYBRID, RetrievalStrategy.HYBRID])
    if keyword_heavy_score >= 0.5 or intent == QueryIntent.TROUBLESHOOTING:
        strategies.extend([RetrievalStrategy.HYBRID, RetrievalStrategy.SPARSE])
    strategies.extend([RetrievalStrategy.DENSE, RetrievalStrategy.HYBRID])
    return _dedupe(strategies)


def _sub_queries(
    query: str,
    *,
    intent: QueryIntent,
    max_count: int,
    max_preview_chars: int,
    store_query_preview: bool,
) -> list[QuerySubQueryTrace]:
    if max_count <= 0:
        return []
    normalized = normalize_query(query)
    candidates: list[tuple[str, str]] = []
    if intent == QueryIntent.COMPARISON:
        parts = re.split(r"(?i)\s+(?:vs|versus|and|と|と比較|比較)\s+", normalized)
        candidates.extend((part.strip(), "comparison_component") for part in parts if part.strip())
    elif intent == QueryIntent.VERSION_SPECIFIC:
        without_version_words = normalized
        for keyword in _VERSION_KEYWORDS:
            without_version_words = re.sub(
                re.escape(keyword), " ", without_version_words, flags=re.I
            )
        candidates.append((normalize_query(without_version_words), "version_target"))
    elif intent == QueryIntent.TROUBLESHOOTING:
        codes = [match.group(0) for match in _ERROR_CODE_RE.finditer(normalized)]
        candidates.extend((code, "troubleshooting_code") for code in codes)
    if not candidates and len(_tokens(normalized)) >= 6:
        candidates.append((" ".join(_tokens(normalized)[:4]), "leading_terms"))

    sub_queries: list[QuerySubQueryTrace] = []
    seen: set[str] = set()
    for candidate, reason_code in candidates:
        candidate = normalize_query(candidate)
        if not candidate or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        sub_queries.append(
            QuerySubQueryTrace(
                query_hash=_hash_text(candidate),
                query_preview=_preview(
                    candidate,
                    max_chars=max_preview_chars,
                    enabled=store_query_preview,
                ),
                intent=intent,
                reason_code=reason_code,
            )
        )
        if len(sub_queries) >= max_count:
            break
    return sub_queries


def _preview(value: str, *, max_chars: int, enabled: bool) -> str | None:
    if not enabled:
        return None
    normalized = normalize_query(value)
    if not normalized:
        return None
    if _PHONE_RE.search(normalized) or _SENSITIVE_QUERY_RE.search(normalized):
        return "redacted"
    safe = TraceRedactor.safe_string(normalized, max_length=max_chars)
    if len(safe) <= max_chars:
        return safe
    return f"{safe[: max_chars - 3]}..."


def _tokens(query: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_RE.finditer(query)]


def _contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _drop_none(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if value is not None}


def _dedupe(values: Iterable[T]) -> list[T]:
    seen: set[T] = set()
    deduped: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
