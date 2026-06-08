from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import RetrievalRun
from app.evaluation.rag_service import EvaluationRagQuestionService
from app.ingest.embedding import FakeEmbeddingAdapter
from app.rag.generation import _truncate_output
from app.rag.rerank import FakeRerankerClient
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate, VectorSearchClient
from app.services.rag_service import RagService


class _EmptyVectorClient(VectorSearchClient):
    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        del collection_name, query_vector, limit, filters
        return []


def test_evaluation_marks_rewritten_insufficient_answer_as_no_context() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        service = RagService(
            settings=Settings(app_env="test"),
            embedding_adapter=FakeEmbeddingAdapter(dimension=4),
            vector_client=_EmptyVectorClient(),
            reranker=FakeRerankerClient(),
        )
        evaluator = EvaluationRagQuestionService(service)
        raw = "検索された文書には、この質問に答えるための十分な根拠がありません。 [1]"
        rewritten = _truncate_output(raw, max_chars=200)

        with session_factory() as db:
            run = service.repository.create_standalone_run(
                db,
                top_k=1,
                query_hash="a" * 64,
                request_id="test-eval-insufficient-answer",
                started_at=datetime.now(UTC),
            )
            db.commit()

            result = evaluator._no_context_result_if_insufficient_answer(
                db,
                retrieval_run_id=run.retrieval_run_id,
                answer_text=rewritten,
                rollback=False,
            )

            assert result is not None
            assert result.status == "failed"
            assert result.error_code == "no_context_found"
            stored = (
                db.query(RetrievalRun).filter_by(request_id="test-eval-insufficient-answer").one()
            )
            assert stored.status == "failed"
            assert stored.error_code == "no_context_found"
    finally:
        engine.dispose()
