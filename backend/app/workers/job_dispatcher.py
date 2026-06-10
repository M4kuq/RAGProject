from __future__ import annotations

import logging
from collections.abc import Mapping

from app.core.job_utils import LeaseLostError
from app.workers.handlers.base import JobExecutionContext, JobHandler, JobHandlerResult
from app.workers.handlers.document_ingest_handler import DocumentIngestHandler
from app.workers.handlers.evaluation_run_handler import EvaluationRunHandler
from app.workers.handlers.graph_index_build_handler import GraphIndexBuildHandler
from app.workers.handlers.message_edit_regeneration_handler import MessageEditRegenerationHandler
from app.workers.handlers.qdrant_mirror_update_handler import QdrantMirrorUpdateHandler
from app.workers.handlers.temporary_chat_cleanup_handler import TemporaryChatCleanupHandler

logger = logging.getLogger(__name__)


class JobDispatcher:
    def __init__(self, handlers: Mapping[str, JobHandler] | None = None) -> None:
        self._handlers = dict(handlers or _default_handlers())

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


def _default_handlers() -> dict[str, JobHandler]:
    return {
        "document_ingest": DocumentIngestHandler(),
        "qdrant_mirror_update": QdrantMirrorUpdateHandler(),
        "message_edit_regeneration": MessageEditRegenerationHandler(),
        "evaluation_run": EvaluationRunHandler(),
        "temporary_chat_cleanup": TemporaryChatCleanupHandler(),
        "graph_index_build": GraphIndexBuildHandler(),
    }
