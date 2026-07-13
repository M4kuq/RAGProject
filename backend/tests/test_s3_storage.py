from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import UnsafeFileRejected
from app.storage.file_storage import (
    DocumentStorageError,
    LocalFileStorage,
    S3DocumentStorage,
    create_document_storage,
)


class _AwsError(RuntimeError):
    def __init__(self, code: str, message: str = "sensitive") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class _Body(io.BytesIO):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


class _S3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.bodies: list[_Body] = []
        self.missing_error_code = "NoSuchKey"

    def put_object(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(("put", kwargs))
        body = kwargs["Body"]
        assert isinstance(body, (bytes, bytearray))
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(body)
        return {"VersionId": "version-1"}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("head", kwargs))
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise _AwsError(self.missing_error_code)
        return {"ContentLength": len(self.objects[key])}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get", kwargs))
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise _AwsError(self.missing_error_code)
        value = self.objects[key]
        body = _Body(value)
        self.bodies.append(body)
        return {"ContentLength": len(value), "Body": body}

    def delete_object(self, **kwargs: object) -> None:
        self.calls.append(("delete", kwargs))
        self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    values: dict[str, Any] = {
        "storage_root": tmp_path,
        "storage_backend": "s3",
        "documents_bucket_name": "documents-test",
        "documents_key_prefix": "source",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_s3_storage_save_exists_materialize_delete(tmp_path: Path) -> None:
    client = _S3()
    storage = S3DocumentStorage(settings=_settings(tmp_path), client=client)
    key = storage.build_storage_key(file_name="guide.txt")

    version_id = storage.save_bytes(storage_key=key, content=b"hello")

    assert storage.exists(storage_key=key)
    assert ("documents-test", f"source/{key}") in client.objects
    with storage.materialize(storage_key=key) as path:
        assert path.read_bytes() == b"hello"
        temporary_path = path
    assert not temporary_path.exists()
    assert client.bodies[0].was_closed

    storage.delete(storage_key=key, version_id=version_id)
    assert not storage.exists(storage_key=key)
    assert client.calls[-1] == (
        "delete",
        {
            "Bucket": "documents-test",
            "Key": f"source/{key}",
            "VersionId": "version-1",
        },
    )


@pytest.mark.parametrize(
    "storage_key",
    ["", "/absolute", "../escape", "documents//file", "documents/./file", "a\\b"],
)
def test_s3_storage_rejects_unsafe_keys(tmp_path: Path, storage_key: str) -> None:
    storage = S3DocumentStorage(settings=_settings(tmp_path), client=_S3())

    with pytest.raises(UnsafeFileRejected):
        storage.save_bytes(storage_key=storage_key, content=b"data")


def test_s3_storage_enforces_materialized_size_limit(tmp_path: Path) -> None:
    client = _S3()
    settings = _settings(tmp_path, upload_max_bytes=3)
    storage = S3DocumentStorage(settings=settings, client=client)
    client.objects[("documents-test", "source/documents/aa/file.txt")] = b"toolarge"

    with pytest.raises(DocumentStorageError) as exc_info:
        with storage.materialize(storage_key="documents/aa/file.txt"):
            pass

    assert exc_info.value.error_category == "invalid_response"
    assert client.bodies[0].was_closed


@pytest.mark.parametrize("error_code", ["NoSuchKey", "404"])
def test_s3_storage_missing_object_is_safe(
    tmp_path: Path,
    error_code: str,
) -> None:
    client = _S3()
    client.missing_error_code = error_code
    storage = S3DocumentStorage(settings=_settings(tmp_path), client=client)

    assert not storage.exists(storage_key="documents/aa/missing.txt")
    with pytest.raises(DocumentStorageError) as exc_info:
        with storage.materialize(storage_key="documents/aa/missing.txt"):
            pass

    assert exc_info.value.error_code == "storage_file_missing"
    assert "sensitive" not in str(exc_info.value)


def test_document_storage_factory_preserves_local_default(tmp_path: Path) -> None:
    local = create_document_storage(
        Settings(_env_file=None, storage_backend="local", storage_root=tmp_path)
    )
    assert isinstance(local, LocalFileStorage)

    s3 = create_document_storage(_settings(tmp_path), s3_client=_S3())
    assert isinstance(s3, S3DocumentStorage)


def test_default_settings_allow_empty_document_prefix() -> None:
    settings = Settings(_env_file=None)

    assert settings.documents_key_prefix == ""
    assert settings.storage_backend == "local"


def test_s3_storage_requires_bucket_and_safe_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DOCUMENTS_BUCKET_NAME"):
        Settings(_env_file=None, storage_backend="s3")
    with pytest.raises(ValueError, match="safe relative prefix"):
        _settings(tmp_path, documents_key_prefix="../escape")
