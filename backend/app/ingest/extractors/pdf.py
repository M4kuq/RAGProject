from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
    safe_extraction_failure,
)

PDF_EXTRACTOR_VERSION = "1"


class PdfTextExtractor:
    name = "pdf_text_layer"
    version = PDF_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        try:
            reader = PdfReader(str(file_path))
            pages = [
                ExtractedPage(text=page.extract_text() or "", page_number=index)
                for index, page in enumerate(reader.pages, start=1)
            ]
        except Exception as exc:
            raise safe_extraction_failure() from exc
        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=len(pages),
            ),
        )
