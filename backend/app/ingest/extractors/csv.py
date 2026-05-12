from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
)
from app.ingest.extractors.text import decode_text_file

CSV_EXTRACTOR_VERSION = "1"


class CsvExtractor:
    name = "csv"
    version = CSV_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        text, encoding = decode_text_file(file_path)
        text = text.lstrip("\ufeff")
        rows = csv_rows_to_text(text)
        pages = [ExtractedPage(text=rows, page_number=None)]
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


def csv_rows_to_text(text: str) -> str:
    try:
        reader = csv.reader(StringIO(text), strict=True)
        lines = [
            " | ".join(cell.strip() for cell in row).strip()
            for row in reader
            if any(cell.strip() for cell in row)
        ]
    except csv.Error as exc:
        raise ExtractionError("text_extraction_failed", "Text extraction failed.") from exc
    if not lines:
        raise ExtractionError("empty_extracted_text", "Extracted text is empty.")
    return "\n".join(lines)
