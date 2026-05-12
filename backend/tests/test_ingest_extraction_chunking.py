from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app.core.config import get_settings
from app.core.errors import UnsafeFileRejected
from app.core.job_utils import redact_error_message
from app.ingest.chunking import ChunkingConfig, ChunkingError, FixedTokenChunker
from app.ingest.extractors.base import ExtractedDocument, ExtractedPage, ExtractionError
from app.ingest.extractors.csv import CsvExtractor
from app.ingest.extractors.dispatcher import ExtractorDispatcher
from app.ingest.extractors.docx import DocxExtractor
from app.ingest.extractors.markdown import MarkdownExtractor
from app.ingest.extractors.pdf import PdfTextExtractor
from app.ingest.extractors.text import PlainTextExtractor
from app.ingest.hashing import chunk_hash, normalize_chunk_text
from app.ingest.metadata import metadata_from_extracted_document
from app.storage.validators import validate_upload


def test_text_extractor_decodes_utf8_utf8_sig_and_cp932(tmp_path: Path) -> None:
    extractor = PlainTextExtractor()
    cases = [
        ("utf8.txt", "hello utf8".encode("utf-8"), "hello utf8"),
        ("bom.txt", "\ufeffhello bom".encode("utf-8"), "hello bom"),
        ("cp932.txt", b"\x82\xa0", "\u3042"),
    ]

    for file_name, content, expected in cases:
        path = tmp_path / file_name
        path.write_bytes(content)
        extracted = extractor.extract(path, _metadata(file_name, "text/plain", len(content)))
        assert extracted.pages[0].text == expected
        assert extracted.metadata.extractor_name == "plain_text"


def test_text_extractor_failure_and_empty_text(tmp_path: Path) -> None:
    extractor = PlainTextExtractor()
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\x81")
    with pytest.raises(ExtractionError) as invalid_exc:
        extractor.extract(invalid, _metadata("invalid.txt", "text/plain", 1))
    assert invalid_exc.value.error_code == "text_extraction_failed"

    empty = tmp_path / "empty.txt"
    empty.write_text("   \n\t", encoding="utf-8")
    with pytest.raises(ExtractionError) as empty_exc:
        extractor.extract(empty, _metadata("empty.txt", "text/plain", 5))
    assert empty_exc.value.error_code == "empty_extracted_text"


def test_markdown_extraction_keeps_atx_section_titles(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# Intro\nbody\n## Detail\nmore\n", encoding="utf-8")

    extracted = MarkdownExtractor().extract(path, _metadata("guide.md", "text/markdown", 27))

    assert [page.section_title for page in extracted.pages] == ["Intro", "Detail"]
    assert "# Intro" in extracted.pages[0].text
    assert extracted.metadata.extractor_name == "markdown"


def test_csv_extraction_and_malformed_handling(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("name,value\nalpha,1\n", encoding="utf-8")

    extracted = CsvExtractor().extract(path, _metadata("data.csv", "text/csv", 19))

    assert extracted.pages[0].text == "name | value\nalpha | 1"

    bom_path = tmp_path / "bom.csv"
    bom_path.write_bytes("\ufeffname,value\nalpha,1\n".encode("utf-8"))
    bom_extracted = CsvExtractor().extract(bom_path, _metadata("bom.csv", "text/csv", 22))
    assert bom_extracted.pages[0].text.startswith("name | value")

    malformed = tmp_path / "bad.csv"
    malformed.write_text('"unterminated', encoding="utf-8")
    with pytest.raises(ExtractionError) as exc:
        CsvExtractor().extract(malformed, _metadata("bad.csv", "text/csv", 13))
    assert exc.value.error_code == "text_extraction_failed"


def test_docx_extraction_includes_paragraphs_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "doc.docx"
    document = Document()
    document.add_paragraph("Paragraph text")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    document.save(path)

    extracted = DocxExtractor().extract(
        path,
        _metadata(
            "doc.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            path.stat().st_size,
        ),
    )

    assert "Paragraph text" in extracted.pages[0].text
    assert "A | B" in extracted.pages[0].text
    assert extracted.metadata.extractor_name == "docx"


def test_docx_upload_validation_rejects_zip_bomb_shape() -> None:
    content = _docx_zip_with_large_document_xml()

    with pytest.raises(UnsafeFileRejected):
        validate_upload(
            filename="bomb.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=content,
            max_bytes=len(content) + 1,
            allowed_extensions=[".docx"],
        )


def test_pdf_text_layer_extraction(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(_minimal_pdf("Hello PDF text"))

    extracted = PdfTextExtractor().extract(
        path, _metadata("sample.pdf", "application/pdf", path.stat().st_size)
    )

    assert "Hello PDF text" in extracted.pages[0].text
    assert extracted.pages[0].page_number == 1
    assert extracted.metadata.page_count == 1


def test_extractor_dispatcher_validates_extension_and_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "UPLOAD_ALLOWED_EXTENSIONS",
        '[".pdf",".docx",".txt",".md",".markdown",".csv"]',
    )
    get_settings.cache_clear()
    dispatcher = ExtractorDispatcher()

    assert (
        dispatcher.select(file_name="a.pdf", mime_type="application/pdf").name
        == "pdf_text_layer"
    )
    assert dispatcher.select(file_name="a.markdown", mime_type="text/markdown").name == "markdown"

    with pytest.raises(ExtractionError) as unsupported:
        dispatcher.select(file_name="a.exe", mime_type="application/octet-stream")
    assert unsupported.value.error_code == "unsupported_file_type"

    with pytest.raises(ExtractionError) as mismatch:
        dispatcher.select(file_name="a.pdf", mime_type="text/plain")
    assert mismatch.value.error_code == "mime_type_mismatch"
    get_settings.cache_clear()


def test_metadata_and_chunking_are_deterministic() -> None:
    document = ExtractedDocument(
        pages=[
            ExtractedPage("alpha beta gamma delta", page_number=1, section_title="Intro"),
            ExtractedPage("epsilon zeta eta theta", page_number=2, section_title="Next"),
        ],
        metadata=_extraction_metadata(page_count=2),
    )
    metadata = metadata_from_extracted_document(document)
    chunker = FixedTokenChunker(ChunkingConfig(chunk_size_tokens=5, chunk_overlap_tokens=2))

    first = chunker.chunk(document, document_version_id=10)
    second = chunker.chunk(document, document_version_id=10)

    assert first == second
    assert metadata.page_count == 2
    assert [chunk.chunk_index for chunk in first] == [0, 1, 2]
    assert first[0].token_count == 5
    assert first[0].char_count == len(first[0].content_text)
    assert first[0].page_from == 1
    assert first[0].page_to == 2
    assert first[0].section_title == "Intro"
    assert first[0].chunk_hash == chunk_hash(
        normalized_chunk_text=normalize_chunk_text(first[0].content_text),
        document_version_id=10,
        chunk_index=0,
    )


def test_chunking_rejects_invalid_config_and_no_chunks() -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size_tokens=5, chunk_overlap_tokens=5)

    empty = ExtractedDocument(
        pages=[ExtractedPage("   ", page_number=None)],
        metadata=_extraction_metadata(page_count=None),
    )
    with pytest.raises(ChunkingError) as exc:
        FixedTokenChunker(ChunkingConfig(chunk_size_tokens=5, chunk_overlap_tokens=1)).chunk(
            empty, document_version_id=1
        )
    assert exc.value.error_code == "no_chunks_created"


def test_chunking_overlap_zero_and_size_boundaries() -> None:
    exact = ExtractedDocument(
        pages=[ExtractedPage("one two three", page_number=1)],
        metadata=_extraction_metadata(page_count=1),
    )
    plus_one = ExtractedDocument(
        pages=[ExtractedPage("one two three four", page_number=1)],
        metadata=_extraction_metadata(page_count=1),
    )
    chunker = FixedTokenChunker(ChunkingConfig(chunk_size_tokens=3, chunk_overlap_tokens=0))

    exact_chunks = chunker.chunk(exact, document_version_id=1)
    plus_one_chunks = chunker.chunk(plus_one, document_version_id=1)

    assert [chunk.content_text for chunk in exact_chunks] == ["one two three"]
    assert [chunk.content_text for chunk in plus_one_chunks] == ["one two three", "four"]


def test_redaction_helper_removes_sensitive_error_content() -> None:
    assert redact_error_message("chunk content_text contained raw document text") == (
        "Job failed with a redacted error."
    )


def _metadata(file_name: str, mime_type: str, file_size_bytes: int):
    from app.ingest.extractors.base import ExtractionInputMetadata

    return ExtractionInputMetadata(
        file_name=file_name,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
    )


def _extraction_metadata(page_count: int | None):
    from app.ingest.extractors.base import ExtractionMetadata

    return ExtractionMetadata(
        extractor_name="test",
        extractor_version="1",
        page_count=page_count,
    )


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length "
        + str(len(stream)).encode("ascii")
        + b" >> stream\n"
        + stream
        + b"\nendstream endobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(content))
        content.extend(obj)
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(content)


def _docx_zip_with_large_document_xml() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "x" * (5 * 1024 * 1024 + 1))
    return buffer.getvalue()
