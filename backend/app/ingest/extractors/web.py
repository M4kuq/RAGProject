from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from app.core.config import get_settings
from app.core.errors import UnsafeFileRejected
from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    ExtractionInputMetadata,
    ExtractionMetadata,
    ensure_non_empty_text,
    safe_extraction_failure,
)
from app.ingest.extractors.text import decode_text_file
from app.storage.validators import validate_xml_text_safety

WEB_INGEST_SCHEMA_VERSION = "phase2.web_ingest.v1"
HTML_EXTRACTOR_VERSION = "1"
XML_EXTRACTOR_VERSION = "1"

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

_BLOCKED_HTML_TAGS = {"script", "style", "noscript", "iframe", "object", "embed", "template"}
_BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "table", "ul", "ol"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TABLE_CELL_TAGS = {"td", "th"}
_VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"


@dataclass(frozen=True)
class _HtmlBlock:
    text: str
    heading_path: tuple[str, ...]
    element_type: str
    element_index: int
    parent_index: int


class HtmlExtractor:
    name = "html"
    version = HTML_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        try:
            text, encoding = decode_text_file(file_path)
            parser = _SafeHtmlTextParser(max_elements=get_settings().ingest_html_max_elements)
            parser.feed(text)
            parser.close()
            pages = _html_blocks_to_pages(
                parser.blocks,
                metadata=metadata,
                html_title=parser.title,
            )
        except ExtractionError:
            raise
        except Exception as exc:
            raise safe_extraction_failure() from exc

        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
                extra={
                    "encoding": encoding,
                    "html_title": _safe_metadata_text(parser.title),
                    "source_type": metadata.source_type,
                },
            ),
        )


class XmlExtractor:
    name = "xml"
    version = XML_EXTRACTOR_VERSION

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        try:
            text, encoding = decode_text_file(file_path)
            root = _safe_xml_root(text)
            pages = _xml_pages(root, metadata=metadata)
        except ExtractionError:
            raise
        except Exception as exc:
            raise safe_extraction_failure() from exc

        ensure_non_empty_text(pages)
        return ExtractedDocument(
            pages=pages,
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
                extra={
                    "encoding": encoding,
                    "xml_root": _safe_tag_name(root.tag),
                    "source_type": metadata.source_type,
                },
            ),
        )


class _SafeHtmlTextParser(HTMLParser):
    def __init__(self, *, max_elements: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_elements = max_elements
        self.element_count = 0
        self.title: str | None = None
        self.blocks: list[_HtmlBlock] = []
        self._suppressed_tags: list[str] = []
        self._current_text: list[str] = []
        self._current_type: str = "body"
        self._current_heading_level: int | None = None
        self._headings: list[str] = []
        self._current_parent_index: int | None = None
        self._in_title = False
        self._title_text: list[str] = []
        self._table_row_cells: list[str] | None = None
        self._table_cell_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.element_count += 1
        if self.element_count > self.max_elements:
            raise ExtractionError("text_extraction_failed", "Text extraction failed.")
        if self._suppressed_tags:
            if tag not in _VOID_HTML_TAGS:
                self._suppressed_tags.append(tag)
            return
        if tag in _BLOCKED_HTML_TAGS or _is_hidden_html_element(attrs):
            if tag not in _VOID_HTML_TAGS:
                self._suppressed_tags.append(tag)
            return
        if tag == "title":
            self._in_title = True
            self._title_text = []
            return
        if tag in _HEADING_TAGS:
            self._flush_current()
            self._current_type = tag
            self._current_heading_level = int(tag[1])
            return
        if tag in _TABLE_CELL_TAGS:
            self._table_cell_text = []
            return
        if tag == "tr":
            self._table_row_cells = []
            return
        if tag in _BLOCK_TAGS:
            self._flush_current()
            self._current_type = tag

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._suppressed_tags:
            if tag in self._suppressed_tags:
                while self._suppressed_tags:
                    suppressed_tag = self._suppressed_tags.pop()
                    if suppressed_tag == tag:
                        break
            return
        if tag == "title":
            self._in_title = False
            self.title = _safe_metadata_text(" ".join(self._title_text))
            self._title_text = []
            return
        if tag in _TABLE_CELL_TAGS and self._table_cell_text is not None:
            cell_text = _safe_text(" ".join(self._table_cell_text))
            if self._table_row_cells is not None and cell_text:
                self._table_row_cells.append(cell_text)
            self._table_cell_text = None
            return
        if tag == "tr" and self._table_row_cells is not None:
            if self._table_row_cells:
                self._append_text(" | ".join(self._table_row_cells))
                self._flush_current(element_type="table_row")
            self._table_row_cells = None
            return
        if tag in _HEADING_TAGS:
            heading = _safe_metadata_text(" ".join(self._current_text))
            if heading:
                level = self._current_heading_level or 1
                self._headings = self._headings[: level - 1]
                self._headings.append(heading)
                element_index = len(self.blocks) + 1
                self._current_parent_index = element_index
                self.blocks.append(
                    _HtmlBlock(
                        text=heading,
                        heading_path=tuple(self._headings),
                        element_type=tag,
                        element_index=element_index,
                        parent_index=element_index,
                    )
                )
            self._current_text = []
            self._current_heading_level = None
            self._current_type = "body"
            return
        if tag in _BLOCK_TAGS:
            self._flush_current()

    def handle_data(self, data: str) -> None:
        if self._suppressed_tags:
            return
        if self._in_title:
            self._title_text.append(data)
            return
        if self._table_cell_text is not None:
            self._table_cell_text.append(data)
            return
        self._append_text(data)

    def handle_comment(self, data: str) -> None:
        del data
        return

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _append_text(self, data: str) -> None:
        text = _safe_text(data)
        if text:
            self._current_text.append(text)

    def _flush_current(self, *, element_type: str | None = None) -> None:
        text = _safe_text(" ".join(self._current_text))
        if text:
            element_index = len(self.blocks) + 1
            parent_index = (
                self._current_parent_index
                if self._headings and self._current_parent_index is not None
                else element_index
            )
            self.blocks.append(
                _HtmlBlock(
                    text=text,
                    heading_path=tuple(self._headings),
                    element_type=element_type or self._current_type,
                    element_index=element_index,
                    parent_index=parent_index,
                )
            )
        self._current_text = []
        self._current_type = "body"


def _is_hidden_html_element(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        attr_name = name.lower()
        if attr_name == "hidden":
            return True
        if attr_name == "aria-hidden" and (value or "").strip().lower() == "true":
            return True
        if attr_name == "style" and value and _style_hides_element(value):
            return True
    return False


def _style_hides_element(style: str) -> bool:
    for declaration in style.split(";"):
        property_name, separator, raw_value = declaration.partition(":")
        if not separator:
            continue
        property_name = property_name.strip().lower()
        value = raw_value.split("!", 1)[0].strip().lower()
        if property_name == "display" and value == "none":
            return True
        if property_name == "visibility" and value in {"hidden", "collapse"}:
            return True
    return False


def _html_blocks_to_pages(
    blocks: list[_HtmlBlock],
    *,
    metadata: ExtractionInputMetadata,
    html_title: str | None,
) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    safe_html_title = _safe_metadata_text(html_title or metadata.file_name)
    for block in blocks:
        text = _safe_text(block.text)
        if not text:
            continue
        heading_path = " / ".join(block.heading_path)
        source_url = _safe_source_url_text(metadata.source_url)
        page_metadata = {
            "parent_child_schema_version": WEB_INGEST_SCHEMA_VERSION,
            "structure_type": "html_section",
            "chunk_level": "child",
            "parent_chunk_key": _parent_key("html", block.heading_path, block.parent_index),
            "parent_title": heading_path or _safe_metadata_text(metadata.file_name),
            "html_title": safe_html_title,
            "heading_path": heading_path,
            "element_type": block.element_type,
            "element_index": block.element_index,
            "source_type": metadata.source_type or "file",
        }
        if source_url:
            page_metadata["source_url"] = source_url
        pages.append(
            ExtractedPage(
                text=text,
                page_number=None,
                section_title=heading_path or block.element_type,
                metadata=page_metadata,
            )
        )
    return pages


def _safe_xml_root(text: str) -> ElementTree.Element:
    settings = get_settings()
    try:
        validate_xml_text_safety(text, max_elements=settings.ingest_xml_max_elements)
    except UnsafeFileRejected as exc:
        raise ExtractionError("text_extraction_failed", "Text extraction failed.") from exc
    try:
        root = ElementTree.fromstring(text.lstrip("\ufeff"))
    except ElementTree.ParseError as exc:
        raise ExtractionError("text_extraction_failed", "Text extraction failed.") from exc
    if _xml_tree_contains_svg(root):
        raise ExtractionError("text_extraction_failed", "Text extraction failed.")
    return root


def _xml_pages(
    root: ElementTree.Element, *, metadata: ExtractionInputMetadata
) -> list[ExtractedPage]:
    settings = get_settings()
    pages: list[ExtractedPage] = []
    for index, (path, element, element_text) in enumerate(
        _walk_xml(root, max_elements=settings.ingest_xml_max_elements),
        start=1,
    ):
        text = _safe_text(element_text)
        if not text:
            continue
        safe_path = " / ".join(path)
        source_url = _safe_source_url_text(metadata.source_url)
        page_metadata = {
            "parent_child_schema_version": WEB_INGEST_SCHEMA_VERSION,
            "structure_type": "xml_element",
            "chunk_level": "child",
            "parent_chunk_key": _xml_parent_key(path, index),
            "parent_title": safe_path,
            "xml_root": _safe_tag_name(root.tag),
            "xml_path": safe_path,
            "element_name": path[-1] if path else _safe_tag_name(element.tag),
            "element_index": index,
            "source_type": metadata.source_type or "file",
        }
        if source_url:
            page_metadata["source_url"] = source_url
        pages.append(
            ExtractedPage(
                text=text,
                page_number=None,
                section_title=safe_path,
                metadata=page_metadata,
            )
        )
    return pages


def _walk_xml(
    root: ElementTree.Element,
    *,
    max_elements: int,
) -> list[tuple[list[str], ElementTree.Element, str]]:
    rows: list[tuple[list[str], ElementTree.Element, str]] = []
    visited_count = 0
    stack: list[tuple[ElementTree.Element, list[str]]] = [(root, [])]

    while stack:
        element, path = stack.pop()
        visited_count += 1
        if visited_count > max_elements:
            raise ExtractionError("text_extraction_failed", "Text extraction failed.")
        current_name = _safe_tag_name(element.tag)
        current_path = [*path, current_name]
        children = list(element)
        own_text = _xml_direct_text(element)
        if not children:
            rows.append((current_path, element, own_text))
            continue
        if own_text:
            rows.append((current_path, element, own_text))
        for child in reversed(children):
            stack.append((child, current_path))
    return rows


def _xml_direct_text(element: ElementTree.Element) -> str:
    parts = [element.text or ""]
    parts.extend(child.tail or "" for child in element)
    return _safe_text(" ".join(parts))


def _parent_key(prefix: str, heading_path: tuple[str, ...], element_index: int) -> str:
    if heading_path:
        digest = sha256("\0".join([str(element_index), *heading_path]).encode("utf-8")).hexdigest()[
            :12
        ]
        return f"{prefix}:section:{digest}"
    return f"{prefix}:element:{element_index}"


def _xml_parent_key(path: list[str], element_index: int) -> str:
    if path:
        digest = sha256("\0".join([str(element_index), *path]).encode("utf-8")).hexdigest()[:12]
        return f"xml:path:{digest}"
    return f"xml:element:{element_index}"


def _safe_tag_name(value: object) -> str:
    text = str(value)
    if "}" in text:
        text = text.rsplit("}", 1)[1]
    return _safe_metadata_text(text) or "element"


def _xml_tree_contains_svg(root: ElementTree.Element) -> bool:
    return any(_is_svg_xml_tag(element.tag) for element in root.iter())


def _is_svg_xml_tag(tag: object) -> bool:
    text = str(tag)
    if text.startswith("{"):
        namespace, _, local_name = text[1:].partition("}")
        return local_name.lower() == "svg" or namespace.lower() == _SVG_NAMESPACE
    if ":" in text:
        return text.rsplit(":", 1)[1].lower() == "svg"
    return text.lower() == "svg"


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("\x00", " ").split())


def _safe_metadata_text(value: object) -> str:
    text = _safe_text(value)[:120]
    if not text:
        return ""
    if SECRET_ASSIGNMENT_RE.search(text) or URL_RE.search(text) or EMAIL_RE.search(text):
        return "redacted"
    return text


def _safe_source_url_text(value: object) -> str:
    text = _safe_text(value)[:200]
    if not text:
        return ""
    if SECRET_ASSIGNMENT_RE.search(text) or EMAIL_RE.search(text):
        return "redacted"
    return text
