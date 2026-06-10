from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from app.core.job_utils import LeaseLostError
from app.db.session import SessionLocal
from app.workers.handlers.base import JobExecutionContext, JobHandler, JobHandlerResult
from app.workers.handlers.document_ingest_handler import DocumentIngestHandler
from app.workers.handlers.evaluation_run_handler import EvaluationRunHandler
from app.workers.handlers.graph_index_build_handler import GraphIndexBuildHandler
from app.workers.handlers.message_edit_regeneration_handler import MessageEditRegenerationHandler
from app.workers.handlers.qdrant_mirror_update_handler import QdrantMirrorUpdateHandler
from app.workers.handlers.temporary_chat_cleanup_handler import TemporaryChatCleanupHandler

logger = logging.getLogger(__name__)


class JobDispatcher:
    def __init__(
        self,
        handlers: Mapping[str, JobHandler] | None = None,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._handlers = dict(
            handlers or _default_handlers(cast(sessionmaker[Session], session_factory))
        )

    @property
    def supported_job_types(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def dispatch(self, context: JobExecutionContext) -> JobHandlerResult:
        handler = self._handlers.get(context.job_type)
        if handler is None:
            return JobHandlerResult.failed(
                error_code="unknown_job_type",
                error_message="Unknown job type.",
            )
        try:
            return handler.handle(context)
        except LeaseLostError:
            raise
        except Exception:
            logger.error(
                "job handler raised an unsafe exception",
                extra={"job_id": context.job_id, "job_type": context.job_type},
            )
            return JobHandlerResult.failed(
                error_code="internal_error",
                error_message="Job handler failed.",
            )


def _default_handlers(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, JobHandler]:
    return {
        "document_ingest": DocumentIngestHandler(session_factory=session_factory),
        "qdrant_mirror_update": QdrantMirrorUpdateHandler(session_factory=session_factory),
        "message_edit_regeneration": MessageEditRegenerationHandler(),
        "evaluation_run": EvaluationRunHandler(session_factory=session_factory),
        "temporary_chat_cleanup": TemporaryChatCleanupHandler(),
        "graph_index_build": GraphIndexBuildHandler(session_factory=session_factory),
    }
