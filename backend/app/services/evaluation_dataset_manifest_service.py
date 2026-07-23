from __future__ import annotations

import hashlib
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationFailed
from app.db.models import EvaluationDataset, User
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluation_datasets_v2 import (
    EvaluationDatasetManifestInput,
    EvaluationDatasetManifestV2,
    EvaluationDatasetValidationComposition,
    EvaluationDatasetValidationResponse,
    manifest_content_fingerprint,
    manifest_corpus_fingerprint,
    manifest_serialized_size,
)
from app.schemas.evaluations import (
    EvaluationDatasetImportResponse,
    EvaluationDatasetManifest,
)

_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


class EvaluationDatasetManifestService:
    def __init__(self, repository: EvaluationRepository) -> None:
        self.repository = repository

    def validate(
        self,
        *,
        manifest: EvaluationDatasetManifestInput,
    ) -> EvaluationDatasetValidationResponse:
        serialized_size = manifest_serialized_size(manifest)
        if serialized_size > 2 * 1024 * 1024:
            raise ValidationFailed({"dataset": "evaluation dataset exceeds 2 MiB"})
        if isinstance(manifest, EvaluationDatasetManifestV2):
            composition = _v2_composition(manifest)
            warnings: list[str] = []
        else:
            composition = _v1_composition(manifest)
            warnings = ["v1 datasets use the shared legacy corpus"]
        return EvaluationDatasetValidationResponse(
            manifest_schema_version=manifest.schema_version,
            dataset_name=manifest.dataset.dataset_name,
            version=manifest.dataset.version,
            content_fingerprint=manifest_content_fingerprint(manifest),
            corpus_fingerprint=manifest_corpus_fingerprint(manifest),
            serialized_size_bytes=serialized_size,
            composition=composition,
            warnings=warnings,
        )

    def _legacy_manifest_matches_existing(
        self,
        db: Session,
        *,
        dataset: EvaluationDataset,
        manifest: EvaluationDatasetManifest,
    ) -> bool:
        if (
            dataset.description != manifest.dataset.description
            or dataset.source_type != manifest.dataset.source_type.value
            or dataset.status != manifest.dataset.status.value
            or dataset.metadata_json != manifest.dataset.metadata_json
        ):
            return False
        stored_cases, _ = self.repository.list_cases(
            db,
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            offset=0,
            limit=None,
        )
        if len(stored_cases) != len(manifest.cases):
            return False
        expected_by_key = {case.case_key: case for case in manifest.cases}
        for stored in stored_cases:
            expected = expected_by_key.get(stored.case_key)
            if expected is None:
                return False
            if (
                stored.question != expected.question
                or stored.expected_answer != expected.expected_answer
                or stored.expected_keywords != expected.expected_keywords
                or stored.expected_document_ids != expected.expected_document_ids
                or stored.expected_chunk_ids != expected.expected_chunk_ids
                or stored.required_citation != expected.required_citation
                or stored.tags != expected.tags
                or stored.metadata_json != expected.metadata_json
                or stored.status != expected.status.value
            ):
                return False
        return True

    def import_manifest(
        self,
        db: Session,
        *,
        manifest: EvaluationDatasetManifestInput,
        user: User,
    ) -> EvaluationDatasetImportResponse:
        validation = self.validate(manifest=manifest)
        existing = self.repository.get_dataset_by_name_and_version(
            db,
            dataset_name=manifest.dataset.dataset_name,
            version=manifest.dataset.version,
        )
        if existing is not None:
            if existing.content_fingerprint is None:
                legacy_matches = (
                    isinstance(manifest, EvaluationDatasetManifest)
                    and getattr(existing, "corpus_mode", "shared_legacy") == "shared_legacy"
                    and self._legacy_manifest_matches_existing(
                        db, dataset=existing, manifest=manifest
                    )
                )
                if legacy_matches:
                    existing.content_fingerprint = validation.content_fingerprint
                    existing.manifest_schema_version = manifest.schema_version
                    db.commit()
                    db.refresh(existing)
                else:
                    raise ConflictError(
                        "dataset_version_conflict",
                        details={
                            "evaluation_dataset_id": existing.evaluation_dataset_id,
                            "dataset_name": existing.dataset_name,
                            "version": existing.version,
                        },
                    )
            elif existing.content_fingerprint != validation.content_fingerprint:
                raise ConflictError(
                    "dataset_version_conflict",
                    details={
                        "evaluation_dataset_id": existing.evaluation_dataset_id,
                        "dataset_name": existing.dataset_name,
                        "version": existing.version,
                    },
                )
            return EvaluationDatasetImportResponse(
                evaluation_dataset_id=existing.evaluation_dataset_id,
                dataset_name=existing.dataset_name,
                version=existing.version,
                content_fingerprint=validation.content_fingerprint,
                corpus_fingerprint=existing.corpus_fingerprint,
                case_count=self.repository.count_cases(
                    db,
                    evaluation_dataset_id=existing.evaluation_dataset_id,
                ),
                imported_case_count=0,
                result_code="unchanged",
            )

        isolated = isinstance(manifest, EvaluationDatasetManifestV2)
        try:
            dataset = self.repository.create_dataset(
                db,
                dataset_name=manifest.dataset.dataset_name,
                description=manifest.dataset.description,
                version=manifest.dataset.version,
                source_type=manifest.dataset.source_type.value,
                status=manifest.dataset.status.value,
                metadata_json=manifest.dataset.metadata_json,
                created_by=user.user_id,
                manifest_schema_version=manifest.schema_version,
                content_fingerprint=validation.content_fingerprint,
                corpus_fingerprint=validation.corpus_fingerprint,
                corpus_mode="isolated" if isolated else "shared_legacy",
                corpus_status="not_prepared" if isolated else "shared_legacy",
            )
            if isinstance(manifest, EvaluationDatasetManifestV2):
                for case_v2_spec in manifest.cases:
                    metadata_json = dict(case_v2_spec.metadata_json or {})
                    metadata_json.update(
                        {
                            "answerable": case_v2_spec.answerable,
                            "required_facts": [
                                fact.model_dump(mode="json") for fact in case_v2_spec.required_facts
                            ],
                            "expected_evidence": [
                                evidence.model_dump(mode="json")
                                for evidence in case_v2_spec.expected_evidence
                            ],
                            "forbidden_claims": list(case_v2_spec.forbidden_claims),
                            "expected_strategy": (
                                case_v2_spec.expected_strategy.value
                                if case_v2_spec.expected_strategy is not None
                                else None
                            ),
                            "manifest_schema_version": manifest.schema_version,
                        }
                    )
                    self.repository.create_case(
                        db,
                        evaluation_dataset_id=dataset.evaluation_dataset_id,
                        case_key=case_v2_spec.case_key,
                        question=case_v2_spec.question,
                        expected_answer=case_v2_spec.expected_answer,
                        expected_keywords=[fact.statement for fact in case_v2_spec.required_facts],
                        expected_document_ids=[],
                        expected_chunk_ids=[],
                        required_citation=case_v2_spec.required_citation,
                        tags=case_v2_spec.tags,
                        metadata_json=metadata_json,
                        status=case_v2_spec.status.value,
                    )
                for document in manifest.corpus_documents:
                    self.repository.create_corpus_source(
                        db,
                        evaluation_dataset_id=dataset.evaluation_dataset_id,
                        source_key=document.source_key,
                        title=document.title,
                        body_text=document.body,
                        facts_json=[fact.model_dump(mode="json") for fact in document.facts],
                        content_hash=hashlib.sha256(document.body.encode("utf-8")).hexdigest(),
                    )
            else:
                for case_v1_spec in manifest.cases:
                    self.repository.create_case(
                        db,
                        evaluation_dataset_id=dataset.evaluation_dataset_id,
                        case_key=case_v1_spec.case_key,
                        question=case_v1_spec.question,
                        expected_answer=case_v1_spec.expected_answer,
                        expected_keywords=case_v1_spec.expected_keywords,
                        expected_document_ids=case_v1_spec.expected_document_ids,
                        expected_chunk_ids=case_v1_spec.expected_chunk_ids,
                        required_citation=case_v1_spec.required_citation,
                        tags=case_v1_spec.tags,
                        metadata_json=case_v1_spec.metadata_json,
                        status=case_v1_spec.status.value,
                    )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
        db.refresh(dataset)
        return EvaluationDatasetImportResponse(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            version=dataset.version,
            content_fingerprint=validation.content_fingerprint,
            corpus_fingerprint=validation.corpus_fingerprint,
            case_count=self.repository.count_cases(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
            ),
            imported_case_count=len(manifest.cases),
            result_code="created",
        )


def _v2_composition(
    manifest: EvaluationDatasetManifestV2,
) -> EvaluationDatasetValidationComposition:
    cases = manifest.cases
    tags = [set(case.tags) for case in cases]
    return EvaluationDatasetValidationComposition(
        case_count=len(cases),
        source_count=len(manifest.corpus_documents),
        fact_count=sum(len(document.facts) for document in manifest.corpus_documents),
        answerable_count=sum(case.answerable for case in cases),
        unanswerable_count=sum(not case.answerable for case in cases),
        language_ja_count=sum(
            "language:ja" in case_tags or _contains_japanese(case.question)
            for case, case_tags in zip(cases, tags, strict=True)
        ),
        language_en_count=sum(
            "language:en" in case_tags
            or ("language:ja" not in case_tags and not _contains_japanese(case.question))
            for case, case_tags in zip(cases, tags, strict=True)
        ),
        single_hop_count=sum(
            "single_hop" in case_tags or len(case.expected_evidence) == 1
            for case, case_tags in zip(cases, tags, strict=True)
        ),
        multi_hop_count=sum(
            "multi_hop" in case_tags or len(case.expected_evidence) > 1
            for case, case_tags in zip(cases, tags, strict=True)
        ),
        prompt_injection_count=sum("prompt_injection" in case_tags for case_tags in tags),
    )


def _v1_composition(
    manifest: EvaluationDatasetManifest,
) -> EvaluationDatasetValidationComposition:
    cases = manifest.cases
    return EvaluationDatasetValidationComposition(
        case_count=len(cases),
        source_count=0,
        fact_count=0,
        answerable_count=sum(case.expected_answer is not None for case in cases),
        unanswerable_count=sum(case.expected_answer is None for case in cases),
        language_ja_count=sum(
            "language:ja" in case.tags or _contains_japanese(case.question) for case in cases
        ),
        language_en_count=sum(
            "language:ja" not in case.tags and not _contains_japanese(case.question)
            for case in cases
        ),
        single_hop_count=sum("single_hop" in case.tags for case in cases),
        multi_hop_count=sum("multi_hop" in case.tags for case in cases),
        prompt_injection_count=sum("prompt_injection" in case.tags for case in cases),
    )


def _contains_japanese(value: str) -> bool:
    return bool(_JAPANESE_RE.search(value))
