from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExtractionInputMetadata:
    file_name: str
    mime_type: str
    file_size_bytes: int


@dataclass(frozen=True)
class ExtractionMetadata:
    extractor_name: str
    extractor_version: str
    page_count: int | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    page_number: int | None = None
    section_title: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedDocument:
    pages: list[ExtractedPage]
    metadata: ExtractionMetadata

    @property
    def text_char_count(self) -> int:
        return sum(len(page.text) for page in self.pages)


class TextExtractor(Protocol):
    name: str
    version: str

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument: ...


class ExtractionError(RuntimeError):
    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


def ensure_non_empty_text(pages: list[ExtractedPage]) -> None:
    if not any(page.text.strip() for page in pages):
        raise ExtractionError("empty_extracted_text", "Extracted text is empty.")


def safe_extraction_failure() -> ExtractionError:
    return ExtractionError("text_extraction_failed", "Text extraction failed.")
