from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal

from app.ingest.extractors.base import ExtractedDocument, ExtractedPage
from app.ingest.hashing import chunk_hash, normalize_chunk_text

_TOKEN_RE = re.compile(r"\S+")

ChunkTokenizerProfile = Literal["whitespace_v1", "japanese_aware_v1"]
ChunkBoundaryProfile = Literal["fixed_v1", "structure_v1"]


class ChunkingError(RuntimeError):
    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 128
    tokenizer_profile: ChunkTokenizerProfile = "whitespace_v1"
    boundary_profile: ChunkBoundaryProfile = "fixed_v1"

    def __post_init__(self) -> None:
        if self.chunk_size_tokens <= 0:
            raise ValueError("chunk_size_tokens must be positive")
        if self.chunk_overlap_tokens < 0:
            raise ValueError("chunk_overlap_tokens must not be negative")
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        if self.tokenizer_profile not in {"whitespace_v1", "japanese_aware_v1"}:
            raise ValueError("unsupported tokenizer_profile")
        if self.boundary_profile not in {"fixed_v1", "structure_v1"}:
            raise ValueError("unsupported boundary_profile")


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
    separator_before: str = " "


class FixedTokenChunker:
    def __init__(self, config: ChunkingConfig) -> None:
        self.config = config

    def chunk(self, document: ExtractedDocument, *, document_version_id: int) -> list[Chunk]:
        tokens = _document_tokens(
            document.pages,
            tokenizer_profile=self.config.tokenizer_profile,
        )
        if not tokens:
            raise ChunkingError("no_chunks_created", "No chunks were created.")

        chunks: list[Chunk] = []
        step = self.config.chunk_size_tokens - self.config.chunk_overlap_tokens
        for segment in _token_segments(
            tokens,
            boundary_profile=self.config.boundary_profile,
        ):
            start = 0
            while start < len(segment):
                window = segment[start : start + self.config.chunk_size_tokens]
                content_text = _render_window(
                    window,
                    tokenizer_profile=self.config.tokenizer_profile,
                )
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
                            token_count=len(window),
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


@dataclass(frozen=True)
class ChunkStatistics:
    chunk_count: int
    min_char_count: int
    max_char_count: int
    median_char_count: int

    def to_payload(self) -> dict[str, int]:
        # Flat ``*_count`` int keys so the values survive the worker result
        # sanitizer (sanitize_result_json keeps int-valued ``_count`` keys and
        # drops nested dicts).
        return {
            "chunk_min_char_count": self.min_char_count,
            "chunk_max_char_count": self.max_char_count,
            "chunk_median_char_count": self.median_char_count,
        }


def chunk_statistics(char_counts: Sequence[int]) -> ChunkStatistics:
    """Per-document chunk character-length telemetry for the ingest job result."""
    counts = [int(value) for value in char_counts]
    if not counts:
        return ChunkStatistics(
            chunk_count=0,
            min_char_count=0,
            max_char_count=0,
            median_char_count=0,
        )
    return ChunkStatistics(
        chunk_count=len(counts),
        min_char_count=min(counts),
        max_char_count=max(counts),
        median_char_count=int(median(counts)),
    )


def estimate_token_count(text: str) -> int:
    # NOTE: This regex word-count drives chunk boundaries during ingestion and is
    # intentionally kept separate from the budgeting estimator in app.core.tokens
    # (estimate_tokens). The budgeting estimator is Japanese-aware (non-ASCII ~1
    # token/char); changing the chunking estimator here would shift chunk
    # boundaries and broadly affect fixtures/tests, so the difference is
    # intentional for now.
    return len(_TOKEN_RE.findall(text))


def chunk_profile_fingerprint(config: ChunkingConfig) -> str:
    payload = {
        "schema_version": "rag60.chunk_profile.v1",
        "chunk_size_tokens": config.chunk_size_tokens,
        "chunk_overlap_tokens": config.chunk_overlap_tokens,
        "tokenizer_profile": config.tokenizer_profile,
        "boundary_profile": config.boundary_profile,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_tokens(
    pages: list[ExtractedPage],
    *,
    tokenizer_profile: ChunkTokenizerProfile,
) -> list[_Token]:
    if tokenizer_profile == "japanese_aware_v1":
        return _japanese_aware_document_tokens(pages)
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


def _japanese_aware_document_tokens(pages: list[ExtractedPage]) -> list[_Token]:
    tokens: list[_Token] = []
    for page in pages:
        text = _normalize_page_text(page.text)
        pending_separator = "\n" if tokens else ""
        index = 0
        while index < len(text):
            if text[index].isspace():
                whitespace_start = index
                while index < len(text) and text[index].isspace():
                    index += 1
                whitespace = text[whitespace_start:index]
                pending_separator = "\n" if "\n" in whitespace else " "
                continue
            if ord(text[index]) < 128:
                ascii_start = index
                while (
                    index < len(text)
                    and not text[index].isspace()
                    and ord(text[index]) < 128
                ):
                    index += 1
                ascii_run = text[ascii_start:index]
                for offset in range(0, len(ascii_run), 4):
                    value = ascii_run[offset : offset + 4]
                    tokens.append(
                        _Token(
                            value=value,
                            page_number=page.page_number,
                            section_title=page.section_title,
                            metadata=page.metadata,
                            separator_before=pending_separator if offset == 0 else "",
                        )
                    )
                pending_separator = ""
                continue
            tokens.append(
                _Token(
                    value=text[index],
                    page_number=page.page_number,
                    section_title=page.section_title,
                    metadata=page.metadata,
                    separator_before=pending_separator,
                )
            )
            pending_separator = ""
            index += 1
    return tokens


def _normalize_page_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _render_window(
    tokens: list[_Token],
    *,
    tokenizer_profile: ChunkTokenizerProfile,
) -> str:
    if tokenizer_profile == "whitespace_v1":
        return " ".join(token.value for token in tokens).strip()
    return "".join(f"{token.separator_before}{token.value}" for token in tokens).strip()


def _first_section_title(tokens: list[_Token]) -> str | None:
    for token in tokens:
        if token.section_title:
            return token.section_title
    return None


def _token_segments(
    tokens: list[_Token],
    *,
    boundary_profile: ChunkBoundaryProfile,
) -> list[list[_Token]]:
    segments: list[list[_Token]] = []
    current: list[_Token] = []
    current_key: tuple[object, ...] | None = None
    has_current_key = False
    for token in tokens:
        key = _metadata_boundary_key(token.metadata)
        if key is None and boundary_profile == "structure_v1":
            if token.section_title:
                key = ("section", token.section_title)
            elif token.page_number is not None:
                key = ("page", token.page_number)
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
