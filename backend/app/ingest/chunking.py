from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class _Token:
    value: str
    page_number: int | None
    section_title: str | None


class FixedTokenChunker:
    def __init__(self, config: ChunkingConfig) -> None:
        self.config = config

    def chunk(self, document: ExtractedDocument, *, document_version_id: int) -> list[Chunk]:
        tokens = _document_tokens(document.pages)
        if not tokens:
            raise ChunkingError("no_chunks_created", "No chunks were created.")

        chunks: list[Chunk] = []
        step = self.config.chunk_size_tokens - self.config.chunk_overlap_tokens
        start = 0
        while start < len(tokens):
            window = tokens[start : start + self.config.chunk_size_tokens]
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
                    )
                )
            start += step

        if not chunks:
            raise ChunkingError("no_chunks_created", "No chunks were created.")
        return chunks


def estimate_token_count(text: str) -> int:
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
