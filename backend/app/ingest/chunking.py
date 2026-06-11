from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.ingest.extractors.base import ExtractedDocument, ExtractedPage
from app.ingest.hashing import chunk_hash, normalize_chunk_text

_TOKEN_RE = re.compile(r"\S+")


class ChunkingError(RuntimeError):
    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 128

    def __post_init__(self) -> None:
        if self.chunk_size_tokens <= 0:
            raise ValueError("chunk_size_tokens must be positive")
        if self.chunk_overlap_tokens < 0:
            raise ValueError("chunk_overlap_tokens must not be negative")
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")


@dataclass(frozen=True)
class Chunk:
    document_version_id: int
    chunk_index: int
    chunk_hash: str
    content_text: str
    token_count: int
    char_count: int
    page_from: int | None
    page_to: int | None
    section_title: str | None
    modality: str = "text"
    metadata_json: dict[str, object] | None = None


@dataclass(frozen=True)
class _Token:
    value: str
    page_number: int | None
    section_title: str | None
    metadata: dict[str, object]


class FixedTokenChunker:
    def __init__(self, config: ChunkingConfig) -> None:
        self.config = config

    def chunk(self, document: ExtractedDocument, *, document_version_id: int) -> list[Chunk]:
        tokens = _document_tokens(document.pages)
        if not tokens:
            raise ChunkingError("no_chunks_created", "No chunks were created.")

        chunks: list[Chunk] = []
        step = self.config.chunk_size_tokens - self.config.chunk_overlap_tokens
        for segment in _token_segments(tokens):
            start = 0
            while start < len(segment):
                window = segment[start : start + self.config.chunk_size_tokens]
                content_text = " ".join(token.value for token in window).strip()
                normalized_text = normalize_chunk_text(content_text)
                if normalized_text:
                    chunk_index = len(chunks)
                    page_numbers = [
                        token.page_number for token in window if token.page_number is not None
                    ]
                    chunks.append(
                        Chunk(
                            document_version_id=document_version_id,
                            chunk_index=chunk_index,
                            chunk_hash=chunk_hash(
                                normalized_chunk_text=normalized_text,
                                document_version_id=document_version_id,
                                chunk_index=chunk_index,
                            ),
                            content_text=content_text,
                            token_count=estimate_token_count(content_text),
                            char_count=len(content_text),
                            page_from=min(page_numbers) if page_numbers else None,
                            page_to=max(page_numbers) if page_numbers else None,
                            section_title=_first_section_title(window),
                            metadata_json=_chunk_metadata(window, chunk_index=chunk_index),
                        )
                    )
                start += step

        if not chunks:
            raise ChunkingError("no_chunks_created", "No chunks were created.")
        return chunks


def estimate_token_count(text: str) -> int:
    # NOTE: This regex word-count drives chunk boundaries during ingestion and is
    # intentionally kept separate from the budgeting estimator in app.core.tokens
    # (estimate_tokens). The budgeting estimator is Japanese-aware (non-ASCII ~1
    # token/char); changing the chunking estimator here would shift chunk
    # boundaries and broadly affect fixtures/tests, so the difference is
    # intentional for now.
    return len(_TOKEN_RE.findall(text))


def _document_tokens(pages: list[ExtractedPage]) -> list[_Token]:
    tokens: list[_Token] = []
    for page in pages:
        text = _normalize_page_text(page.text)
        tokens.extend(
            _Token(
                value=match.group(0),
                page_number=page.page_number,
                section_title=page.section_title,
                metadata=page.metadata,
            )
            for match in _TOKEN_RE.finditer(text)
        )
    return tokens


def _normalize_page_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _first_section_title(tokens: list[_Token]) -> str | None:
    for token in tokens:
        if token.section_title:
            return token.section_title
    return None


def _token_segments(tokens: list[_Token]) -> list[list[_Token]]:
    segments: list[list[_Token]] = []
    current: list[_Token] = []
    current_key: tuple[object, ...] | None = None
    has_current_key = False
    for token in tokens:
        key = _metadata_boundary_key(token.metadata)
        if current and has_current_key and key != current_key:
            segments.append(current)
            current = []
        current.append(token)
        current_key = key
        has_current_key = True
    if current:
        segments.append(current)
    return segments


def _metadata_boundary_key(metadata: dict[str, object]) -> tuple[object, ...] | None:
    parent_key = metadata.get("parent_chunk_key")
    if not isinstance(parent_key, str) or not parent_key:
        return None
    structure_type = metadata.get("structure_type")
    structure_key = structure_type if isinstance(structure_type, str) else "structured"
    if structure_key == "excel_sheet":
        table_index = metadata.get("table_index")
        safe_table_index = (
            table_index
            if isinstance(table_index, int) and not isinstance(table_index, bool)
            else None
        )
        return (structure_key, parent_key, safe_table_index)
    return (structure_key, parent_key)


def _chunk_metadata(tokens: list[_Token], *, chunk_index: int) -> dict[str, object] | None:
    metadata_items = [token.metadata for token in tokens if token.metadata]
    if not metadata_items:
        return None
    base = dict(metadata_items[0])
    safe: dict[str, object] = {
        key: value
        for key, value in base.items()
        if isinstance(key, str) and _is_safe_metadata_value(value)
    }
    if not safe:
        return None
    safe["chunk_level"] = "child"
    safe["chunk_index"] = chunk_index
    parent_key = safe.get("parent_chunk_key")
    if isinstance(parent_key, str) and parent_key:
        safe["child_chunk_key"] = f"{parent_key}:chunk:{chunk_index}"
    _merge_int_range(safe, metadata_items, "row_from", min)
    _merge_int_range(safe, metadata_items, "row_to", max)
    _merge_int_range(safe, metadata_items, "column_from", min)
    _merge_int_range(safe, metadata_items, "column_to", max)
    _merge_int_range(safe, metadata_items, "slide_number", min)
    return safe


def _merge_int_range(
    target: dict[str, object],
    metadata_items: list[dict[str, object]],
    key: str,
    reducer: Any,
) -> None:
    values = [item.get(key) for item in metadata_items]
    int_values = [
        value for value in values if isinstance(value, int) and not isinstance(value, bool)
    ]
    if int_values:
        target[key] = int(reducer(int_values))


def _is_safe_metadata_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= 255
    if isinstance(value, bool):
        return True
    if isinstance(value, int | float):
        return True
    if isinstance(value, list):
        return all(_is_safe_metadata_value(item) for item in value[:20])
    return False
