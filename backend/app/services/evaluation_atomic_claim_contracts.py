"""Canonical raw-free contracts shared by RAG-81, RAG-82, and RAG-83."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SafeId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]


class StrictRawFreeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AtomicClaimSourceContract(StrictRawFreeModel):
    source_evaluation_run_id: Literal[112]
    dataset_name: Literal["local_accuracy_dev_v1"]
    dataset_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    generation_config_fingerprint: Sha256
    generation_prompt_profile: Literal["baseline"]
    generation_prompt_fingerprint: Sha256
    generation_budget_fingerprint: Sha256
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    generation_temperature: float
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]

    @model_validator(mode="after")
    def validate_frozen_temperature(self) -> AtomicClaimSourceContract:
        if self.generation_temperature != 0.0:
            raise ValueError("atomic_claim_generation_temperature_drift")
        return self


class AtomicClaimReviewCalibrationSourceContract(StrictRawFreeModel):
    """Raw-free authority for a new review-only dev calibration run."""

    schema_version: Literal["phase3.oracle_atomic_claim_review_source.v1"]
    source_kind: Literal["review_only_calibration"]
    review_run_id: SafeId
    dataset_name: Literal["local_accuracy_dev_v1"]
    dataset_content_fingerprint: Sha256
    selection_rule: Literal["all_answerable_cases"]
    selected_case_count: int = Field(gt=0)
    succeeded_case_count: int = Field(ge=0)
    pipeline_failure_count: int = Field(ge=0)
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    generation_config_fingerprint: Sha256
    generation_prompt_profile: Literal["baseline"]
    generation_prompt_fingerprint: Sha256
    generation_budget_fingerprint: Sha256
    generation_provider: Literal["lmstudio"]
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    generation_temperature: float
    reasoning_enabled: Literal[False]
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]
    generation_case_wall_clock_timeout_seconds: Literal[180]
    screening_only: Literal[True]
    accuracy_metric_eligible: Literal[False]
    gold_holdout_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]

    @model_validator(mode="after")
    def validate_review_only_source(self) -> AtomicClaimReviewCalibrationSourceContract:
        if self.generation_temperature != 0.0:
            raise ValueError("atomic_claim_review_generation_temperature_drift")
        if self.succeeded_case_count + self.pipeline_failure_count != self.selected_case_count:
            raise ValueError("atomic_claim_review_generation_count_drift")
        return self


AtomicClaimReviewSourceContract = (
    AtomicClaimSourceContract | AtomicClaimReviewCalibrationSourceContract
)


class AtomicClaimIdentityBinding(StrictRawFreeModel):
    case_id: SafeId
    required_fact_id: SafeId
    claim_ordinal: int = Field(ge=0)

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.case_id, self.required_fact_id, self.claim_ordinal)


class AtomicClaimLegacyHashBinding(AtomicClaimIdentityBinding):
    answer_hash: Sha256
    context_hash: Sha256

    @property
    def hash_bound_identity(self) -> tuple[str, str, str, str, int]:
        return (
            self.case_id,
            self.answer_hash,
            self.context_hash,
            self.required_fact_id,
            self.claim_ordinal,
        )


class AtomicClaimFullHashBinding(AtomicClaimLegacyHashBinding):
    question_hash: Sha256
    source_hash: Sha256
    required_fact_hash: Sha256

    def scope_binding(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "question_hash": self.question_hash,
            "source_hash": self.source_hash,
            "answer_hash": self.answer_hash,
            "context_hash": self.context_hash,
            "required_fact_id": self.required_fact_id,
            "required_fact_hash": self.required_fact_hash,
            "claim_ordinal": self.claim_ordinal,
        }


class AtomicClaimLegacyNotApplicableBinding(StrictRawFreeModel):
    case_id: SafeId
    answer_hash: Sha256
    context_hash: Sha256

    @property
    def observation_identity(self) -> tuple[str, str, str]:
        return (self.case_id, self.answer_hash, self.context_hash)


class AtomicClaimFullNotApplicableBinding(AtomicClaimLegacyNotApplicableBinding):
    question_hash: Sha256
    source_hash: Sha256


def canonical_json_bytes(payload: BaseModel | dict[str, object]) -> bytes:
    safe_payload = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return (
        json.dumps(
            safe_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def read_json_object(path: Path) -> tuple[bytes, dict[str, object]]:
    payload_bytes = path.read_bytes()
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    return payload_bytes, cast(dict[str, object], payload)


def model_bytes_match(payload_bytes: bytes, model: BaseModel) -> bool:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload == model.model_dump(mode="json")


def render_model_json(model: BaseModel) -> str:
    return model.model_dump_json(indent=2)


def write_raw_free_text(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise OSError("atomic_claim_output_symlink_rejected")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(
            rendered.rstrip("\n") + "\n",
            encoding="utf-8",
            newline="",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_model_json(path: Path, model: BaseModel) -> None:
    write_raw_free_text(path, render_model_json(model))


def print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2
