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


@dataclass(frozen=True)
class NormalizedGraphEntity:
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...] = ()


class GraphEntityNormalizer:
    """Deterministic, conservative graph label normalizer.

    The normalizer intentionally rejects labels that do not look technical enough for
    the current graph index scope. This keeps private names or incidental text out of
    graph tables while PR-47 ships a rule-based baseline.
    """

    def normalize(
        self,
        value: str,
        *,
        entity_type: str | None = None,
        aliases: list[str] | tuple[str, ...] | None = None,
    ) -> NormalizedGraphEntity | None:
        canonical = self._normalize_label(value)
        if canonical is None or not self._looks_like_graph_entity(canonical):
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
