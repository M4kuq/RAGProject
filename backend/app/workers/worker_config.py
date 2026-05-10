from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from app.core.config import Settings, get_settings

SUPPORTED_JOB_TYPES = frozenset(
    {
        "document_ingest",
        "qdrant_mirror_update",
        "message_edit_regeneration",
        "evaluation_run",
        "temporary_chat_cleanup",
    }
)


class WorkerConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    poll_interval_seconds: float
    batch_size: int
    lease_duration: timedelta
    lease_renew_interval_seconds: int
    shutdown_grace_seconds: int
    enabled_job_types: frozenset[str] | None
    worker_instance_id: str


def load_worker_config(
    settings: Settings | None = None,
    *,
    worker_instance_id: str | None = None,
) -> WorkerConfig:
    effective_settings = settings or get_settings()
    enabled_job_types = parse_enabled_job_types(effective_settings.worker_enabled_job_types)
    return WorkerConfig(
        poll_interval_seconds=effective_settings.worker_poll_interval_ms / 1000,
        batch_size=effective_settings.worker_batch_size,
        lease_duration=timedelta(seconds=effective_settings.worker_lease_seconds),
        lease_renew_interval_seconds=effective_settings.worker_lease_renew_interval_seconds,
        shutdown_grace_seconds=effective_settings.worker_shutdown_grace_seconds,
        enabled_job_types=enabled_job_types,
        worker_instance_id=worker_instance_id
        or build_worker_instance_id(instance_name=effective_settings.worker_instance_name),
    )


def parse_enabled_job_types(value: Iterable[str] | str | None) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = [str(item).strip() for item in value if str(item).strip()]

    lowered_items = [item.lower() for item in items]
    if not items:
        return None
    if "all" in lowered_items:
        if len(items) > 1:
            raise WorkerConfigError(
                "WORKER_ENABLED_JOB_TYPES cannot combine 'all' with explicit job types."
            )
        return None
    if "none" in lowered_items:
        if len(items) > 1:
            raise WorkerConfigError(
                "WORKER_ENABLED_JOB_TYPES cannot combine 'none' with explicit job types."
            )
        return frozenset()

    enabled = frozenset(items)
    unknown = enabled - SUPPORTED_JOB_TYPES
    if unknown:
        raise WorkerConfigError(f"Unknown worker job_type: {', '.join(sorted(unknown))}")
    return enabled


def build_worker_instance_id(
    *,
    hostname: str | None = None,
    process_id: int | None = None,
    boot_uuid: str | None = None,
    instance_name: str | None = None,
) -> str:
    host = _safe_part(hostname or socket.gethostname(), max_length=36)
    pid = process_id if process_id is not None else os.getpid()
    boot = _safe_part(boot_uuid or uuid.uuid4().hex, max_length=16)
    parts = []
    if instance_name:
        parts.append(_safe_part(instance_name, max_length=24))
    parts.extend([host, f"pid-{pid}", f"boot-{boot}"])
    value = ":".join(parts)
    return value[:100]


def _safe_part(value: str, *, max_length: int) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return safe[:max_length] or "worker"
