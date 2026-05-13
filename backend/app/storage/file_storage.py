from __future__ import annotations

import uuid
from pathlib import Path, PurePosixPath

from app.core.config import get_settings
from app.core.errors import UnsafeFileRejected


class LocalFileStorage:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or get_settings().storage_root

    def build_storage_key(self, *, file_name: str) -> str:
        token = uuid.uuid4().hex
        return str(PurePosixPath("documents") / token[:2] / f"{token}_{file_name}")

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

    def _safe_path(self, storage_key: str) -> Path:
        key_path = PurePosixPath(storage_key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise UnsafeFileRejected()
        base = self.base_dir.resolve()
        target = (base / Path(*key_path.parts)).resolve()
        if base != target and base not in target.parents:
            raise UnsafeFileRejected()
        return target
