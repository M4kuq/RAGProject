from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.core.config import get_settings
from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
    safe_extraction_failure,
)

OFFICE_PARENT_CHILD_SCHEMA_VERSION = "phase2.parent_child.v1"
XLSX_EXTRACTOR_VERSION = "1"
PPTX_EXTRACTOR_VERSION = "1"

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

REL_ID_ATTR = f"{{{NS['r']}}}id"
CELL_REF_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


@dataclass(frozen=True)
class _SheetRef:
    name: str
    path: str
    state: str | None


@dataclass(frozen=True)
class _Cell:
    row: int
    column: int
    value: str


class ExcelExtractor:
    name = "xlsx"
    version = XLSX_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        settings = get_settings()
        try:
            with zipfile.ZipFile(file_path) as archive:
                shared_strings = _shared_strings(archive)
                pages: list[ExtractedPage] = []
                total_cells = 0
                for sheet_index, sheet in enumerate(_workbook_sheets(archive), start=1):
                    if sheet.state not in {None, "visible"}:
                        continue
                    sheet_pages, cell_count = _extract_sheet_pages(
                        archive,
                        sheet=sheet,
                        sheet_index=sheet_index,
                        shared_strings=shared_strings,
                        max_rows=settings.ingest_office_max_rows_per_sheet,
                        max_cells=max(settings.ingest_office_max_cells - total_cells, 0),
                        rows_per_chunk=settings.ingest_office_rows_per_chunk,
                    )
                    pages.extend(sheet_pages)
                    total_cells += cell_count
                    if total_cells >= settings.ingest_office_max_cells:
                        break
                    if len(pages) >= settings.ingest_office_max_pages:
                        pages = pages[: settings.ingest_office_max_pages]
                        break
        except Exception as exc:
            raise safe_extraction_failure() from exc

        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
                extra={"office_format": "xlsx", "visible_sheet_page_count": len(pages)},
            ),
        )


class PowerPointExtractor:
    name = "pptx"
    version = PPTX_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        settings = get_settings()
        try:
            with zipfile.ZipFile(file_path) as archive:
                pages = _extract_slide_pages(
                    archive,
                    max_slides=settings.ingest_office_max_slides,
                )
        except Exception as exc:
            raise safe_extraction_failure() from exc

        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=len(pages),
                extra={"office_format": "pptx"},
            ),
        )


def _workbook_sheets(archive: zipfile.ZipFile) -> list[_SheetRef]:
    workbook = _xml_root(archive, "xl/workbook.xml")
    relationships = _relationships(archive, "xl/_rels/workbook.xml.rels", base_dir="xl")
    refs: list[_SheetRef] = []
    for sheet in workbook.findall(".//s:sheet", NS):
        rel_id = sheet.attrib.get(REL_ID_ATTR)
        target = relationships.get(rel_id or "")
        name = _safe_metadata_text(sheet.attrib.get("name") or "Sheet")
        if not target or not name:
            continue
        refs.append(_SheetRef(name=name, path=target, state=sheet.attrib.get("state")))
    return refs


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml_root(archive, "xl/sharedStrings.xml")
    values: list[str] = []
    for item in root.findall(".//s:si", NS):
        values.append(_safe_text(_text_nodes(item)))
    return values


def _extract_sheet_pages(
    archive: zipfile.ZipFile,
    *,
    sheet: _SheetRef,
    sheet_index: int,
    shared_strings: list[str],
    max_rows: int,
    max_cells: int,
    rows_per_chunk: int,
) -> tuple[list[ExtractedPage], int]:
    if max_cells < 1:
        return [], 0
    root = _xml_root(archive, sheet.path)
    rows: list[tuple[int, list[_Cell]]] = []
    total_cells = 0
    for row_element in root.findall(".//s:sheetData/s:row", NS):
        if len(rows) >= max_rows or total_cells >= max_cells:
            break
        row_number = _safe_int(row_element.attrib.get("r")) or len(rows) + 1
        cells: list[_Cell] = []
        for cell_element in row_element.findall("s:c", NS):
            if total_cells >= max_cells:
                break
            cell = _cell_value(cell_element, shared_strings=shared_strings)
            if cell is None:
                continue
            cell_ref = cell_element.attrib.get("r") or ""
            column_index = _column_index_from_cell_ref(cell_ref) or len(cells) + 1
            cells.append(_Cell(row=row_number, column=column_index, value=cell))
            total_cells += 1
        if cells:
            rows.append((row_number, cells))
    return _sheet_rows_to_pages(
        sheet=sheet,
        sheet_index=sheet_index,
        rows=rows,
        rows_per_chunk=rows_per_chunk,
    ), total_cells


def _sheet_rows_to_pages(
    *,
    sheet: _SheetRef,
    sheet_index: int,
    rows: list[tuple[int, list[_Cell]]],
    rows_per_chunk: int,
) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    table_index = 1
    current_group: list[tuple[int, list[_Cell]]] = []
    previous_row: int | None = None
    for row_number, cells in rows:
        if previous_row is not None and row_number > previous_row + 1:
            pages.extend(
                _row_group_pages(
                    sheet=sheet,
                    sheet_index=sheet_index,
                    table_index=table_index,
                    rows=current_group,
                    rows_per_chunk=rows_per_chunk,
                )
            )
            table_index += 1
            current_group = []
        current_group.append((row_number, cells))
        previous_row = row_number
    pages.extend(
        _row_group_pages(
            sheet=sheet,
            sheet_index=sheet_index,
            table_index=table_index,
            rows=current_group,
            rows_per_chunk=rows_per_chunk,
        )
    )
    return pages


def _row_group_pages(
    *,
    sheet: _SheetRef,
    sheet_index: int,
    table_index: int,
    rows: list[tuple[int, list[_Cell]]],
    rows_per_chunk: int,
) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    for start in range(0, len(rows), rows_per_chunk):
        window = rows[start : start + rows_per_chunk]
        if not window:
            continue
        row_numbers = [row for row, _ in window]
        cells = [cell for _, row_cells in window for cell in row_cells]
        column_numbers = [cell.column for cell in cells]
        lines = [
            f"Sheet: {sheet.name}",
            f"Rows: {min(row_numbers)}-{max(row_numbers)}",
        ]
        for row_number, row_cells in window:
            cell_text = " | ".join(
                f"{_column_label(cell.column)}={cell.value}" for cell in row_cells
            )
            lines.append(f"R{row_number}: {cell_text}")
        metadata = {
            "parent_child_schema_version": OFFICE_PARENT_CHILD_SCHEMA_VERSION,
            "structure_type": "excel_sheet",
            "chunk_level": "child",
            "parent_chunk_key": f"xlsx:sheet:{sheet_index}",
            "parent_title": sheet.name,
            "sheet_name": sheet.name,
            "row_from": min(row_numbers),
            "row_to": max(row_numbers),
            "column_from": min(column_numbers),
            "column_to": max(column_numbers),
            "table_index": table_index,
        }
        pages.append(
            ExtractedPage(
                text="\n".join(lines),
                page_number=None,
                section_title=f"Sheet: {sheet.name}",
                metadata=metadata,
            )
        )
    return pages


def _cell_value(cell_element: ElementTree.Element, *, shared_strings: list[str]) -> str | None:
    cell_type = cell_element.attrib.get("t")
    if cell_type == "inlineStr":
        value = _safe_text(_text_nodes(cell_element))
    else:
        value_element = cell_element.find("s:v", NS)
        if value_element is None or value_element.text is None:
            return None
        value = value_element.text
        if cell_type == "s":
            index = _safe_int(value)
            value = (
                shared_strings[index] if index is not None and index < len(shared_strings) else ""
            )
        elif cell_type == "b":
            value = "TRUE" if value == "1" else "FALSE"
    value = _safe_text(value)
    return value or None


def _extract_slide_pages(
    archive: zipfile.ZipFile,
    *,
    max_slides: int,
) -> list[ExtractedPage]:
    presentation = _xml_root(archive, "ppt/presentation.xml")
    relationships = _relationships(archive, "ppt/_rels/presentation.xml.rels", base_dir="ppt")
    pages: list[ExtractedPage] = []
    for slide_number, slide_id in enumerate(presentation.findall(".//p:sldId", NS), start=1):
        if len(pages) >= max_slides:
            break
        rel_id = slide_id.attrib.get(REL_ID_ATTR)
        target = relationships.get(rel_id or "")
        if not target:
            continue
        page = _slide_page(archive, slide_path=target, slide_number=slide_number)
        if page is not None:
            pages.append(page)
    return pages


def _slide_page(
    archive: zipfile.ZipFile,
    *,
    slide_path: str,
    slide_number: int,
) -> ExtractedPage | None:
    root = _xml_root(archive, slide_path)
    if root.attrib.get("show") == "0":
        return None
    shape_texts: list[str] = []
    for shape in root.findall(".//p:sp", NS):
        text = _safe_text(_text_nodes(shape))
        if text:
            shape_texts.append(text)
    table_texts: list[str] = []
    for table_index, table in enumerate(root.findall(".//a:tbl", NS), start=1):
        rows: list[str] = []
        for row in table.findall("a:tr", NS):
            cells = [_safe_text(_text_nodes(cell)) for cell in row.findall("a:tc", NS)]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            table_texts.append(f"Table {table_index}:\n" + "\n".join(rows))
    if not shape_texts and not table_texts:
        return None
    title = shape_texts[0] if shape_texts else f"Slide {slide_number}"
    safe_title = _safe_metadata_text(title)
    lines = [f"Slide {slide_number}: {safe_title}"]
    lines.extend(f"Shape {index}: {text}" for index, text in enumerate(shape_texts, start=1))
    lines.extend(table_texts)
    metadata = {
        "parent_child_schema_version": OFFICE_PARENT_CHILD_SCHEMA_VERSION,
        "structure_type": "powerpoint_slide",
        "chunk_level": "child",
        "parent_chunk_key": f"pptx:slide:{slide_number}",
        "parent_title": safe_title,
        "slide_number": slide_number,
        "slide_title": safe_title,
        "shape_count": len(shape_texts),
        "table_count": len(table_texts),
    }
    return ExtractedPage(
        text="\n".join(lines),
        page_number=slide_number,
        section_title=f"Slide {slide_number}: {safe_title}",
        metadata=metadata,
    )


def _relationships(
    archive: zipfile.ZipFile,
    path: str,
    *,
    base_dir: str,
) -> dict[str, str]:
    root = _xml_root(archive, path)
    relationships: dict[str, str] = {}
    for rel in root.findall("rel:Relationship", NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        mode = rel.attrib.get("TargetMode")
        if not rel_id or not target or mode == "External":
            continue
        normalized = posixpath.normpath(posixpath.join(base_dir, target))
        if normalized.startswith("../") or normalized.startswith("/"):
            continue
        relationships[rel_id] = normalized
    return relationships


def _xml_root(archive: zipfile.ZipFile, path: str) -> ElementTree.Element:
    try:
        with archive.open(path) as handle:
            return ElementTree.fromstring(handle.read())
    except Exception as exc:
        raise safe_extraction_failure() from exc


def _text_nodes(element: ElementTree.Element) -> str:
    nodes = [*element.findall(".//a:t", NS), *element.findall(".//s:t", NS)]
    return " ".join(text for text in (node.text for node in nodes) if text)


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("\x00", " ").split())[:500]


def _safe_metadata_text(value: object) -> str:
    text = _safe_text(value)[:120]
    if SECRET_ASSIGNMENT_RE.search(text) or URL_RE.search(text) or EMAIL_RE.search(text):
        return "redacted"
    return text


def _safe_int(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _column_index_from_cell_ref(cell_ref: str) -> int | None:
    match = CELL_REF_RE.match(cell_ref)
    if match is None:
        return None
    column = 0
    for char in match.group(1):
        column = column * 26 + ord(char) - ord("A") + 1
    return column


def _column_label(index: int) -> str:
    if index < 1:
        return "?"
    label = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label
