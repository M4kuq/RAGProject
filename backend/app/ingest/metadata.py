from __future__ import annotations

from dataclasses import dataclass

from app.ingest.extractors.base import ExtractedDocument


@dataclass(frozen=True)
class DocumentIngestMetadata:
    page_count: int | None
    extractor_name: str
    extractor_version: str
    text_char_count: int


def metadata_from_extracted_document(document: ExtractedDocument) -> DocumentIngestMetadata:
    return DocumentIngestMetadata(
        page_count=document.metadata.page_count,
        extractor_name=document.metadata.extractor_name,
        extractor_version=document.metadata.extractor_version,
        text_char_count=document.text_char_count,
    )

