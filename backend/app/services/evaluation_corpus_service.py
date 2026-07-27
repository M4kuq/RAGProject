from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ResourceNotFound, ValidationFailed
from app.db.evaluation_models import EvaluationCorpusSource
from app.db.models import DocumentChunk, DocumentVersion, Job, User
from app.evaluation.metrics import RetrievedEvaluationItem
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluation_datasets_v2 import (
    EvaluationCorpusPrepareResponse,
    EvaluationCorpusReadinessResponse,
    EvaluationCorpusSourceReadiness,
)
from app.services.document_service import DocumentService

RetrievalProbe = Callable[
    [Session, str, Sequence[int], int, str],
    Sequence[RetrievedEvaluationItem],
]
logger = logging.getLogger(__name__)
_READINESS_CACHE_TTL = timedelta(minutes=5)


class EvaluationCorpusService:
    def __init__(
        self,
        repository: EvaluationRepository,
        *,
        document_service: DocumentService | None = None,
        retrieval_probe: RetrievalProbe | None = None,
    ) -> None:
        self.repository = repository
        self.document_service = document_service
        self.retrieval_probe = retrieval_probe

    def prepare(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        user: User,
        request_id: str | None,
    ) -> EvaluationCorpusPrepareResponse:
        dataset = self.repository.get_dataset(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )
        if dataset is None:
            raise ResourceNotFound()
        if dataset.corpus_mode != "isolated":
            raise ValidationFailed(
                {"evaluation_dataset_id": "v1 datasets use the shared legacy corpus"}
            )
        sources = self.repository.list_corpus_sources(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )
        if not sources:
            raise ValidationFailed({"corpus_documents": "dataset has no corpus sources"})

        document_service = self.document_service or DocumentService()
        queued = 0
        reused = 0
        job_ids: list[int] = []
        dataset.corpus_status = "preparing"
        dataset.corpus_failure_code = None
        db.commit()

        for original in sources:
            source = self.repository.get_corpus_source_by_key(
                db,
                evaluation_dataset_id=evaluation_dataset_id,
                source_key=original.source_key,
            )
            if source is None:
                continue
            version = (
                db.get(DocumentVersion, source.document_version_id)
                if source.document_version_id is not None
                else None
            )
            job = db.get(Job, source.ingest_job_id) if source.ingest_job_id else None
            stored_chunk_count = self.repository.count_chunks_for_version(
                db,
                document_version_id=source.document_version_id,
                require_active=False,
            )
            if (
                version is not None
                and version.status == "ready"
                and not version.is_active
                and stored_chunk_count > 0
            ):
                try:
                    document_service.approve_version(
                        db,
                        user=user,
                        logical_document_id=version.logical_document_id,
                        document_version_id=version.document_version_id,
                        request_id=request_id,
                    )
                    db.expire_all()
                    version = db.get(DocumentVersion, source.document_version_id)
                    job = db.get(Job, source.ingest_job_id) if source.ingest_job_id else None
                except Exception as exc:
                    self._record_prepare_failure(
                        db,
                        evaluation_dataset_id=evaluation_dataset_id,
                        source_key=original.source_key,
                        exc=exc,
                    )
                    continue
            if (
                version is not None
                and version.status == "ready"
                and version.is_active
                and self.repository.count_chunks_for_version(
                    db,
                    document_version_id=version.document_version_id,
                )
                > 0
            ):
                source.status = "ready"
                source.failure_code = None
                reused += 1
                db.commit()
                continue
            if (
                version is not None
                and version.status == "processing"
                and (job is None or job.status in {"queued", "running"})
            ):
                source.status = "preparing"
                if job is not None:
                    job_ids.append(job.job_id)
                reused += 1
                db.commit()
                continue
            try:
                upload = document_service.upload_document(
                    db,
                    user=user,
                    title=(f"Evaluation {dataset.dataset_name}@{dataset.version}: {source.title}"),
                    filename=f"{source.source_key}.txt",
                    content_type="text/plain",
                    content=source.body_text.encode("utf-8"),
                    request_id=request_id,
                    activate_after_ingest=True,
                    evaluation_dataset_id=evaluation_dataset_id,
                )
                source = self.repository.get_corpus_source_by_key(
                    db,
                    evaluation_dataset_id=evaluation_dataset_id,
                    source_key=original.source_key,
                )
                if source is None:
                    raise RuntimeError("evaluation corpus source disappeared")
                source.logical_document_id = upload.logical_document_id
                source.document_version_id = upload.document_version_id
                source.ingest_job_id = upload.job_id
                source.status = "preparing"
                source.failure_code = None
                source.updated_at = datetime.now(UTC)
                db.commit()
                queued += 1
                job_ids.append(upload.job_id)
            except Exception as exc:
                self._record_prepare_failure(
                    db,
                    evaluation_dataset_id=evaluation_dataset_id,
                    source_key=original.source_key,
                    exc=exc,
                )

        readiness = self.readiness(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )
        return EvaluationCorpusPrepareResponse(
            evaluation_dataset_id=evaluation_dataset_id,
            corpus_status="ready" if readiness.ready else "preparing",
            queued_source_count=queued,
            reused_source_count=reused,
            job_ids=sorted(set(job_ids)),
            readiness=readiness,
        )

    def _record_prepare_failure(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        source_key: str,
        exc: Exception,
    ) -> None:
        db.rollback()
        failure_code = (
            "corpus_storage_unwritable"
            if isinstance(exc, PermissionError)
            else "corpus_prepare_failed"
        )
        logger.warning(
            "evaluation corpus source preparation failed",
            extra={
                "evaluation_dataset_id": evaluation_dataset_id,
                "source_key": source_key,
                "error_code": failure_code,
                "error_type": type(exc).__name__,
            },
        )
        source = self.repository.get_corpus_source_by_key(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            source_key=source_key,
        )
        if source is not None:
            source.status = "failed"
            source.failure_code = failure_code
            source.updated_at = datetime.now(UTC)
            db.commit()

    def readiness(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationCorpusReadinessResponse:
        dataset = self.repository.get_dataset(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )
        if dataset is None:
            raise ResourceNotFound()
        now = datetime.now(UTC)
        if dataset.corpus_mode == "shared_legacy":
            return _legacy_readiness(
                dataset.evaluation_dataset_id, dataset.dataset_name, dataset.version, now
            )

        sources = self.repository.list_corpus_sources(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )
        responses: list[EvaluationCorpusSourceReadiness] = []
        source_texts: dict[str, str] = {}
        source_document_ids: dict[str, int] = {}
        ready_count = failed_count = fact_count = present_count = index_count = 0
        failures: list[str] = []

        for source in sources:
            version = (
                db.get(DocumentVersion, source.document_version_id)
                if source.document_version_id is not None
                else None
            )
            job = db.get(Job, source.ingest_job_id) if source.ingest_job_id else None
            chunk_count = self.repository.count_chunks_for_version(
                db,
                document_version_id=source.document_version_id,
            )
            if version is not None and version.status == "ready" and version.is_active:
                status = "ready" if chunk_count > 0 else "preparing"
                failure_code = None
            elif (version is not None and version.status == "failed") or (
                job is not None and job.status == "failed"
            ):
                status = "failed"
                failure_code = (
                    version.error_code
                    if version is not None and version.error_code
                    else (job.error_code if job is not None else None)
                ) or "corpus_ingest_failed"
            elif source.logical_document_id is not None:
                status = "preparing"
                failure_code = None
            else:
                failure_code = source.failure_code
                status = "failed" if failure_code else "pending"

            source.status = status
            source.failure_code = failure_code
            source.updated_at = now
            facts = source.facts_json or []
            fact_count += len(facts)
            if status == "ready":
                ready_count += 1
                source.prepared_at = source.prepared_at or now
                index_count += chunk_count
                chunks = list(
                    db.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.document_version_id == source.document_version_id)
                        .order_by(DocumentChunk.chunk_index.asc())
                    ).all()
                )
                indexed_text = " ".join(chunk.content_text for chunk in chunks)
                source_texts[source.source_key] = indexed_text
                if source.logical_document_id is not None:
                    source_document_ids[source.source_key] = source.logical_document_id
                present_count += sum(
                    _normalized_contains(indexed_text, str(fact.get("statement") or ""))
                    for fact in facts
                )
            elif status == "failed":
                failed_count += 1
                failures.append(failure_code or "corpus_ingest_failed")

            responses.append(
                EvaluationCorpusSourceReadiness(
                    source_key=source.source_key,
                    status=cast(
                        Literal["pending", "preparing", "ready", "failed"],
                        status,
                    ),
                    logical_document_id=source.logical_document_id,
                    document_version_id=source.document_version_id,
                    ingest_job_id=source.ingest_job_id,
                    fact_count=len(facts),
                    indexed_chunk_count=chunk_count,
                    failure_code=failure_code,
                )
            )

        cases, _ = self.repository.list_cases(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            status="active",
        )
        answerable = [
            case
            for case in cases
            if isinstance(case.metadata_json, dict) and case.metadata_json.get("answerable") is True
        ]
        answerable_expectations: list[tuple[str, list[int]]] = []
        for case in answerable:
            metadata = case.metadata_json or {}
            evidence = metadata.get("expected_evidence")
            evidence_rows = evidence if isinstance(evidence, list) else []
            source_keys = {
                str(row.get("source_key"))
                for row in evidence_rows
                if isinstance(row, dict) and row.get("source_key")
            }
            expected_ids = sorted(
                source_document_ids[key] for key in source_keys if key in source_document_ids
            )
            if expected_ids != list(case.expected_document_ids or []):
                case.expected_document_ids = expected_ids
                case.updated_at = now
            answerable_expectations.append((case.question, expected_ids))

        isolated_fact_retrieved = 0
        answerable_retrieved = 0
        probe_failed = False
        all_sources_indexed = bool(sources) and ready_count == len(sources)
        readiness_is_fresh = (
            dataset.corpus_status == "ready"
            and dataset.corpus_prepared_at is not None
            and dataset.readiness_checked_at is not None
            and _datetime_age(now, dataset.readiness_checked_at) <= _READINESS_CACHE_TTL
        )
        if all_sources_indexed and present_count == fact_count:
            if readiness_is_fresh:
                isolated_fact_retrieved = fact_count
                answerable_retrieved = len(answerable)
            else:
                (
                    isolated_fact_retrieved,
                    answerable_retrieved,
                    probe_failed,
                ) = self._run_retrieval_preflight(
                    db,
                    evaluation_dataset_id=evaluation_dataset_id,
                    sources=sources,
                    source_document_ids=source_document_ids,
                    answerable_expectations=answerable_expectations,
                )

        ready = bool(sources) and (
            all_sources_indexed
            and present_count == fact_count
            and isolated_fact_retrieved == fact_count
            and answerable_retrieved == len(answerable)
        )
        if failed_count:
            corpus_status = "failed"
        elif ready:
            corpus_status = "ready"
        elif any(source.logical_document_id is not None for source in sources):
            corpus_status = "preparing"
        else:
            corpus_status = "not_prepared"
        if present_count < fact_count:
            failures.append("corpus_fact_missing")
        if isolated_fact_retrieved < fact_count:
            failures.append("corpus_fact_not_retrievable")
        if answerable_retrieved < len(answerable):
            failures.append("corpus_required_fact_not_retrievable")
        if probe_failed:
            failures.append("corpus_retrieval_probe_failed")
        denominator = len(sources) + (fact_count * 2) + len(answerable)
        numerator = ready_count + present_count + isolated_fact_retrieved + answerable_retrieved
        coverage = round(numerator / denominator, 6) if denominator else 0.0

        dataset.corpus_status = corpus_status
        dataset.corpus_failure_code = sorted(set(failures))[0] if failures else None
        dataset.readiness_checked_at = now
        if ready:
            dataset.corpus_prepared_at = dataset.corpus_prepared_at or now
        db.commit()
        return EvaluationCorpusReadinessResponse(
            evaluation_dataset_id=evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            version=dataset.version,
            corpus_mode="isolated",
            corpus_status=cast(
                Literal["not_prepared", "preparing", "ready", "failed"],
                corpus_status,
            ),
            ready=ready,
            run_allowed=ready,
            corpus_fingerprint=dataset.corpus_fingerprint,
            source_count=len(sources),
            ready_source_count=ready_count,
            failed_source_count=failed_count,
            fact_count=fact_count,
            present_fact_count=present_count,
            index_count=index_count,
            isolated_fact_retrieval_count=isolated_fact_retrieved,
            answerable_case_count=len(answerable),
            answerable_retrieval_count=answerable_retrieved,
            coverage=coverage,
            failure_reasons=sorted(set(failures)),
            sources=responses,
            checked_at=now,
        )

    def _run_retrieval_preflight(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        sources: Sequence[EvaluationCorpusSource],
        source_document_ids: dict[str, int],
        answerable_expectations: Sequence[tuple[str, list[int]]],
    ) -> tuple[int, int, bool]:
        allowed_ids = sorted(set(source_document_ids.values()))
        if not allowed_ids:
            return 0, 0, True
        probe = self.retrieval_probe or _default_retrieval_probe(db)
        top_k = min(50, max(10, len(allowed_ids)))
        fact_retrieved = 0
        answerable_retrieved = 0
        probe_failed = False

        for source in sources:
            source_key = str(getattr(source, "source_key", ""))
            expected_document_id = source_document_ids.get(source_key)
            facts = getattr(source, "facts_json", None)
            if expected_document_id is None or not isinstance(facts, list):
                continue
            for fact in facts:
                statement = str(fact.get("statement") or "") if isinstance(fact, dict) else ""
                if not statement:
                    continue
                try:
                    items = probe(
                        db,
                        statement,
                        allowed_ids,
                        top_k,
                        _preflight_request_id(
                            evaluation_dataset_id,
                            kind="fact",
                            key=str(fact.get("fact_id") or source_key)
                            if isinstance(fact, dict)
                            else source_key,
                        ),
                    )
                except Exception:
                    probe_failed = True
                    continue
                returned_ids = _validated_retrieved_document_ids(items, allowed_ids)
                if returned_ids is not None and expected_document_id in returned_ids:
                    fact_retrieved += 1

        for question, expected_ids in answerable_expectations:
            if not expected_ids:
                continue
            try:
                items = probe(
                    db,
                    question,
                    allowed_ids,
                    top_k,
                    _preflight_request_id(
                        evaluation_dataset_id,
                        kind="case",
                        key=question,
                    ),
                )
            except Exception:
                probe_failed = True
                continue
            returned_ids = _validated_retrieved_document_ids(items, allowed_ids)
            if returned_ids is not None and set(expected_ids).issubset(returned_ids):
                answerable_retrieved += 1

        return fact_retrieved, answerable_retrieved, probe_failed


def _default_retrieval_probe(db: Session) -> RetrievalProbe:
    from app.core.config import get_settings
    from app.evaluation.rag_service import create_evaluation_rag_service
    from app.rag.strategy import RetrievalStrategy

    service = create_evaluation_rag_service(get_settings(), db)

    def probe(
        session: Session,
        query: str,
        logical_document_ids: Sequence[int],
        top_k: int,
        request_id: str,
    ) -> Sequence[RetrievedEvaluationItem]:
        result = service.evaluate_strategy(
            session,
            question=query,
            request_id=request_id,
            strategy_type=RetrievalStrategy.HYBRID,
            top_k=top_k,
            rerank_top_n=top_k,
            logical_document_ids=logical_document_ids,
        )
        if result.status != "succeeded":
            raise RuntimeError(result.error_code or "corpus_retrieval_probe_failed")
        return result.retrieved_items

    return probe


def _validated_retrieved_document_ids(
    items: Sequence[RetrievedEvaluationItem],
    allowed_ids: Sequence[int],
) -> set[int] | None:
    allowed = set(allowed_ids)
    returned = {item.logical_document_id for item in items if item.logical_document_id is not None}
    if any(item.logical_document_id is None for item in items) or not returned.issubset(allowed):
        return None
    return returned


def _preflight_request_id(
    evaluation_dataset_id: int,
    *,
    kind: str,
    key: str,
) -> str:
    import hashlib

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"evaluation-preflight-{evaluation_dataset_id}-{kind}-{digest}"


def _datetime_age(now: datetime, value: datetime) -> timedelta:
    comparable = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return now - comparable


def _legacy_readiness(
    evaluation_dataset_id: int,
    dataset_name: str,
    version: str,
    now: datetime,
) -> EvaluationCorpusReadinessResponse:
    return EvaluationCorpusReadinessResponse(
        evaluation_dataset_id=evaluation_dataset_id,
        dataset_name=dataset_name,
        version=version,
        corpus_mode="shared_legacy",
        corpus_status="shared_legacy",
        ready=True,
        run_allowed=True,
        source_count=0,
        ready_source_count=0,
        failed_source_count=0,
        fact_count=0,
        present_fact_count=0,
        index_count=0,
        isolated_fact_retrieval_count=0,
        answerable_case_count=0,
        answerable_retrieval_count=0,
        coverage=1.0,
        checked_at=now,
    )


def _normalized_contains(haystack: str, needle: str) -> bool:
    normalized_needle = " ".join(needle.casefold().split())
    if not normalized_needle:
        return False
    return normalized_needle in " ".join(haystack.casefold().split())
