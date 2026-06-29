from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.schemas.graph import validate_safe_graph_label

_WHITESPACE_RE = re.compile(r"\s+")
_SURROUNDING_PUNCTUATION = "\"'`.,;:()[]{}<>"
_TECHNOLOGY_TERMS = {
    "api",
    "aws",
    "buildkit",
    "ci",
    "docker",
    "fastapi",
    "graph",
    "langchain",
    "langgraph",
    "llm",
    "mcp",
    "ocr",
    "oidc",
    "openai",
    "postgresql",
    "qdrant",
    "rag",
    "react",
    "s3",
    "typescript",
    "ui",
}
_TECHNICAL_SUFFIXES = (
    "adapter",
    "chunk",
    "citation",
    "document",
    "entity",
    "evaluation",
    "extractor",
    "graph",
    "handler",
    "index",
    "job",
    "mention",
    "payload",
    "pipeline",
    "relation",
    "repository",
    "retrieval",
    "retriever",
    "router",
    "schema",
    "service",
    "worker",
)
_LLM_ALLOWED_ENTITY_TYPES = {
    "acronym",
    "artifact",
    "concept",
    "dataset",
    "document",
    "method",
    "organization",
    "paper",
    "system",
    "technology",
}


@dataclass(frozen=True)
class NormalizedGraphEntity:
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...] = ()


class GraphEntityNormalizer:
    """Deterministic, conservative graph label normalizer.

    The normalizer intentionally rejects labels that do not look technical enough for
    the rule-based extractor scope. LLM extraction may pass explicit safe entity
    types, but all labels still go through the same redaction and metadata checks.
    """

    def normalize(
        self,
        value: str,
        *,
        entity_type: str | None = None,
        aliases: list[str] | tuple[str, ...] | None = None,
    ) -> NormalizedGraphEntity | None:
        if not _is_safe_raw_label(value, field_name="canonical_name", max_length=255):
            return None
        canonical = self._normalize_label(value)
        if canonical is None:
            return None
        if _looks_like_person_name(canonical):
            return None
        if not self._looks_like_graph_entity(canonical):
            return None
        if entity_type is not None and not self._is_allowed_typed_entity(entity_type):
            return None

        normalized_type = entity_type or self.infer_entity_type(canonical)
        try:
            safe_name = validate_safe_graph_label(
                canonical,
                field_name="canonical_name",
                max_length=255,
            )
            safe_type = validate_safe_graph_label(
                normalized_type,
                field_name="entity_type",
                max_length=80,
            )
        except ValueError:
            return None

        safe_aliases: list[str] = []
        seen = {safe_name.lower()}
        for alias in aliases or ():
            if not _is_safe_raw_label(alias, field_name="aliases_json", max_length=120):
                continue
            normalized_alias = self._normalize_label(alias)
            if normalized_alias is None:
                continue
            try:
                safe_alias = validate_safe_graph_label(
                    normalized_alias,
                    field_name="aliases_json",
                    max_length=120,
                )
            except ValueError:
                continue
            dedupe_key = safe_alias.lower()
            if dedupe_key not in seen:
                safe_aliases.append(safe_alias)
                seen.add(dedupe_key)

        return NormalizedGraphEntity(
            canonical_name=safe_name,
            entity_type=safe_type,
            aliases=tuple(safe_aliases[:32]),
        )

    def infer_entity_type(self, value: str) -> str:
        normalized = value.strip()
        if normalized.isupper() and 2 <= len(normalized) <= 12:
            return "acronym"
        lowered = normalized.lower()
        if lowered in _TECHNOLOGY_TERMS or any(term in lowered for term in _TECHNOLOGY_TERMS):
            return "technology"
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", normalized):
            return "artifact"
        return "concept"

    def _normalize_label(self, value: str) -> str | None:
        normalized = unicodedata.normalize("NFKC", value)
        normalized = normalized.strip(_SURROUNDING_PUNCTUATION)
        normalized = normalized.replace("_", " ")
        normalized = normalized.replace("-", " ")
        normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
        if len(normalized) < 2 or len(normalized) > 255:
            return None
        return normalized

    def _looks_like_graph_entity(self, value: str) -> bool:
        lowered = value.lower()
        tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
        if not tokens:
            return False
        if lowered in _TECHNOLOGY_TERMS:
            return True
        if any(token in _TECHNOLOGY_TERMS for token in tokens):
            return True
        if any(token.endswith(_TECHNICAL_SUFFIXES) for token in tokens):
            return True
        if re.fullmatch(r"[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+", value):
            return True
        if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", value):
            return True
        return False

    def _is_allowed_typed_entity(self, value: str) -> bool:
        normalized = self._normalize_label(value)
        if normalized is None:
            return False
        return normalized.lower() in _LLM_ALLOWED_ENTITY_TYPES


def _looks_like_person_name(value: str) -> bool:
    tokens = value.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    lowered = value.lower()
    if any(term in lowered for term in _TECHNOLOGY_TERMS):
        return False
    if any(token.lower().endswith(_TECHNICAL_SUFFIXES) for token in tokens):
        return False
    return all(re.fullmatch(r"[A-Z][a-z]{1,40}", token) for token in tokens)


def _is_safe_raw_label(value: str, *, field_name: str, max_length: int) -> bool:
    try:
        validate_safe_graph_label(value, field_name=field_name, max_length=max_length)
    except ValueError:
        return False
    return True
