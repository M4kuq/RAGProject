from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.ingest.extractors.base import ExtractionError, TextExtractor
from app.ingest.extractors.csv import CsvExtractor
from app.ingest.extractors.docx import DocxExtractor
from app.ingest.extractors.markdown import MarkdownExtractor
from app.ingest.extractors.pdf import PdfTextExtractor
from app.ingest.extractors.text import PlainTextExtractor
from app.storage.validators import allowed_mime_types_for_extension, extension_from_file_name


class ExtractorDispatcher:
    def __init__(self, extractors: dict[str, TextExtractor] | None = None) -> None:
        self._extractors = extractors or {
            ".pdf": PdfTextExtractor(),
            ".docx": DocxExtractor(),
            ".txt": PlainTextExtractor(),
            ".md": MarkdownExtractor(),
            ".markdown": MarkdownExtractor(),
            ".csv": CsvExtractor(),
        }

    def select(self, *, file_name: str, mime_type: str) -> TextExtractor:
        try:
            extension = extension_from_file_name(file_name)
        except Exception as exc:
            raise ExtractionError("unsupported_file_type", "Unsupported file type.") from exc

        allowed_extensions = {item.lower() for item in get_settings().upload_allowed_extensions}
        if extension not in allowed_extensions:
            raise ExtractionError("unsupported_file_type", "Unsupported file type.")

        normalized_mime = (mime_type or "application/octet-stream").split(";", 1)[0].lower()
        if normalized_mime not in allowed_mime_types_for_extension(extension):
            raise ExtractionError("mime_type_mismatch", "File type does not match MIME type.")

        extractor = self._extractors.get(extension)
        if extractor is None:
            raise ExtractionError("unsupported_file_type", "Unsupported file type.")
        return extractor

    def select_for_path(self, file_path: Path, *, mime_type: str) -> TextExtractor:
        return self.select(file_name=file_path.name, mime_type=mime_type)
