from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from app.core.config import get_settings
from app.evaluation.local_chunk_ablation import (
    CHUNK_ABLATION_DATASET_NAME,
    ChunkAblationCase,
    ProfileChunk,
    build_chunk_ablation_inputs,
    build_profile_chunks,
    chunk_ablation_profiles,
    collection_name_for_profile,
)
from app.ingest.embedding import (
    EmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
    probe_lmstudio_embedding_dimension,
)
from app.ingest.qdrant import (
    HttpQdrantClient,
    QdrantCollectionConfig,
    QdrantPoint,
    QdrantVectorStore,
)
from app.rag.rerank import RerankCandidate, RerankError, create_reranker
from app.rag.retrieval import HttpQdrantSearchClient, RetrievalFilters

DEFAULT_OUTPUT_DIR = Path("../artifacts/rag60-chunk-ablation")
DEFAULT_EMBEDDING_MODEL = "text-embedding-qwen3-embedding-4b"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
SAFE_GIT_SHA_RE = r"^[0-9a-f]{7,64}$"


class ChunkAblationRunError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the dev-only RAG-60 Japanese chunk retrieval ablation without "
            "changing the default ingest profile."
        )
    )
    parser.add_argument("--confirm-local-only", action="store_true")
    parser.add_argument("--geometry-only", action="store_true")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--git-sha", default="unknown")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER_MODEL)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--rerank-top-n", type=int, default=3)
    args = parser.parse_args()

    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    activity_path = output_dir / "activity.jsonl"
    _append_event(
        activity_path,
        run_id=run_id,
        event="run_started",
        status="running",
        git_sha=_safe_git_sha(args.git_sha),
        dataset_name=CHUNK_ABLATION_DATASET_NAME,
        geometry_only=args.geometry_only,
        top_k=args.top_k,
        rerank_top_n=args.rerank_top_n,
    )

    try:
        _validate_cli(args)
        dataset_fingerprint, stress_fingerprint, sources, cases = (
            build_chunk_ablation_inputs()
        )
        profile_payloads: list[dict[str, object]] = []
        prepared: list[tuple[Any, tuple[ProfileChunk, ...], Any]] = []
        for profile in chunk_ablation_profiles():
            records, geometry = build_profile_chunks(profile, sources)
            prepared.append((profile, records, geometry))
            safe_geometry = geometry.safe_dict()
            profile_payloads.append(
                {
                    **profile.safe_dict(),
                    "geometry": safe_geometry,
                }
            )
            _append_event(
                activity_path,
                run_id=run_id,
                event="geometry_completed",
                status="succeeded",
                dataset_fingerprint=dataset_fingerprint,
                base_stress_corpus_fingerprint=stress_fingerprint,
                **safe_geometry,
            )

        geometry_summary = {
            "schema_version": "rag60.chunk_ablation.geometry.v1",
            "run_id": run_id,
            "git_sha": _safe_git_sha(args.git_sha),
            "dataset_name": CHUNK_ABLATION_DATASET_NAME,
            "dataset_fingerprint": dataset_fingerprint,
            "base_stress_corpus_fingerprint": stress_fingerprint,
            "case_count": len(cases),
            "source_count": len(sources),
            "profiles": profile_payloads,
            "raw_content_persisted": False,
        }
        _write_safe_json(output_dir / f"{run_id}-geometry.json", geometry_summary)
        if args.geometry_only:
            _append_event(
                activity_path,
                run_id=run_id,
                event="run_completed",
                status="succeeded",
                reason_codes=["geometry_only"],
            )
            print(
                json.dumps(
                    {
                        "status": "succeeded",
                        "run_id": run_id,
                        "stage": "geometry_only",
                        "profile_count": len(prepared),
                        "output_dir": str(output_dir),
                    },
                    sort_keys=True,
                )
            )
            return 0

        settings = get_settings()
        _validate_local_settings(settings)
        probe = probe_lmstudio_embedding_dimension(
            settings,
            model_name=args.embedding_model,
        )
        experiment_settings = settings.model_copy(
            update={
                "embedding_provider": "lmstudio",
                "embedding_model": args.embedding_model,
                "embedding_vector_dimension": probe.dimension,
                "rerank_provider": "local",
                "reranker_model": args.reranker_model,
                "retrieval_cache_enabled": False,
                "trace_export_enabled": False,
                "trace_export_provider": "none",
            }
        )
        _append_event(
            activity_path,
            run_id=run_id,
            event="model_preflight_completed",
            status="succeeded",
            embedding_model_requested=probe.requested_model,
            embedding_model_resolved=probe.resolved_model,
            embedding_dimension=probe.dimension,
            reranker_model=args.reranker_model,
            model_download_allowed=args.allow_model_download,
        )
        if not args.allow_model_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        embedding_adapter = create_embedding_adapter(experiment_settings)
        reranker = create_reranker(experiment_settings)
        query_vectors = _embed_batched(
            embedding_adapter,
            [case.question for case in cases],
        )
        retrieval_results: list[dict[str, object]] = []
        for profile, records, geometry in prepared:
            started = time.perf_counter()
            collection_name = collection_name_for_profile(
                base_name=experiment_settings.qdrant_collection_name,
                dataset_fingerprint=dataset_fingerprint,
                corpus_fingerprint=geometry.corpus_fingerprint,
                profile_fingerprint=profile.fingerprint,
                embedding_model=probe.resolved_model,
                embedding_dimension=probe.dimension,
            )
            _append_event(
                activity_path,
                run_id=run_id,
                event="profile_started",
                status="running",
                profile_id=profile.profile_id,
                chunk_profile_fingerprint=profile.fingerprint,
                corpus_fingerprint=geometry.corpus_fingerprint,
                collection_name=collection_name,
            )
            result = _run_profile(
                records=records,
                cases=cases,
                query_vectors=query_vectors,
                embedding_adapter=embedding_adapter,
                reranker=reranker,
                collection_name=collection_name,
                qdrant_url=experiment_settings.qdrant_url,
                qdrant_timeout_seconds=experiment_settings.qdrant_timeout_seconds,
                embedding_dimension=probe.dimension,
                embedding_model=probe.resolved_model,
                upsert_batch_size=experiment_settings.qdrant_upsert_batch_size,
                top_k=args.top_k,
                rerank_top_n=args.rerank_top_n,
            )
            result.update(
                {
                    "profile_id": profile.profile_id,
                    "chunk_profile_fingerprint": profile.fingerprint,
                    "corpus_fingerprint": geometry.corpus_fingerprint,
                    "collection_name": collection_name,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            retrieval_results.append(result)
            _append_event(
                activity_path,
                run_id=run_id,
                event="profile_completed",
                status="succeeded",
                **_safe_profile_event(result),
            )

        finalists = _select_finalists(retrieval_results)
        summary = {
            "schema_version": "rag60.chunk_ablation.retrieval.v1",
            "run_id": run_id,
            "git_sha": _safe_git_sha(args.git_sha),
            "dataset_name": CHUNK_ABLATION_DATASET_NAME,
            "dataset_fingerprint": dataset_fingerprint,
            "base_stress_corpus_fingerprint": stress_fingerprint,
            "embedding_model_requested": probe.requested_model,
            "embedding_model_resolved": probe.resolved_model,
            "embedding_dimension": probe.dimension,
            "reranker_model": args.reranker_model,
            "top_k": args.top_k,
            "rerank_top_n": args.rerank_top_n,
            "profiles": retrieval_results,
            "selected_for_end_to_end": finalists,
            "selection_limit": 2,
            "selection_rule": (
                "no pipeline failures; no_context <= C0; recall and fact_recall "
                "not below C0; at least one of recall, fact_recall, or MRR above C0"
            ),
            "raw_content_persisted": False,
            "promotion_decision": "not_evaluated_retrieval_screen_only",
        }
        _write_safe_json(output_dir / f"{run_id}-retrieval.json", summary)
        _write_markdown(output_dir / f"{run_id}-retrieval.md", summary)
        _append_event(
            activity_path,
            run_id=run_id,
            event="run_completed",
            status="succeeded",
            selected_for_end_to_end=finalists,
            reason_codes=(
                ["retrieval_finalists_selected"]
                if finalists
                else ["no_candidate_beats_c0"]
            ),
        )
        print(
            json.dumps(
                {
                    "status": "succeeded",
                    "run_id": run_id,
                    "profile_count": len(retrieval_results),
                    "selected_for_end_to_end": finalists,
                    "output_dir": str(output_dir),
                },
                sort_keys=True,
            )
        )
        return 0
    except ChunkAblationRunError as exc:
        reason_code = exc.reason_code
    except EmbeddingAdapterError as exc:
        reason_code = exc.error_code
    except RerankError:
        reason_code = "reranker_unavailable"
    except Exception:
        reason_code = "chunk_ablation_unexpected_failure"

    _append_event(
        activity_path,
        run_id=run_id,
        event="run_completed",
        status="blocked",
        reason_codes=[reason_code],
    )
    print(
        json.dumps(
            {
                "status": "blocked",
                "run_id": run_id,
                "reason_codes": [reason_code],
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 2


def _run_profile(
    *,
    records: tuple[ProfileChunk, ...],
    cases: tuple[ChunkAblationCase, ...],
    query_vectors: Sequence[Sequence[float]],
    embedding_adapter: EmbeddingAdapter,
    reranker: Any,
    collection_name: str,
    qdrant_url: str,
    qdrant_timeout_seconds: float,
    embedding_dimension: int,
    embedding_model: str,
    upsert_batch_size: int,
    top_k: int,
    rerank_top_n: int,
) -> dict[str, object]:
    chunk_vectors = _embed_batched(
        embedding_adapter,
        [record.chunk.content_text for record in records],
    )
    store = QdrantVectorStore(
        client=HttpQdrantClient(
            url=qdrant_url,
            timeout_seconds=qdrant_timeout_seconds,
        ),
        config=QdrantCollectionConfig(
            name=collection_name,
            vector_dimension=embedding_dimension,
        ),
        create_collection=True,
    )
    store.ensure_collection()
    store.upsert(
        [
            QdrantPoint(
                point_id=record.point_id,
                vector=[float(value) for value in vector],
                payload={
                    "logical_document_id": _source_number(record.source_key),
                    "document_version_id": _source_number(record.source_key),
                    "document_chunk_id": record.point_id,
                    "chunk_index": record.chunk.chunk_index,
                    "modality": "text",
                    "is_active": True,
                    "logical_document_status": "active",
                    "document_version_status": "ready",
                    "source_key": record.source_key,
                    "fact_ids": list(record.fact_ids),
                    "embedding_model": embedding_model,
                    "embedding_dimension": embedding_dimension,
                },
            )
            for record, vector in zip(records, chunk_vectors, strict=True)
        ],
        batch_size=upsert_batch_size,
    )
    search_client = HttpQdrantSearchClient(
        url=qdrant_url,
        timeout_seconds=qdrant_timeout_seconds,
    )
    by_point_id = {record.point_id: record for record in records}
    case_metrics: list[dict[str, object]] = []
    latencies_ms: list[float] = []
    for case, query_vector in zip(cases, query_vectors, strict=True):
        started = time.perf_counter()
        dense = search_client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=top_k,
            filters=RetrievalFilters(),
        )
        candidates = []
        for item in dense:
            if item.document_chunk_id is None:
                continue
            record = by_point_id.get(item.document_chunk_id)
            if record is None:
                continue
            candidates.append(
                RerankCandidate(
                    document_chunk_id=record.point_id,
                    text=record.chunk.content_text,
                    retrieval_score=item.retrieval_score,
                )
            )
        try:
            reranked = reranker.rerank(query=case.question, candidates=candidates)
        except (OSError, RerankError) as exc:
            raise ChunkAblationRunError("reranker_model_unavailable") from exc
        final_ids = [
            result.document_chunk_id for result in reranked[:rerank_top_n]
        ]
        final_records = [
            by_point_id[point_id]
            for point_id in final_ids
            if point_id in by_point_id
        ]
        dense_records = [
            by_point_id[item.document_chunk_id]
            for item in dense
            if item.document_chunk_id in by_point_id
        ]
        latencies_ms.append((time.perf_counter() - started) * 1000)
        case_metrics.append(
            _case_metric(
                case,
                dense_records=dense_records,
                final_records=final_records,
            )
        )
    return _aggregate_profile(case_metrics, latencies_ms)


def _case_metric(
    case: ChunkAblationCase,
    *,
    dense_records: Sequence[ProfileChunk],
    final_records: Sequence[ProfileChunk],
) -> dict[str, object]:
    expected_sources = set(case.expected_source_keys)
    expected_facts = set(case.expected_fact_ids)
    dense_sources = {record.source_key for record in dense_records}
    final_sources = {record.source_key for record in final_records}
    final_facts = {
        fact_id for record in final_records for fact_id in record.fact_ids
    }
    first_relevant_rank = next(
        (
            index
            for index, record in enumerate(final_records, start=1)
            if record.source_key in expected_sources
        ),
        None,
    )
    return {
        "case_hash": case.case_hash,
        "answerable": case.answerable,
        "language": case.language,
        "dense_recall_at_k": _coverage(expected_sources, dense_sources),
        "recall_at_n": _coverage(expected_sources, final_sources),
        "fact_recall_at_n": _coverage(expected_facts, final_facts),
        "reciprocal_rank": (
            0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
        ),
        "no_context": not final_records,
    }


def _aggregate_profile(
    case_metrics: Sequence[dict[str, object]],
    latencies_ms: Sequence[float],
) -> dict[str, object]:
    return {
        "case_count": len(case_metrics),
        "pipeline_failure_count": 0,
        "dense_recall_at_k": _mean(case_metrics, "dense_recall_at_k"),
        "recall_at_n": _mean(case_metrics, "recall_at_n"),
        "fact_recall_at_n": _mean(case_metrics, "fact_recall_at_n"),
        "mrr": _mean(case_metrics, "reciprocal_rank"),
        "no_context_rate": (
            sum(bool(item["no_context"]) for item in case_metrics)
            / len(case_metrics)
        ),
        "retrieval_p95_latency_ms": _percentile_95(latencies_ms),
        "case_metrics": list(case_metrics),
    }


def _select_finalists(results: Sequence[dict[str, object]]) -> list[str]:
    baseline = next(
        (result for result in results if result.get("profile_id") == "C0"),
        None,
    )
    if baseline is None:
        return []
    eligible = []
    for result in results:
        if result.get("profile_id") == "C0":
            continue
        if _as_int(result.get("pipeline_failure_count", 1)) != 0:
            continue
        if _as_float(result["no_context_rate"]) > _as_float(baseline["no_context_rate"]):
            continue
        recall = _as_float(result["recall_at_n"])
        fact_recall = _as_float(result["fact_recall_at_n"])
        mrr = _as_float(result["mrr"])
        if recall < _as_float(baseline["recall_at_n"]):
            continue
        if fact_recall < _as_float(baseline["fact_recall_at_n"]):
            continue
        if not (
            recall > _as_float(baseline["recall_at_n"])
            or fact_recall > _as_float(baseline["fact_recall_at_n"])
            or mrr > _as_float(baseline["mrr"])
        ):
            continue
        eligible.append(result)
    ranked = sorted(
        eligible,
        key=lambda item: (
            -_as_float(item["fact_recall_at_n"]),
            -_as_float(item["recall_at_n"]),
            -_as_float(item["mrr"]),
            _as_float(item["retrieval_p95_latency_ms"]),
            str(item["profile_id"]),
        ),
    )
    return [str(item["profile_id"]) for item in ranked[:2]]


def _embed_batched(
    adapter: EmbeddingAdapter,
    texts: Sequence[str],
    *,
    batch_size: int = 16,
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(adapter.embed_texts(texts[start : start + batch_size]))
    return vectors


def _validate_cli(args: argparse.Namespace) -> None:
    if not args.confirm_local_only:
        raise ChunkAblationRunError("local_confirmation_required")
    if args.top_k < 1 or args.top_k > 100:
        raise ChunkAblationRunError("top_k_invalid")
    if args.rerank_top_n < 1 or args.rerank_top_n > args.top_k:
        raise ChunkAblationRunError("rerank_top_n_invalid")
    if args.embedding_model != DEFAULT_EMBEDDING_MODEL:
        raise ChunkAblationRunError("embedding_model_not_frozen")
    if args.reranker_model != DEFAULT_RERANKER_MODEL:
        raise ChunkAblationRunError("reranker_model_not_frozen")


def _validate_local_settings(settings: Any) -> None:
    if settings.app_env.lower() == "production":
        raise ChunkAblationRunError("production_execution_forbidden")
    for raw_url, reason_code in (
        (settings.lmstudio_base_url, "lmstudio_must_be_local"),
        (settings.qdrant_url, "qdrant_must_be_local"),
    ):
        host = (urlparse(raw_url).hostname or "").lower()
        if host not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
            "qdrant",
            "ragproject-qdrant-1",
        }:
            raise ChunkAblationRunError(reason_code)


def _coverage(expected: set[str], observed: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & observed) / len(expected)


def _mean(items: Sequence[dict[str, object]], key: str) -> float:
    return statistics.fmean(_as_float(item[key]) for item in items)


def _percentile_95(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95) - 1e-9)))
    return round(ordered[index], 6)


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ChunkAblationRunError("metric_payload_invalid")
    return float(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChunkAblationRunError("metric_payload_invalid")
    return value


def _source_number(source_key: str) -> int:
    suffix = source_key.rsplit("_", 1)[-1]
    try:
        value = int(suffix)
    except ValueError as exc:
        raise ChunkAblationRunError("source_key_invalid") from exc
    if value < 1:
        raise ChunkAblationRunError("source_key_invalid")
    return value


def _safe_git_sha(value: str) -> str:
    import re

    cleaned = value.strip().lower()
    return cleaned if re.fullmatch(SAFE_GIT_SHA_RE, cleaned) else "unknown"


def _safe_profile_event(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "profile_id",
        "chunk_profile_fingerprint",
        "corpus_fingerprint",
        "collection_name",
        "case_count",
        "pipeline_failure_count",
        "dense_recall_at_k",
        "recall_at_n",
        "fact_recall_at_n",
        "mrr",
        "no_context_rate",
        "retrieval_p95_latency_ms",
        "elapsed_ms",
    }
    return {key: result[key] for key in allowed if key in result}


def _append_event(path: Path, *, run_id: str, event: str, **payload: object) -> None:
    record = {
        "schema_version": "rag60.activity.v1",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "event": event,
        **payload,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        handle.write("\n")


def _write_safe_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_markdown(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# RAG-60 chunk retrieval ablation",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Git SHA: `{summary['git_sha']}`",
        f"- Dataset fingerprint: `{summary['dataset_fingerprint']}`",
        f"- Embedding: `{summary['embedding_model_resolved']}`",
        f"- Reranker: `{summary['reranker_model']}`",
        f"- Finalists: `{summary['selected_for_end_to_end']}`",
        "- Raw question, chunk, answer, and context text persisted: `false`",
        "",
        "| Profile | Recall@N | Fact recall@N | MRR | No-context | p95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    raw_profiles = summary.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ChunkAblationRunError("summary_profiles_invalid")
    for item in raw_profiles:
        if not isinstance(item, dict):
            raise ChunkAblationRunError("summary_profiles_invalid")
        row = cast(dict[str, Any], item)
        lines.append(
            "| {profile_id} | {recall_at_n:.6f} | {fact_recall_at_n:.6f} | "
            "{mrr:.6f} | {no_context_rate:.6f} | "
            "{retrieval_p95_latency_ms:.3f} |".format(**row)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
