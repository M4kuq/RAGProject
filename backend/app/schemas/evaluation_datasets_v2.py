from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evaluations import (
    DATASET_MANIFEST_SCHEMA_VERSION,
    EvaluationDatasetManifest,
    EvaluationDatasetManifestInfo,
    EvaluationDatasetStatus,
    EvaluationRunRequestStrategy,
    MetricSpec,
    _assert_safe_json,
    _safe_key,
    _safe_text,
)

DATASET_MANIFEST_V2_SCHEMA_VERSION: Literal["phase3.evaluation_dataset.v2"] = (
    "phase3.evaluation_dataset.v2"
)
MAX_EVALUATION_DATASET_BYTES = 2 * 1024 * 1024
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|credential|token)\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


class EvaluationCorpusFactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=2000)

    @field_validator("fact_id")
    @classmethod
    def validate_fact_id(cls, value: str) -> str:
        return _safe_key(value, field_name="fact_id")

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _safe_text(value, max_length=2000) or ""


class EvaluationCorpusDocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=200_000)
    facts: list[EvaluationCorpusFactSpec] = Field(min_length=1, max_length=100)

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return _safe_key(value, field_name="source_key")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _safe_text(value, max_length=255) or ""

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return _safe_corpus_text(value, max_length=200_000)

    @model_validator(mode="after")
    def validate_facts(self) -> EvaluationCorpusDocumentSpec:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be unique within a source")
        normalized_body = _normalized_text(self.body)
        for fact in self.facts:
            if _normalized_text(fact.statement) not in normalized_body:
                raise ValueError(f"fact statement is missing from body: {fact.fact_id}")
        return self


class EvaluationRequiredFactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=2000)

    @field_validator("fact_id")
    @classmethod
    def validate_fact_id(cls, value: str) -> str:
        return _safe_key(value, field_name="fact_id")

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _safe_text(value, max_length=2000) or ""


class EvaluationExpectedEvidenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=120)
    fact_ids: list[str] = Field(min_length=1, max_length=20)
    locator: str | None = Field(default=None, max_length=255)
    role: Literal["supports_answer", "supports_abstention"] = "supports_answer"

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return _safe_key(value, field_name="source_key")

    @field_validator("fact_ids")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        normalized = [_safe_key(item, field_name="fact_id") for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("expected evidence fact_ids must be unique")
        return normalized

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str | None) -> str | None:
        return _safe_text(value, max_length=255)


class EvaluationCaseV2Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=8000)
    answerable: bool
    expected_answer: str | None = Field(default=None, max_length=8000)
    required_facts: list[EvaluationRequiredFactSpec] = Field(default_factory=list, max_length=20)
    expected_evidence: list[EvaluationExpectedEvidenceSpec] = Field(
        default_factory=list, max_length=20
    )
    forbidden_claims: list[str] = Field(min_length=1, max_length=20)
    required_citation: bool = True
    expected_strategy: EvaluationRunRequestStrategy | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata_json: dict[str, Any] | None = None
    status: EvaluationDatasetStatus = EvaluationDatasetStatus.ACTIVE

    @field_validator("case_key")
    @classmethod
    def validate_case_key(cls, value: str) -> str:
        return _safe_key(value, field_name="case_key")

    @field_validator("question", "expected_answer")
    @classmethod
    def validate_safe_text(cls, value: str | None) -> str | None:
        return _safe_text(value)

    @field_validator("forbidden_claims")
    @classmethod
    def validate_forbidden_claims(cls, value: list[str]) -> list[str]:
        normalized = [_safe_text(item, max_length=2000) or "" for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("forbidden_claims must be unique")
        return normalized

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        normalized = [_safe_text(item, max_length=100) or "" for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tags must be unique")
        return normalized

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value

    @model_validator(mode="after")
    def validate_answer_contract(self) -> EvaluationCaseV2Spec:
        fact_ids = [fact.fact_id for fact in self.required_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("required fact_ids must be unique")
        if self.answerable and (not self.expected_answer or not self.required_facts):
            raise ValueError("answerable cases require expected_answer and required_facts")
        if not self.answerable and self.required_facts:
            raise ValueError("unanswerable cases must not require facts")
        if self.answerable and not self.expected_evidence:
            raise ValueError("answerable cases require expected_evidence")
        return self


class EvaluationDatasetManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase3.evaluation_dataset.v2"] = DATASET_MANIFEST_V2_SCHEMA_VERSION
    dataset: EvaluationDatasetManifestInfo
    corpus_documents: list[EvaluationCorpusDocumentSpec] = Field(min_length=1, max_length=100)
    cases: list[EvaluationCaseV2Spec] = Field(min_length=1, max_length=500)
    metric_specs: list[MetricSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> EvaluationDatasetManifestV2:
        source_keys = [document.source_key for document in self.corpus_documents]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("source_key values must be unique")
        case_keys = [case.case_key for case in self.cases]
        if len(case_keys) != len(set(case_keys)):
            raise ValueError("case_key values must be unique")
        normalized_questions = [_normalized_text(case.question) for case in self.cases]
        if len(normalized_questions) != len(set(normalized_questions)):
            raise ValueError("questions must be unique")

        fact_catalog: dict[str, tuple[str, str]] = {}
        source_facts: dict[str, set[str]] = {}
        for document in self.corpus_documents:
            source_facts[document.source_key] = {fact.fact_id for fact in document.facts}
            for fact in document.facts:
                if fact.fact_id in fact_catalog:
                    raise ValueError("fact_id values must be globally unique")
                fact_catalog[fact.fact_id] = (document.source_key, fact.statement)

        for case in self.cases:
            required_ids = {fact.fact_id for fact in case.required_facts}
            covered_ids: set[str] = set()
            for required_fact in case.required_facts:
                catalog = fact_catalog.get(required_fact.fact_id)
                if catalog is None or catalog[1] != required_fact.statement:
                    raise ValueError(
                        f"required fact does not match corpus: {required_fact.fact_id}"
                    )
            for evidence in case.expected_evidence:
                facts = source_facts.get(evidence.source_key)
                if facts is None:
                    raise ValueError(f"evidence source is missing: {evidence.source_key}")
                if not set(evidence.fact_ids).issubset(facts):
                    raise ValueError("evidence fact does not belong to source")
                if evidence.role == "supports_answer":
                    covered_ids.update(evidence.fact_ids)
            if case.answerable and covered_ids != required_ids:
                raise ValueError(f"required facts are not fully covered: {case.case_key}")

        if _serialized_size(self) > MAX_EVALUATION_DATASET_BYTES:
            raise ValueError("evaluation dataset exceeds 2 MiB")
        return self

    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json"))

    def corpus_fingerprint(self) -> str:
        return canonical_fingerprint(
            [document.model_dump(mode="json") for document in self.corpus_documents]
        )


EvaluationDatasetManifestInput: TypeAlias = EvaluationDatasetManifest | EvaluationDatasetManifestV2


class EvaluationDatasetValidationComposition(BaseModel):
    case_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    answerable_count: int = Field(ge=0)
    unanswerable_count: int = Field(ge=0)
    language_ja_count: int = Field(ge=0)
    language_en_count: int = Field(ge=0)
    single_hop_count: int = Field(ge=0)
    multi_hop_count: int = Field(ge=0)
    prompt_injection_count: int = Field(ge=0)


class EvaluationDatasetValidationResponse(BaseModel):
    schema_version: Literal["phase3.evaluation_dataset_validation.v1"] = (
        "phase3.evaluation_dataset_validation.v1"
    )
    valid: Literal[True] = True
    manifest_schema_version: str
    dataset_name: str
    version: str
    content_fingerprint: str = Field(min_length=64, max_length=64)
    corpus_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    serialized_size_bytes: int = Field(ge=0, le=MAX_EVALUATION_DATASET_BYTES)
    composition: EvaluationDatasetValidationComposition
    warnings: list[str] = Field(default_factory=list)


class EvaluationCorpusSourceReadiness(BaseModel):
    source_key: str
    status: Literal["pending", "preparing", "ready", "failed"]
    logical_document_id: int | None = None
    document_version_id: int | None = None
    ingest_job_id: int | None = None
    fact_count: int = Field(ge=0)
    indexed_chunk_count: int = Field(ge=0)
    failure_code: str | None = None


class EvaluationCorpusReadinessResponse(BaseModel):
    schema_version: Literal["phase3.evaluation_corpus_readiness.v1"] = (
        "phase3.evaluation_corpus_readiness.v1"
    )
    evaluation_dataset_id: int
    dataset_name: str
    version: str
    corpus_mode: Literal["shared_legacy", "isolated"]
    corpus_status: Literal["shared_legacy", "not_prepared", "preparing", "ready", "failed"]
    ready: bool
    run_allowed: bool
    corpus_fingerprint: str | None = None
    source_count: int = Field(ge=0)
    ready_source_count: int = Field(ge=0)
    failed_source_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    present_fact_count: int = Field(ge=0)
    index_count: int = Field(ge=0)
    isolated_fact_retrieval_count: int = Field(ge=0)
    answerable_case_count: int = Field(ge=0)
    answerable_retrieval_count: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    failure_reasons: list[str] = Field(default_factory=list)
    sources: list[EvaluationCorpusSourceReadiness] = Field(default_factory=list)
    checked_at: datetime


class EvaluationCorpusPrepareResponse(BaseModel):
    schema_version: Literal["phase3.evaluation_corpus_prepare.v1"] = (
        "phase3.evaluation_corpus_prepare.v1"
    )
    evaluation_dataset_id: int
    corpus_status: Literal["preparing", "ready"]
    queued_source_count: int = Field(ge=0)
    reused_source_count: int = Field(ge=0)
    job_ids: list[int] = Field(default_factory=list)
    readiness: EvaluationCorpusReadinessResponse


def manifest_content_fingerprint(manifest: EvaluationDatasetManifestInput) -> str:
    if isinstance(manifest, EvaluationDatasetManifestV2):
        return manifest.content_fingerprint()
    return canonical_fingerprint(manifest.model_dump(mode="json"))


def manifest_corpus_fingerprint(
    manifest: EvaluationDatasetManifestInput,
) -> str | None:
    if isinstance(manifest, EvaluationDatasetManifestV2):
        return manifest.corpus_fingerprint()
    return None


def manifest_serialized_size(manifest: EvaluationDatasetManifestInput) -> int:
    return _serialized_size(manifest)


def canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _serialized_size(manifest: BaseModel) -> int:
    return len(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _safe_corpus_text(value: str, *, max_length: int) -> str:
    normalized = value.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError("corpus body length is invalid")
    if (
        _SECRET_ASSIGNMENT_RE.search(normalized)
        or _BEARER_RE.search(normalized)
        or _PRIVATE_KEY_RE.search(normalized)
    ):
        raise ValueError("corpus body contains a secret-shaped value")
    return normalized


def is_v1_manifest(manifest: EvaluationDatasetManifestInput) -> bool:
    return manifest.schema_version == DATASET_MANIFEST_SCHEMA_VERSION
