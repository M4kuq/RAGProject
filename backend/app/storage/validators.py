from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PureWindowsPath

from app.core.errors import (
    PayloadTooLarge,
    UnsafeFileRejected,
    UnsupportedMediaType,
    ValidationFailed,
)

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_DANGEROUS_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
    ".vbs",
}
_MIME_TYPES_BY_EXTENSION = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
}
_DOCX_MAX_ZIP_ENTRIES = 200
_DOCX_MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
_DOCX_MAX_DOCUMENT_XML_BYTES = 5 * 1024 * 1024
_DOCX_MAX_COMPRESSION_RATIO = 100


@dataclass(frozen=True)
class ValidatedUpload:
    file_name: str
    extension: str
    mime_type: str
    file_size_bytes: int


def validate_upload(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
    max_bytes: int,
    allowed_extensions: list[str],
) -> ValidatedUpload:
    if filename is None or not filename.strip():
        raise ValidationFailed(details=[{"field": "file", "reason": "file name is required."}])
    if not content:
        raise ValidationFailed(details=[{"field": "file", "reason": "file must not be empty."}])
    if len(content) > max_bytes:
        raise PayloadTooLarge()

    safe_name = sanitize_file_name(filename)
    extension = _extension(safe_name)
    allowed = {item.lower() for item in allowed_extensions}
    if extension not in allowed:
        raise UnsupportedMediaType()
    _reject_dangerous_suffixes(safe_name)

    normalized_mime_type = (content_type or "application/octet-stream").split(";", 1)[0].lower()
    allowed_mimes = _MIME_TYPES_BY_EXTENSION.get(extension, {"application/octet-stream"})
    if normalized_mime_type not in allowed_mimes:
        raise UnsupportedMediaType()

    _validate_magic_bytes(extension, content)
    return ValidatedUpload(
        file_name=safe_name,
        extension=extension,
        mime_type=normalized_mime_type,
        file_size_bytes=len(content),
    )


def sanitize_file_name(filename: str) -> str:
    if "\x00" in filename or _CONTROL_CHAR_PATTERN.search(filename):
        raise UnsafeFileRejected()
    if "/" in filename or "\\" in filename:
        raise UnsafeFileRejected()
    if filename in {".", ".."} or ".." in PureWindowsPath(filename).parts:
        raise UnsafeFileRejected()

    safe_name = _SAFE_FILENAME_PATTERN.sub("_", filename.strip()).strip(" .")
    if not safe_name:
        raise UnsafeFileRejected()
    if len(safe_name) > 255:
        stem, dot, suffix = safe_name.rpartition(".")
        if dot:
            safe_name = f"{stem[: 255 - len(suffix) - 1]}.{suffix}"
        else:
            safe_name = safe_name[:255]
    return safe_name


def safe_title_from_file_name(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].strip()
    return stem or filename


def extension_from_file_name(filename: str) -> str:
    return _extension(sanitize_file_name(filename))


def allowed_mime_types_for_extension(extension: str) -> set[str]:
    return set(_MIME_TYPES_BY_EXTENSION.get(extension.lower(), {"application/octet-stream"}))


def _extension(filename: str) -> str:
    if "." not in filename:
        raise UnsupportedMediaType()
    return f".{filename.rsplit('.', 1)[1].lower()}"


def _reject_dangerous_suffixes(filename: str) -> None:
    suffixes = [f".{part.lower()}" for part in filename.split(".")[1:]]
    if any(suffix in _DANGEROUS_EXTENSIONS for suffix in suffixes):
        raise UnsafeFileRejected()


def _validate_magic_bytes(extension: str, content: bytes) -> None:
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise UnsafeFileRejected()
        return
    if extension == ".docx":
        if not content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            raise UnsafeFileRejected()
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                _validate_docx_archive(archive)
        except zipfile.BadZipFile as exc:
            raise UnsafeFileRejected() from exc
        return
    if extension in {".txt", ".md", ".markdown", ".csv"}:
        if b"\x00" in content:
            raise UnsafeFileRejected()
        for encoding in ("utf-8", "utf-8-sig", "cp932"):
            try:
                content.decode(encoding)
                return
            except UnicodeDecodeError:
                continue
        raise UnsafeFileRejected()


def _validate_docx_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if not infos or len(infos) > _DOCX_MAX_ZIP_ENTRIES:
        raise UnsafeFileRejected()

    total_uncompressed = 0
    document_xml_size: int | None = None
    for info in infos:
        name = info.filename
        if not name or name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise UnsafeFileRejected()
        if info.flag_bits & 0x1:
            raise UnsafeFileRejected()
        total_uncompressed += info.file_size
        if total_uncompressed > _DOCX_MAX_UNCOMPRESSED_BYTES:
            raise UnsafeFileRejected()
        if info.compress_size == 0 and info.file_size > 0:
            raise UnsafeFileRejected()
        if (
            info.compress_size > 0
            and info.file_size / info.compress_size > _DOCX_MAX_COMPRESSION_RATIO
        ):
            raise UnsafeFileRejected()
        if name == "word/document.xml":
            document_xml_size = info.file_size

    if document_xml_size is None or document_xml_size > _DOCX_MAX_DOCUMENT_XML_BYTES:
        raise UnsafeFileRejected()
