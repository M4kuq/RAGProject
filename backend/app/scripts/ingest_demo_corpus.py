from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MANIFEST = "docs/demo/corpus_manifest.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ORIGIN = "http://localhost:5173"
DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "password"
DEFAULT_WAIT_TIMEOUT_SECONDS = 300
DEFAULT_POLL_SECONDS = 2.0


class DemoCorpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class CorpusEntry:
    title: str
    source_path: str
    absolute_path: Path
    content_hash: str
    content_type: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class IngestItemResult:
    title: str
    source_path: str
    action: str
    content_hash: str
    logical_document_id: int | None = None
    document_version_id: int | None = None
    job_id: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class IngestSummary:
    manifest_path: str
    base_url: str
    dry_run: bool
    item_count: int
    created_count: int
    version_added_count: int
    approved_count: int
    skipped_count: int
    failed_count: int
    items: tuple[IngestItemResult, ...]


class DemoCorpusApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        admin_email: str,
        admin_password: str,
        origin: str,
        timeout_seconds: float,
        basic_auth_header: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.origin = origin
        self.timeout_seconds = timeout_seconds
        self.csrf_token: str | None = None
        if basic_auth_header is not None and not basic_auth_header.startswith("Basic "):
            raise DemoCorpusError("basic_auth_header_must_use_basic_scheme")
        default_headers = (
            {"Authorization": basic_auth_header} if basic_auth_header is not None else None
        )
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            follow_redirects=False,
            headers=default_headers,
        )

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        pre_auth_csrf = self._csrf()
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": self.admin_email, "password": self.admin_password},
            headers=self._unsafe_headers(pre_auth_csrf),
        )
        data = self._data(response, path="/api/v1/auth/login")
        csrf_token = data.get("csrf_token")
        if not isinstance(csrf_token, str) or not csrf_token:
            raise DemoCorpusError("login_missing_csrf_token")
        self.csrf_token = csrf_token

    def list_documents(self, *, query: str) -> list[dict[str, Any]]:
        response = self.client.get(
            "/api/v1/documents",
            params={"q": query, "page_size": 100},
        )
        data = self._data(response, path="/api/v1/documents")
        if not isinstance(data, list):
            raise DemoCorpusError("documents_response_invalid")
        return [item for item in data if isinstance(item, dict)]

    def get_document_detail(self, logical_document_id: int) -> dict[str, Any]:
        response = self.client.get(f"/api/v1/documents/{logical_document_id}")
        data = self._data(response, path="/api/v1/documents/{id}")
        if not isinstance(data, dict):
            raise DemoCorpusError("document_detail_response_invalid")
        return data

    def upload_document(self, entry: CorpusEntry) -> dict[str, Any]:
        content = entry.absolute_path.read_bytes()
        response = self.client.post(
            "/api/v1/documents",
            data={"title": entry.title},
            files={
                "file": (
                    Path(entry.source_path).name,
                    content,
                    entry.content_type,
                )
            },
            headers=self._unsafe_headers(self._session_csrf()),
        )
        data = self._data(response, path="/api/v1/documents")
        if not isinstance(data, dict):
            raise DemoCorpusError("document_upload_response_invalid")
        return data

    def upload_document_version(
        self, logical_document_id: int, entry: CorpusEntry
    ) -> dict[str, Any]:
        content = entry.absolute_path.read_bytes()
        response = self.client.post(
            f"/api/v1/documents/{logical_document_id}/versions",
            files={
                "file": (
                    Path(entry.source_path).name,
                    content,
                    entry.content_type,
                )
            },
            headers=self._unsafe_headers(self._session_csrf()),
        )
        data = self._data(response, path="/api/v1/documents/{id}/versions")
        if not isinstance(data, dict):
            raise DemoCorpusError("document_version_response_invalid")
        return data

    def approve_document_version(self, logical_document_id: int, document_version_id: int) -> None:
        response = self.client.post(
            f"/api/v1/documents/{logical_document_id}/versions/{document_version_id}/approve",
            headers=self._unsafe_headers(self._session_csrf()),
        )
        self._data(response, path="/api/v1/documents/{id}/versions/{version_id}/approve")

    def _csrf(self) -> str:
        response = self.client.get("/api/v1/auth/csrf", headers={"Origin": self.origin})
        data = self._data(response, path="/api/v1/auth/csrf")
        token = data.get("csrf_token")
        if not isinstance(token, str) or not token:
            raise DemoCorpusError("csrf_response_invalid")
        return token

    def _session_csrf(self) -> str:
        if self.csrf_token is None:
            raise DemoCorpusError("not_authenticated")
        return self.csrf_token

    def _unsafe_headers(self, csrf_token: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf_token, "Origin": self.origin}

    @staticmethod
    def _data(response: httpx.Response, *, path: str) -> Any:
        if response.status_code >= 400:
            raise DemoCorpusError(
                f"http_{response.status_code}:{path}:{_safe_error_code(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DemoCorpusError(f"invalid_json:{path}") from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise DemoCorpusError(f"invalid_api_payload:{path}")
        return payload["data"]


def load_manifest(manifest_path: Path, *, repo_root: Path) -> list[CorpusEntry]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DemoCorpusError(f"manifest_invalid_json:{manifest_path}") from exc
    if not isinstance(payload, dict):
        raise DemoCorpusError("manifest_must_be_object")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DemoCorpusError("manifest_entries_required")
    allowed_extensions = _string_set(payload.get("allowed_extensions"))
    if not allowed_extensions:
        allowed_extensions = {".md", ".markdown", ".txt"}

    loaded: list[CorpusEntry] = []
    seen_titles: set[str] = set()
    seen_paths: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DemoCorpusError(f"manifest_entry_invalid:{index}")
        title = _required_string(item, "title", index=index)
        source_path = _required_string(item, "source_path", index=index)
        if title in seen_titles:
            raise DemoCorpusError(f"manifest_duplicate_title:{title}")
        if source_path in seen_paths:
            raise DemoCorpusError(f"manifest_duplicate_source_path:{source_path}")
        absolute_path = (repo_root / source_path).resolve()
        _ensure_repo_file(absolute_path, repo_root=repo_root, source_path=source_path)
        if absolute_path.suffix.lower() not in allowed_extensions:
            raise DemoCorpusError(f"manifest_extension_not_allowed:{source_path}")
        metadata = item.get("metadata")
        loaded.append(
            CorpusEntry(
                title=title,
                source_path=source_path,
                absolute_path=absolute_path,
                content_hash=_sha256_file(absolute_path),
                content_type=_content_type_for_path(absolute_path),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
        seen_titles.add(title)
        seen_paths.add(source_path)
    return loaded


def ingest_demo_corpus(
    entries: list[CorpusEntry],
    *,
    client: DemoCorpusApiClient,
    manifest_path: Path,
    wait: bool,
    approve: bool,
    wait_timeout_seconds: int,
    poll_seconds: float,
    dry_run: bool = False,
) -> IngestSummary:
    results: list[IngestItemResult] = []
    if dry_run:
        results = [
            IngestItemResult(
                title=entry.title,
                source_path=entry.source_path,
                action="would_upload_or_skip",
                content_hash=entry.content_hash,
            )
            for entry in entries
        ]
        return _summary(
            manifest_path=manifest_path,
            base_url=client.base_url,
            dry_run=True,
            results=results,
        )

    client.login()
    for entry in entries:
        results.append(
            _ingest_entry(
                entry,
                client=client,
                wait=wait,
                approve=approve,
                wait_timeout_seconds=wait_timeout_seconds,
                poll_seconds=poll_seconds,
            )
        )
    return _summary(
        manifest_path=manifest_path,
        base_url=client.base_url,
        dry_run=False,
        results=results,
    )


def _ingest_entry(
    entry: CorpusEntry,
    *,
    client: DemoCorpusApiClient,
    wait: bool,
    approve: bool,
    wait_timeout_seconds: int,
    poll_seconds: float,
) -> IngestItemResult:
    existing = _find_existing_document(client, title=entry.title)
    if existing is None:
        uploaded = client.upload_document(entry)
        logical_document_id = _positive_int(uploaded.get("logical_document_id"))
        document_version_id = _positive_int(uploaded.get("document_version_id"))
        job_id = _positive_int(uploaded.get("job_id"))
        final_action = _finalize_version(
            client,
            entry=entry,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            action="created",
            wait=wait,
            approve=approve,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        return IngestItemResult(
            title=entry.title,
            source_path=entry.source_path,
            action=final_action,
            content_hash=entry.content_hash,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            job_id=job_id,
        )

    logical_document_id = _positive_int(existing.get("logical_document_id"))
    if logical_document_id is None:
        return _failed_entry(entry, "existing_document_missing_id")
    detail = client.get_document_detail(logical_document_id)
    matched_version = _find_version_by_hash(detail, entry.content_hash)
    if matched_version is not None:
        document_version_id = _positive_int(matched_version.get("document_version_id"))
        if document_version_id is None:
            return _failed_entry(entry, "matched_version_missing_id")
        action = _finalize_version(
            client,
            entry=entry,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            action="skipped_existing_hash",
            wait=wait,
            approve=approve and not bool(matched_version.get("is_active")),
            wait_timeout_seconds=wait_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        return IngestItemResult(
            title=entry.title,
            source_path=entry.source_path,
            action=action,
            content_hash=entry.content_hash,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
        )

    version_response = client.upload_document_version(logical_document_id, entry)
    document_version_id = _positive_int(version_response.get("document_version_id"))
    if document_version_id is None:
        document_version_id = _positive_int(version_response.get("matched_document_version_id"))
    if document_version_id is None:
        return _failed_entry(entry, "version_response_missing_id")
    action = _finalize_version(
        client,
        entry=entry,
        logical_document_id=logical_document_id,
        document_version_id=document_version_id,
        action="version_added",
        wait=wait,
        approve=approve,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_seconds=poll_seconds,
    )
    return IngestItemResult(
        title=entry.title,
        source_path=entry.source_path,
        action=action,
        content_hash=entry.content_hash,
        logical_document_id=logical_document_id,
        document_version_id=document_version_id,
        job_id=_positive_int(version_response.get("job_id")),
    )


def _finalize_version(
    client: DemoCorpusApiClient,
    *,
    entry: CorpusEntry,
    logical_document_id: int | None,
    document_version_id: int | None,
    action: str,
    wait: bool,
    approve: bool,
    wait_timeout_seconds: int,
    poll_seconds: float,
) -> str:
    if logical_document_id is None or document_version_id is None:
        return "failed_missing_document_ids"
    version = (
        _wait_for_version_status(
            client,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            timeout_seconds=wait_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        if wait
        else _find_version_by_id(
            client.get_document_detail(logical_document_id), document_version_id
        )
    )
    if not isinstance(version, dict):
        return action if not approve else "queued_without_approval"
    status = str(version.get("status"))
    if status == "failed":
        return "failed_ingest"
    if status != "ready":
        return action if not approve else "queued_without_approval"
    if approve and not bool(version.get("is_active")):
        client.approve_document_version(logical_document_id, document_version_id)
        return "approved" if action.startswith("skipped_") else f"{action}_approved"
    if action == "skipped_existing_hash" and bool(version.get("is_active")):
        return "skipped_existing_active"
    return action


def _wait_for_version_status(
    client: DemoCorpusApiClient,
    *,
    logical_document_id: int,
    document_version_id: int,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        detail = client.get_document_detail(logical_document_id)
        version = _find_version_by_id(detail, document_version_id)
        if version is not None and version.get("status") in {"ready", "failed", "archived"}:
            return version
        time.sleep(poll_seconds)
    raise DemoCorpusError(f"version_wait_timeout:{document_version_id}")


def _find_existing_document(
    client: DemoCorpusApiClient,
    *,
    title: str,
) -> dict[str, Any] | None:
    for item in client.list_documents(query=title):
        if item.get("title") == title or item.get("document_name") == title:
            return item
    return None


def _find_version_by_hash(detail: dict[str, Any], content_hash: str) -> dict[str, Any] | None:
    for version in _version_items(detail):
        if version.get("content_hash") == content_hash:
            return version
    return None


def _find_version_by_id(
    detail: dict[str, Any],
    document_version_id: int,
) -> dict[str, Any] | None:
    for version in _version_items(detail):
        if version.get("document_version_id") == document_version_id:
            return version
    return None


def _version_items(detail: dict[str, Any]) -> list[dict[str, Any]]:
    versions = detail.get("versions")
    if not isinstance(versions, list):
        return []
    return [version for version in versions if isinstance(version, dict)]


def _summary(
    *,
    manifest_path: Path,
    base_url: str,
    dry_run: bool,
    results: list[IngestItemResult],
) -> IngestSummary:
    return IngestSummary(
        manifest_path=str(manifest_path),
        base_url=base_url,
        dry_run=dry_run,
        item_count=len(results),
        created_count=sum(1 for item in results if item.action.startswith("created")),
        version_added_count=sum(1 for item in results if item.action.startswith("version_added")),
        approved_count=sum(1 for item in results if "approved" in item.action),
        skipped_count=sum(1 for item in results if item.action.startswith("skipped")),
        failed_count=sum(1 for item in results if item.action.startswith("failed")),
        items=tuple(results),
    )


def _failed_entry(entry: CorpusEntry, reason_code: str) -> IngestItemResult:
    return IngestItemResult(
        title=entry.title,
        source_path=entry.source_path,
        action="failed",
        content_hash=entry.content_hash,
        reason_code=reason_code,
    )


def _safe_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "unknown_error"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code[:120]
    return "unknown_error"


def _required_string(item: dict[str, object], key: str, *, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DemoCorpusError(f"manifest_entry_{key}_required:{index}")
    return value.strip()


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip().lower() for item in value if isinstance(item, str) and item.strip()}


def _ensure_repo_file(path: Path, *, repo_root: Path, source_path: str) -> None:
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise DemoCorpusError(f"manifest_path_outside_repo:{source_path}") from exc
    if not path.is_file():
        raise DemoCorpusError(f"manifest_path_missing:{source_path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_type_for_path(path: Path) -> str:
    if path.suffix.lower() in {".md", ".markdown"}:
        return "text/markdown"
    return mimetypes.guess_type(path.name)[0] or "text/plain"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def _summary_json(summary: IngestSummary) -> str:
    return json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest the reproducible local demo corpus through the existing API."
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--base-url", default=os.environ.get("RAG_DEMO_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--admin-email",
        default=os.environ.get("RAG_DEMO_ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL),
    )
    parser.add_argument(
        "--admin-password",
        default=os.environ.get("RAG_DEMO_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
    )
    parser.add_argument("--origin", default=os.environ.get("RAG_DEMO_ORIGIN", DEFAULT_ORIGIN))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--wait-timeout-seconds", type=int, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--skip-approve", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    manifest_path = (repo_root / args.manifest).resolve()
    try:
        entries = load_manifest(manifest_path, repo_root=repo_root)
        client = DemoCorpusApiClient(
            base_url=args.base_url,
            admin_email=args.admin_email,
            admin_password=args.admin_password,
            origin=args.origin,
            timeout_seconds=args.timeout_seconds,
            basic_auth_header=os.environ.get("RAG_DEMO_BASIC_AUTH_HEADER"),
        )
        try:
            summary = ingest_demo_corpus(
                entries,
                client=client,
                manifest_path=manifest_path,
                wait=not args.no_wait,
                approve=not args.skip_approve,
                wait_timeout_seconds=args.wait_timeout_seconds,
                poll_seconds=args.poll_seconds,
                dry_run=args.dry_run,
            )
        finally:
            client.close()
    except DemoCorpusError as exc:
        print(f"ingest_demo_corpus_error code={exc}", flush=True)
        return 1
    print(_summary_json(summary), flush=True)
    return 0 if summary.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
