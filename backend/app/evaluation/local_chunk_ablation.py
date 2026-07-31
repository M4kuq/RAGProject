from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from statistics import median
from typing import Any

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.ingest.chunking import (
    Chunk,
    ChunkingConfig,
    FixedTokenChunker,
    chunk_profile_fingerprint,
)
from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionMetadata,
)

CHUNK_ABLATION_SCHEMA_VERSION = "rag60.chunk_ablation.v1"
CHUNK_ABLATION_DATASET_NAME = "local_accuracy_chunk_dev_v1"


@dataclass(frozen=True)
class ChunkAblationProfile:
    profile_id: str
    config: ChunkingConfig

    @property
    def fingerprint(self) -> str:
        return chunk_profile_fingerprint(self.config)

    def safe_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "chunk_size_tokens": self.config.chunk_size_tokens,
            "chunk_overlap_tokens": self.config.chunk_overlap_tokens,
            "tokenizer_profile": self.config.tokenizer_profile,
            "boundary_profile": self.config.boundary_profile,
            "chunk_profile_fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ChunkAblationSource:
    source_key: str
    document: ExtractedDocument
    facts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ChunkAblationCase:
    case_id: str
    case_hash: str
    question: str
    expected_source_keys: tuple[str, ...]
    expected_fact_ids: tuple[str, ...]
    answerable: bool
    language: str
    required_citation: bool
    tags: tuple[str, ...]
    required_facts: tuple[dict[str, object], ...]
    forbidden_claims: tuple[str, ...]


@dataclass(frozen=True)
class ProfileChunk:
    point_id: int
    source_key: str
    fact_ids: tuple[str, ...]
    chunk: Chunk


@dataclass(frozen=True)
class ProfileGeometry:
    profile_id: str
    profile_fingerprint: str
    corpus_fingerprint: str
    chunk_count: int
    source_count: int
    fact_chunk_count: int
    min_char_count: int
    median_char_count: int
    max_char_count: int

    def safe_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "chunk_profile_fingerprint": self.profile_fingerprint,
            "corpus_fingerprint": self.corpus_fingerprint,
            "chunk_count": self.chunk_count,
            "source_count": self.source_count,
            "fact_chunk_count": self.fact_chunk_count,
            "min_char_count": self.min_char_count,
            "median_char_count": self.median_char_count,
            "max_char_count": self.max_char_count,
        }


def chunk_ablation_profiles() -> tuple[ChunkAblationProfile, ...]:
    return (
        ChunkAblationProfile(
            "C0",
            ChunkingConfig(
                chunk_size_tokens=512,
                chunk_overlap_tokens=128,
                tokenizer_profile="whitespace_v1",
                boundary_profile="fixed_v1",
            ),
        ),
        ChunkAblationProfile(
            "C1",
            ChunkingConfig(
                chunk_size_tokens=512,
                chunk_overlap_tokens=128,
                tokenizer_profile="japanese_aware_v1",
                boundary_profile="fixed_v1",
            ),
        ),
        ChunkAblationProfile(
            "C2",
            ChunkingConfig(
                chunk_size_tokens=256,
                chunk_overlap_tokens=64,
                tokenizer_profile="japanese_aware_v1",
                boundary_profile="fixed_v1",
            ),
        ),
        ChunkAblationProfile(
            "C3",
            ChunkingConfig(
                chunk_size_tokens=768,
                chunk_overlap_tokens=128,
                tokenizer_profile="japanese_aware_v1",
                boundary_profile="fixed_v1",
            ),
        ),
        ChunkAblationProfile(
            "C4",
            ChunkingConfig(
                chunk_size_tokens=512,
                chunk_overlap_tokens=128,
                tokenizer_profile="japanese_aware_v1",
                boundary_profile="structure_v1",
            ),
        ),
    )


def build_chunk_ablation_inputs() -> tuple[
    str,
    str,
    tuple[ChunkAblationSource, ...],
    tuple[ChunkAblationCase, ...],
]:
    manifest = build_local_accuracy_dev_manifest()
    sources = tuple(
        _stress_source(index, document)
        for index, document in enumerate(manifest.corpus_documents, start=1)
    )
    cases = tuple(_case_target(case) for case in manifest.cases)
    stress_corpus_fingerprint = _stress_corpus_fingerprint(sources)
    dataset_fingerprint = _fingerprint(
        {
            "schema_version": CHUNK_ABLATION_SCHEMA_VERSION,
            "dataset_name": CHUNK_ABLATION_DATASET_NAME,
            "base_dataset_content_fingerprint": manifest.content_fingerprint(),
            "stress_corpus_fingerprint": stress_corpus_fingerprint,
            "case_hashes": [case.case_hash for case in cases],
        }
    )
    return dataset_fingerprint, stress_corpus_fingerprint, sources, cases


def build_profile_chunks(
    profile: ChunkAblationProfile,
    sources: tuple[ChunkAblationSource, ...],
) -> tuple[tuple[ProfileChunk, ...], ProfileGeometry]:
    records: list[ProfileChunk] = []
    corpus_material: list[dict[str, object]] = []
    for source_index, source in enumerate(sources, start=1):
        chunks = FixedTokenChunker(profile.config).chunk(
            source.document,
            document_version_id=source_index,
        )
        for chunk in chunks:
            fact_ids = tuple(
                fact_id
                for fact_id, statement in source.facts
                if _normalized(statement) in _normalized(chunk.content_text)
            )
            point_id = (source_index * 100_000) + chunk.chunk_index + 1
            records.append(
                ProfileChunk(
                    point_id=point_id,
                    source_key=source.source_key,
                    fact_ids=fact_ids,
                    chunk=chunk,
                )
            )
            corpus_material.append(
                {
                    "source_key": source.source_key,
                    "chunk_hash": chunk.chunk_hash,
                    "fact_ids": fact_ids,
                    "token_count": chunk.token_count,
                    "char_count": chunk.char_count,
                }
            )
    char_counts = [record.chunk.char_count for record in records]
    corpus_fingerprint = _fingerprint(
        {
            "schema_version": CHUNK_ABLATION_SCHEMA_VERSION,
            "chunk_profile_fingerprint": profile.fingerprint,
            "chunks": corpus_material,
        }
    )
    geometry = ProfileGeometry(
        profile_id=profile.profile_id,
        profile_fingerprint=profile.fingerprint,
        corpus_fingerprint=corpus_fingerprint,
        chunk_count=len(records),
        source_count=len({record.source_key for record in records}),
        fact_chunk_count=sum(bool(record.fact_ids) for record in records),
        min_char_count=min(char_counts),
        median_char_count=int(median(char_counts)),
        max_char_count=max(char_counts),
    )
    return tuple(records), geometry


def collection_name_for_profile(
    *,
    base_name: str,
    dataset_fingerprint: str,
    corpus_fingerprint: str,
    profile_fingerprint: str,
    embedding_model: str,
    embedding_dimension: int,
) -> str:
    key = ":".join(
        (
            dataset_fingerprint,
            corpus_fingerprint,
            profile_fingerprint,
            embedding_model,
            str(embedding_dimension),
        )
    )
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    safe_base = "".join(char for char in base_name if char.isalnum() or char in "_-")
    if not safe_base:
        safe_base = "document_chunks"
    return f"{safe_base[:80]}_rag60_{suffix}"


def _stress_source(index: int, document: Any) -> ChunkAblationSource:
    facts = tuple((fact.fact_id, fact.statement) for fact in document.facts)
    is_japanese = any(any(ord(char) > 127 for char in statement) for _, statement in facts)
    if is_japanese:
        neutral = "この節は検索時の境界挙動だけを検証する中立的な説明です。"
    else:
        neutral = (
            "This neutral section exists only to exercise retrieval chunk boundaries "
            "without adding a facility fact."
        )
    intro = "\n".join(neutral for _ in range(40))
    appendix = "\n".join(neutral for _ in range(40))
    extracted = ExtractedDocument(
        pages=[
            ExtractedPage(
                intro,
                page_number=1,
                section_title="Boundary preface",
            ),
            ExtractedPage(
                document.body,
                page_number=2,
                section_title=document.title,
            ),
            ExtractedPage(
                appendix,
                page_number=3,
                section_title="Boundary appendix",
            ),
        ],
        metadata=ExtractionMetadata(
            extractor_name="rag60_chunk_stress_fixture",
            extractor_version="1",
            page_count=3,
        ),
    )
    return ChunkAblationSource(
        source_key=document.source_key,
        document=extracted,
        facts=facts,
    )


def _case_target(case: Any) -> ChunkAblationCase:
    source_keys = tuple(sorted({evidence.source_key for evidence in case.expected_evidence}))
    fact_ids = tuple(
        sorted(
            {
                fact_id
                for evidence in case.expected_evidence
                for fact_id in evidence.fact_ids
            }
        )
    )
    language = "ja" if "language:ja" in case.tags else "en"
    return ChunkAblationCase(
        case_id=case.case_key,
        case_hash=hashlib.sha256(case.case_key.encode("utf-8")).hexdigest(),
        question=case.question,
        expected_source_keys=source_keys,
        expected_fact_ids=fact_ids,
        answerable=case.answerable,
        language=language,
        required_citation=case.required_citation,
        tags=tuple(case.tags),
        required_facts=tuple(
            fact.model_dump(mode="json") for fact in case.required_facts
        ),
        forbidden_claims=tuple(case.forbidden_claims),
    )


def _stress_corpus_fingerprint(sources: tuple[ChunkAblationSource, ...]) -> str:
    material = []
    for source in sources:
        material.append(
            {
                "source_key": source.source_key,
                "page_hashes": [
                    hashlib.sha256(page.text.encode("utf-8")).hexdigest()
                    for page in source.document.pages
                ],
                "fact_ids": [fact_id for fact_id, _ in source.facts],
            }
        )
    return _fingerprint(material)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())
