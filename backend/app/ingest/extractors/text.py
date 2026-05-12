from __future__ import annotations

from pathlib import Path

from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
)

TEXT_EXTRACTOR_VERSION = "1"
TEXT_DECODINGS = ("utf-8", "utf-8-sig", "cp932")


class PlainTextExtractor:
    name = "plain_text"
    version = TEXT_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        text, encoding = decode_text_file(file_path)
        text = text.lstrip("\ufeff")
        pages = [ExtractedPage(text=text, page_number=None)]
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


def decode_text_file(file_path: Path) -> tuple[str, str]:
    content = file_path.read_bytes()
    for encoding in TEXT_DECODINGS:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ExtractionError("text_extraction_failed", "Text extraction failed.")
