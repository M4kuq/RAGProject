from __future__ import annotations

import re
from pathlib import Path

from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
)
from app.ingest.extractors.text import decode_text_file

MARKDOWN_EXTRACTOR_VERSION = "1"
_ATX_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


class MarkdownExtractor:
    name = "markdown"
    version = MARKDOWN_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        text, encoding = decode_text_file(file_path)
        text = text.lstrip("\ufeff")
        pages = markdown_sections(text)
        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
                extra={"encoding": encoding},
            ),
        )


def markdown_sections(text: str) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        heading = _ATX_HEADING_RE.match(line.strip())
        if heading is not None:
            if current_lines:
                pages.append(
                    ExtractedPage(
                        text="\n".join(current_lines).strip(),
                        page_number=None,
                        section_title=current_title,
                    )
                )
                current_lines = []
            current_title = heading.group(2).strip()
        current_lines.append(line)

    if current_lines:
        pages.append(
            ExtractedPage(
                text="\n".join(current_lines).strip(),
                page_number=None,
                section_title=current_title,
            )
        )
    return [page for page in pages if page.text.strip()]
