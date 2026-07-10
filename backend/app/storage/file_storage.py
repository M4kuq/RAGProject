from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.aws.client import aws_error_category, create_aws_client
from app.core.config import Settings, get_settings
from app.core.errors import UnsafeFileRejected

logger = logging.getLogger(__name__)


class DocumentStorageError(RuntimeError):
    def __init__(
        self,
        error_code: str = "storage_failed",
        message: str = "Document storage operation failed.",
        *,
        error_category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_category = error_category


class DocumentStorage(Protocol):
    def build_storage_key(self, *, file_name: str) -> str: ...

    def save_bytes(self, *, storage_key: str, content: bytes) -> None: ...

    def exists(self, *, storage_key: str) -> bool: ...

    def delete(self, *, storage_key: str) -> None: ...

    def materialize(self, *, storage_key: str) -> AbstractContextManager[Path]: ...


class LocalFileStorage:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or get_settings().storage_root

    def build_storage_key(self, *, file_name: str) -> str:
        return _build_storage_key(file_name)

    def save_bytes(self, *, storage_key: str, content: bytes) -> None:
        target = self._safe_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def resolve_path(self, *, storage_key: str) -> Path:
        return self._safe_path(storage_key)

    def exists(self, *, storage_key: str) -> bool:
        return self._safe_path(storage_key).is_file()

    def delete(self, *, storage_key: str) -> None:
        target = self._safe_path(storage_key)
        if target.is_file():
            target.unlink()

    @contextmanager
    def materialize(self, *, storage_key: str) -> Iterator[Path]:
        target = self._safe_path(storage_key)
        if not target.is_file():
            raise DocumentStorageError(
                "storage_file_missing",
                error_category="not_found",
            )
        yield target

    def _safe_path(self, storage_key: str) -> Path:
        key_path = PurePosixPath(_validate_storage_key(storage_key))
        base = self.base_dir.resolve()
        target = (base / Path(*key_path.parts)).resolve()
        if base != target and base not in target.parents:
            raise UnsafeFileRejected()
        return target


class S3DocumentStorage:
    def __init__(
        self,
        *,
        settings: Settings,
        client: Any | None = None,
    ) -> None:
        if not settings.documents_bucket_name:
            raise ValueError("DOCUMENTS_BUCKET_NAME is required for S3 storage")
        self.bucket_name = settings.documents_bucket_name
        self.key_prefix = settings.documents_key_prefix
        self.max_bytes = settings.upload_max_bytes
        self.client = client or create_aws_client("s3", settings)

    def build_storage_key(self, *, file_name: str) -> str:
        return _build_storage_key(file_name)

    def save_bytes(self, *, storage_key: str, content: bytes) -> None:
        if len(content) > self.max_bytes:
            raise DocumentStorageError(error_category="invalid_request")
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=self._object_key(storage_key),
                Body=content,
                ContentLength=len(content),
            )
        except Exception as exc:
            raise self._error(exc, operation="put") from exc

    def exists(self, *, storage_key: str) -> bool:
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=self._object_key(storage_key),
            )
        except Exception as exc:
            if aws_error_category(exc) == "not_found":
                return False
            raise self._error(exc, operation="head") from exc
        return True

    def delete(self, *, storage_key: str) -> None:
        try:
            self.client.delete_object(
                Bucket=self.bucket_name,
                Key=self._object_key(storage_key),
            )
        except Exception as exc:
            raise self._error(exc, operation="delete") from exc

    @contextmanager
    def materialize(self, *, storage_key: str) -> Iterator[Path]:
        object_key = self._object_key(storage_key)
        try:
            response = self.client.get_object(
                Bucket=self.bucket_name,
                Key=object_key,
            )
            body = response.get("Body") if isinstance(response, dict) else None
            content_length = response.get("ContentLength") if isinstance(response, dict) else None
            if isinstance(content_length, int) and content_length > self.max_bytes:
                raise DocumentStorageError(error_category="invalid_response")
            if body is None or not hasattr(body, "read"):
                raise DocumentStorageError(error_category="invalid_response")
            content: object = body.read(self.max_bytes + 1)
            if not isinstance(content, bytes) or len(content) > self.max_bytes:
                raise DocumentStorageError(error_category="invalid_response")
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._error(exc, operation="get") from exc

        suffix = PurePosixPath(storage_key).suffix
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="ragproject-",
                suffix=suffix,
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            yield temporary_path
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _object_key(self, storage_key: str) -> str:
        validated = _validate_storage_key(storage_key)
        if not self.key_prefix:
            return validated
        return f"{self.key_prefix}/{validated}"

    @staticmethod
    def _error(exc: Exception, *, operation: str) -> DocumentStorageError:
        category = aws_error_category(exc)
        logger.warning(
            "s3 document storage failed",
            extra={"operation": operation, "error_category": category},
        )
        error_code = "storage_file_missing" if category == "not_found" else "storage_failed"
        return DocumentStorageError(error_code, error_category=category)


def create_document_storage(
    settings: Settings | None = None,
    *,
    s3_client: Any | None = None,
) -> DocumentStorage:
    resolved = settings or get_settings()
    if resolved.storage_backend == "local":
        return LocalFileStorage(base_dir=resolved.storage_root)
    if resolved.storage_backend == "s3":
        return S3DocumentStorage(settings=resolved, client=s3_client)
    raise ValueError("Unsupported document storage backend")


def _build_storage_key(file_name: str) -> str:
    if not file_name or "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise UnsafeFileRejected()
    token = uuid.uuid4().hex
    return str(PurePosixPath("documents") / token[:2] / f"{token}_{file_name}")


def _validate_storage_key(storage_key: str) -> str:
    if (
        not storage_key
        or storage_key.startswith("/")
        or "\\" in storage_key
        or "\x00" in storage_key
    ):
        raise UnsafeFileRejected()
    parts = storage_key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafeFileRejected()
    return storage_key
