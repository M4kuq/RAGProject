from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from app.graph.normalization import GraphEntityNormalizer

RULE_BASED_GRAPH_EXTRACTOR_TYPE = "rule_based"
RULE_BASED_GRAPH_EXTRACTOR_VERSION = "pr47-rule-based-v1"

_ACRONYM_PAIR_RE = re.compile(
    r"\b(?P<expanded>[A-Z][A-Za-z0-9]+(?:[\s/-]+[A-Z][A-Za-z0-9]+){1,6})"
    r"\s*\((?P<acronym>[A-Z][A-Z0-9]{1,12})\)"
)
_REVERSE_ACRONYM_PAIR_RE = re.compile(
    r"\b(?P<acronym>[A-Z][A-Z0-9]{1,12})"
    r"\s*\((?P<expanded>[A-Z][A-Za-z0-9]+(?:[\s/-]+[A-Z][A-Za-z0-9]+){1,6})\)"
)
_CODE_IDENTIFIER_RE = re.compile(r"`(?P<identifier>[A-Za-z_][A-Za-z0-9_:.]{2,120})`")
_CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]+){1,}\b")
_TECH_PHRASE_RE = re.compile(
    r"\b(?:Agentic|Hybrid|Graph|Vector|Sparse|Semantic|Document|Entity|Relation|"
    r"Mention|Retrieval|Evaluation|Citation|Qdrant|LangChain|LangGraph|FastAPI|"
    r"PostgreSQL|Docker|BuildKit|OpenAI|React|TypeScript|Worker|Job|RAG|LLM|MCP|"
    r"API|UI|CI)"
    r"(?:[\s/-]+(?:RAG|Graph|Index|Schema|Pipeline|Service|Repository|Handler|Router|"
    r"Retriever|Reranker|Embedding|Document|Version|Chunk|Entity|Relation|Mention|"
    r"Payload|Worker|Job|API|UI|CI|LLM|MCP|Qdrant|LangChain|LangGraph))*\b"
)
_SENTENCE_RE = re.compile(r"[^.!?\n]{1,700}(?:[.!?]|$)")
_RELATION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("supports", ("support", "supports", "supported")),
    ("uses", (" use ", " uses ", " using ")),
    ("depends_on", ("depends on", "requires", "require")),
    ("includes", ("includes", "include", "contains", "stores", "persists")),
    ("connects", ("connects", "connect", "wires", "links")),
)


@dataclass(frozen=True)
class GraphChunkRef:
    document_chunk_id: int
    document_version_id: int
    chunk_index: int
    chunk_hash: str
    content_text: str = field(repr=False)


@dataclass(frozen=True)
class EntityMentionCandidate:
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...]
    document_chunk_id: int
    document_version_id: int
    chunk_index: int
    mention_text_hash: str
    mention_offset_start: int
    mention_offset_end: int
    confidence: Decimal
    metadata_json: dict[str, object]

    @property
    def entity_key(self) -> tuple[str, str]:
        return (self.canonical_name.lower(), self.entity_type)


@dataclass(frozen=True)
class RelationCandidate:
    source_key: tuple[str, str]
    target_key: tuple[str, str]
    relation_type: str
    relation_label: str
    confidence: Decimal
    source_document_chunk_id: int
    evidence_text_hash: str
    metadata_json: dict[str, object]


@dataclass(frozen=True)
class GraphExtractionResult:
    entity_mentions: tuple[EntityMentionCandidate, ...]
    relations: tuple[RelationCandidate, ...]


class EntityExtractionService:
    def __init__(
        self,
        *,
        normalizer: GraphEntityNormalizer | None = None,
        max_entities_per_chunk: int = 20,
    ) -> None:
        self.normalizer = normalizer or GraphEntityNormalizer()
        self.max_entities_per_chunk = max_entities_per_chunk

    def extract(self, chunks: tuple[GraphChunkRef, ...]) -> tuple[EntityMentionCandidate, ...]:
        candidates: list[EntityMentionCandidate] = []
        for chunk in chunks:
            candidates.extend(self._extract_chunk(chunk))
        return tuple(candidates)

    def _extract_chunk(self, chunk: GraphChunkRef) -> list[EntityMentionCandidate]:
        candidates: list[EntityMentionCandidate] = []
        seen: set[tuple[str, str, int, int]] = set()

        for match in _ACRONYM_PAIR_RE.finditer(chunk.content_text):
            candidates.extend(
                self._candidate_from_pair(
                    chunk,
                    expanded=match.group("expanded"),
                    acronym=match.group("acronym"),
                    expanded_span=match.span("expanded"),
                    acronym_span=match.span("acronym"),
                    seen=seen,
                )
            )
        for match in _REVERSE_ACRONYM_PAIR_RE.finditer(chunk.content_text):
            candidates.extend(
                self._candidate_from_pair(
                    chunk,
                    expanded=match.group("expanded"),
                    acronym=match.group("acronym"),
                    expanded_span=match.span("expanded"),
                    acronym_span=match.span("acronym"),
                    seen=seen,
                )
            )

        extraction_rules = (
            (_CODE_IDENTIFIER_RE, "code_identifier", Decimal("0.82000")),
            (_CAMEL_CASE_RE, "camel_case_identifier", Decimal("0.80000")),
            (_TECH_PHRASE_RE, "technical_phrase", Decimal("0.78000")),
        )
        for regex, rule_id, confidence in extraction_rules:
            for match in regex.finditer(chunk.content_text):
                text = match.groupdict().get("identifier") or match.group(0)
                span = (
                    match.span("identifier")
                    if "identifier" in match.groupdict()
                    else match.span(0)
                )
                candidate = self._candidate_from_text(
                    chunk,
                    text,
                    span=span,
                    aliases=(),
                    rule_id=rule_id,
                    confidence=confidence,
                    seen=seen,
                )
                if candidate is not None:
                    candidates.append(candidate)
                if len(candidates) >= self.max_entities_per_chunk:
                    return candidates[: self.max_entities_per_chunk]

        return candidates[: self.max_entities_per_chunk]

    def _candidate_from_pair(
        self,
        chunk: GraphChunkRef,
        *,
        expanded: str,
        acronym: str,
        expanded_span: tuple[int, int],
        acronym_span: tuple[int, int],
        seen: set[tuple[str, str, int, int]],
    ) -> list[EntityMentionCandidate]:
        normalized = self.normalizer.normalize(expanded, aliases=(acronym,))
        if normalized is None:
            return []
        results: list[EntityMentionCandidate] = []
        for text, span, rule_id in (
            (expanded, expanded_span, "acronym_expanded_form"),
            (acronym, acronym_span, "acronym_alias"),
        ):
            candidate = self._candidate_from_text(
                chunk,
                text,
                span=span,
                aliases=normalized.aliases,
                canonical_name=normalized.canonical_name,
                entity_type=normalized.entity_type,
                rule_id=rule_id,
                confidence=Decimal("0.88000"),
                seen=seen,
            )
            if candidate is not None:
                results.append(candidate)
        return results

    def _candidate_from_text(
        self,
        chunk: GraphChunkRef,
        text: str,
        *,
        span: tuple[int, int],
        aliases: tuple[str, ...],
        rule_id: str,
        confidence: Decimal,
        seen: set[tuple[str, str, int, int]],
        canonical_name: str | None = None,
        entity_type: str | None = None,
    ) -> EntityMentionCandidate | None:
        normalized = (
            self.normalizer.normalize(text, aliases=aliases)
            if canonical_name is None or entity_type is None
            else self.normalizer.normalize(
                canonical_name,
                entity_type=entity_type,
                aliases=aliases,
            )
        )
        if normalized is None:
            return None
        dedupe_key = (
            normalized.canonical_name.lower(),
            normalized.entity_type,
            span[0],
            span[1],
        )
        if dedupe_key in seen:
            return None
        seen.add(dedupe_key)
        return EntityMentionCandidate(
            canonical_name=normalized.canonical_name,
            entity_type=normalized.entity_type,
            aliases=normalized.aliases,
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=chunk.document_version_id,
            chunk_index=chunk.chunk_index,
            mention_text_hash=_sha256(text),
            mention_offset_start=span[0],
            mention_offset_end=span[1],
            confidence=confidence,
            metadata_json={"rule_id": rule_id, "chunk_index": chunk.chunk_index},
        )


class RelationExtractionService:
    def __init__(self, *, max_relations_per_chunk: int = 40) -> None:
        self.max_relations_per_chunk = max_relations_per_chunk

    def extract(
        self,
        chunks: tuple[GraphChunkRef, ...],
        mentions: tuple[EntityMentionCandidate, ...],
    ) -> tuple[RelationCandidate, ...]:
        mentions_by_chunk: dict[int, list[EntityMentionCandidate]] = defaultdict(list)
        for mention in mentions:
            mentions_by_chunk[mention.document_chunk_id].append(mention)

        relations: list[RelationCandidate] = []
        seen: set[tuple[tuple[str, str], tuple[str, str], str, int]] = set()
        for chunk in chunks:
            chunk_mentions = sorted(
                mentions_by_chunk.get(chunk.document_chunk_id, []),
                key=lambda mention: (mention.mention_offset_start, mention.mention_offset_end),
            )
            relations.extend(self._extract_chunk(chunk, chunk_mentions, seen))
        return tuple(relations)

    def _extract_chunk(
        self,
        chunk: GraphChunkRef,
        mentions: list[EntityMentionCandidate],
        seen: set[tuple[tuple[str, str], tuple[str, str], str, int]],
    ) -> list[RelationCandidate]:
        if len(mentions) < 2:
            return []
        results: list[RelationCandidate] = []
        for sentence_match in _SENTENCE_RE.finditer(chunk.content_text):
            sentence_start, sentence_end = sentence_match.span(0)
            sentence_mentions = [
                mention
                for mention in mentions
                if sentence_start <= mention.mention_offset_start < sentence_end
            ]
            if len(sentence_mentions) < 2:
                continue
            relation_type = _relation_type_for_sentence(sentence_match.group(0))
            if relation_type is None:
                continue
            for source, target in zip(sentence_mentions, sentence_mentions[1:], strict=False):
                if source.entity_key == target.entity_key:
                    continue
                dedupe_key = (
                    source.entity_key,
                    target.entity_key,
                    relation_type,
                    chunk.document_chunk_id,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append(
                    RelationCandidate(
                        source_key=source.entity_key,
                        target_key=target.entity_key,
                        relation_type=relation_type,
                        relation_label=relation_type,
                        confidence=Decimal("0.70000"),
                        source_document_chunk_id=chunk.document_chunk_id,
                        evidence_text_hash=_sha256(sentence_match.group(0)),
                        metadata_json={
                            "rule_id": f"keyword_{relation_type}",
                            "chunk_index": chunk.chunk_index,
                            "source_mention_hash": source.mention_text_hash,
                            "target_mention_hash": target.mention_text_hash,
                        },
                    )
                )
                if len(results) >= self.max_relations_per_chunk:
                    return results
        return results


def _relation_type_for_sentence(sentence: str) -> str | None:
    lowered = f" {sentence.lower()} "
    for relation_type, keywords in _RELATION_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return relation_type
    return None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
