from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Literal, Self, cast
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindReferenceDecision,
    AtomicClaimBlindReviewManifest,
    build_phase_a_commitment,
    compute_review_scope_fingerprint,
)
from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimFullHashBinding,
    AtomicClaimSourceContract,
    SafeId,
    Sha256,
    StrictRawFreeModel,
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
    write_model_json,
    write_raw_free_text,
)

_REVIEW_SCOPE_ID = "rag79_run112_existing_review_subset"
_REVIEW_TOOL = "rag83_local_per_claim_review"
_REVIEW_TOOL_VERSION = "v1"
_SESSION_COOKIE = "rag83_review_session"
_MAX_REQUEST_BYTES = 8_192
_RAW_FIELD_NAMES = frozenset(
    {
        "question",
        "question_text",
        "source_text",
        "answer",
        "answer_text",
        "context",
        "context_text",
        "context_items",
        "chunk",
        "chunks",
        "claim",
        "claim_text",
        "required_fact",
        "required_fact_text",
        "model_output",
        "pii",
        "secret",
    }
)

INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>RAG-83 blind per-claim review</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">RAG-83 / candidate-blind local review</p>
      <h1>1 claimずつ根拠を確認</h1>
      <p class="warning">
        表示内容はlocal process memoryだけで扱われます。画面保存・コピー・共有は行わないでください。
      </p>
      <p id="progress" aria-live="polite"></p>
    </header>
    <section class="card">
      <h2>Question</h2><pre id="question"></pre>
      <h2>Source evidence</h2><div id="source"></div>
      <h2>Answer</h2><pre id="answer"></pre>
      <h2>Runtime context</h2><div id="context"></div>
      <h2>Required fact</h2><pre id="fact"></pre>
    </section>
    <section class="actions" aria-label="review decision">
      <button id="unsupported" type="button">Unsupported</button>
      <button id="pending" type="button">保留</button>
      <button id="supported" type="button">Supported</button>
    </section>
    <section class="nav">
      <button id="previous" type="button">前へ</button>
      <button id="next" type="button">次へ</button>
    </section>
    <section class="finalize">
      <label>
        <input id="confirm" type="checkbox">全claimを自分で確認し、人手signoffとして確定します
      </label>
      <button id="finalize" type="button">ManifestとPhase A commitmentを生成</button>
      <p id="status" aria-live="assertive"></p>
    </section>
  </main>
  <script src="/app.js" defer></script>
</body>
</html>
"""

APP_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f5f3ee;
  color: #1d2a2e;
  font: 16px/1.55 system-ui, sans-serif;
}
main { max-width: 980px; margin: auto; padding: 32px; }
.eyebrow { font-weight: 700; color: #355b52; }
.warning { border-left: 4px solid #b65f32; padding: 10px 14px; background: #fff5ec; }
.card {
  background: #fff;
  border: 1px solid #d8d4c9;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 10px 30px #24332f12;
}
h2 { font-size: 1rem; margin-top: 24px; color: #355b52; }
pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #f8f8f5;
  padding: 12px;
  border-radius: 8px;
}
.source-item, .context-item {
  white-space: pre-wrap;
  word-break: break-word;
  background: #f8f8f5;
  padding: 12px;
  margin: 8px 0;
  border-radius: 8px;
}
.actions, .nav, .finalize {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 20px;
}
button {
  border: 0;
  border-radius: 8px;
  padding: 11px 16px;
  background: #315d53;
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}
button:disabled { opacity: .45; cursor: not-allowed; }
#pending { background: #8a6d2f; }
#unsupported { background: #8b4036; }
.finalize { background: #ece9df; padding: 16px; border-radius: 10px; }
.finalize label { flex: 1 1 100%; }
#status { font-weight: 700; }
"""

APP_JS = """(() => {
  "use strict";
  let currentIndex = 0;
  let csrfToken = "";

  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => { byId(id).textContent = value ?? ""; };
  const addTextBlocks = (id, items, formatter) => {
    const root = byId(id);
    root.replaceChildren();
    for (const item of items) {
      const node = document.createElement("div");
      node.className = id === "source" ? "source-item" : "context-item";
      node.textContent = formatter(item);
      root.appendChild(node);
    }
  };

  async function request(path, options = {}) {
    const response = await fetch(path, {cache: "no-store", credentials: "same-origin", ...options});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.reason_code || "review_request_failed");
    return payload;
  }

  async function load(index) {
    const state = await request("/api/state?index=" + encodeURIComponent(index));
    currentIndex = state.index;
    csrfToken = state.csrf_token;
    setText(
      "progress",
      String(state.index + 1) + " / " + String(state.total) +
        " — 完了 " + String(state.completed) + "、保留 " + String(state.pending)
    );
    setText("question", state.claim.question);
    setText("answer", state.claim.answer);
    setText("fact", state.claim.required_fact);
    addTextBlocks("source", state.claim.source_evidence, (item) => item.title + "\n" + item.body);
    addTextBlocks("context", state.claim.context_items, (item) => item);
    byId("previous").disabled = state.index === 0;
    byId("next").disabled = state.index + 1 >= state.total;
    for (const decision of ["supported", "unsupported", "pending"]) {
      byId(decision).setAttribute("aria-pressed", String(state.decision === decision));
    }
  }

  async function vote(decision) {
    await request("/api/vote", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({csrf_token: csrfToken, index: currentIndex, decision})
    });
    await load(currentIndex);
  }

  byId("supported").addEventListener("click", () => vote("supported").catch(showError));
  byId("unsupported").addEventListener("click", () => vote("unsupported").catch(showError));
  byId("pending").addEventListener("click", () => vote("pending").catch(showError));
  byId("previous").addEventListener("click", () => load(currentIndex - 1).catch(showError));
  byId("next").addEventListener("click", () => load(currentIndex + 1).catch(showError));
  byId("finalize").addEventListener("click", async () => {
    try {
      const result = await request("/api/finalize", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          csrf_token: csrfToken,
          confirm_human_signoff: byId("confirm").checked
        })
      });
      setText(
        "status",
        "確定しました。claim=" + String(result.claim_count) +
          ", manifest SHA-256=" + result.reference_manifest_sha256
      );
    } catch (error) {
      showError(error);
    }
  });

  function showError(error) {
    setText("status", error instanceof Error ? error.message : "review_request_failed");
  }

  load(0).catch(showError);
})();"""


class EvaluationAtomicClaimReviewWorkflowError(RuntimeError):
    """Stable fail-closed error for the local human review workflow."""


class _PrivateRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _AuthorityModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _AuthorityCalibration(_AuthorityModel):
    reviewed_observation_count: int = Field(ge=0)
    hash_matched_observation_count: int = Field(ge=0)
    unique_hash_matched_case_count: int = Field(ge=0)
    answerable_hash_matched_observation_count: int = Field(ge=0)


class _AuthorityCase(_AuthorityModel):
    case_id: SafeId
    answerable: bool
    required_fact_ids: tuple[SafeId, ...]
    o_answer_hashes: tuple[Sha256, ...]


class _AuthoritySummary(_AuthorityModel):
    schema_version: Literal["phase3.oracle_context_confirm.v1"]
    source_evaluation_run_id: Literal[112]
    dataset_name: Literal["local_accuracy_dev_v1"]
    dataset_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    generation_config_fingerprint: Sha256
    generation_prompt_profile: Literal["baseline"]
    generation_prompt_fingerprint: Sha256
    generation_temperature: float
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    atomic_calibration: _AuthorityCalibration
    cases: tuple[_AuthorityCase, ...]

    @model_validator(mode="after")
    def validate_temperature(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("atomic_claim_review_authority_temperature_drift")
        return self


class _LegacyReviewDecision(StrictRawFreeModel):
    profile: Literal["baseline"]
    case_id: SafeId
    answer_hash: Sha256
    codex_review_pass: bool
    manual_context_utilization: float = Field(ge=0.0, le=1.0)


class _LegacyReviewManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.oracle_codex_assisted_review.v1"]
    dataset_name: Literal["local_accuracy_dev_v1"]
    source_evaluation_run_id: Literal[112]
    reviewer_type: SafeId
    reviewer_version: SafeId | None = None
    reviewed_at_utc: datetime | None = None
    review_status: Literal["requires_human_signoff", "human_signed_off"] | None = None
    requires_human_signoff: bool | None = None
    raw_content_persisted: Literal[False] = False
    decisions: tuple[_LegacyReviewDecision, ...] = Field(min_length=1)


class AtomicClaimReviewScopeTarget(StrictRawFreeModel):
    case_id: SafeId
    answer_hash: Sha256

    @property
    def identity(self) -> tuple[str, str]:
        return (self.case_id, self.answer_hash)


class AtomicClaimReviewScopeManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.oracle_atomic_claim_review_scope.v1"]
    source: AtomicClaimSourceContract
    review_scope_id: Literal["rag79_run112_existing_review_subset"]
    authority_summary_sha256: Sha256
    legacy_review_manifest_sha256: Sha256
    legacy_reviewer_type: SafeId
    target_scope_fingerprint: Sha256
    target_observation_count: int = Field(gt=0)
    target_case_count: int = Field(gt=0)
    candidate_results_present: Literal[False] = False
    candidate_identifiers_present: Literal[False] = False
    raw_content_persisted: Literal[False] = False
    targets: tuple[AtomicClaimReviewScopeTarget, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_targets(self) -> Self:
        identities = [target.identity for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError("atomic_claim_review_scope_target_duplicate")
        if self.target_observation_count != len(self.targets):
            raise ValueError("atomic_claim_review_scope_count_drift")
        if self.target_case_count != len({target.case_id for target in self.targets}):
            raise ValueError("atomic_claim_review_scope_case_count_drift")
        expected = _target_scope_fingerprint(self.source, self.targets)
        if self.target_scope_fingerprint != expected:
            raise ValueError("atomic_claim_review_scope_fingerprint_drift")
        return self


class AtomicClaimPrivateObservation(_PrivateRuntimeModel):
    case_id: SafeId
    answer_hash: Sha256
    context_hash: Sha256
    answer_text: str = Field(min_length=1, max_length=12_000)
    context_items: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_raw_hashes(self) -> Self:
        if any(not item or len(item) > 6_000 for item in self.context_items):
            raise ValueError("atomic_claim_review_context_item_invalid")
        if len("\x00".join(self.context_items)) > 6_000:
            raise ValueError("atomic_claim_review_context_budget_drift")
        if _sha256(self.answer_text) != self.answer_hash:
            raise ValueError("atomic_claim_review_answer_hash_drift")
        if _sha256("\x00".join(self.context_items)) != self.context_hash:
            raise ValueError("atomic_claim_review_context_hash_drift")
        return self


class AtomicClaimPrivateReviewBundle(_PrivateRuntimeModel):
    schema_version: Literal["phase3.oracle_atomic_claim_private_review_input.v1"]
    scope_manifest_sha256: Sha256
    export_provenance: SafeId
    export_tool: SafeId
    export_tool_version: SafeId
    exported_at_utc: datetime
    candidate_results_present: Literal[False] = False
    candidate_identifiers_present: Literal[False] = False
    raw_content_runtime_only: Literal[True]
    observations: tuple[AtomicClaimPrivateObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        if not _is_utc(self.exported_at_utc):
            raise ValueError("atomic_claim_review_export_timestamp_not_utc")
        identities = [(item.case_id, item.answer_hash) for item in self.observations]
        if len(identities) != len(set(identities)):
            raise ValueError("atomic_claim_review_private_observation_duplicate")
        return self


class AtomicClaimReviewProgressDecision(AtomicClaimFullHashBinding):
    reference_supported: bool | None = None


class AtomicClaimReviewProgressManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.oracle_atomic_claim_review_progress.v1"]
    source: AtomicClaimSourceContract
    review_scope_id: Literal["rag79_run112_existing_review_subset"]
    review_scope_fingerprint: Sha256
    scope_manifest_sha256: Sha256
    private_input_sha256: Sha256
    reviewer_provenance: SafeId
    review_tool: Literal["rag83_local_per_claim_review"]
    review_tool_version: Literal["v1"]
    started_at_utc: datetime
    candidate_results_observed: Literal[False] = False
    candidate_identifiers_present: Literal[False] = False
    raw_content_persisted: Literal[False] = False
    decisions: tuple[AtomicClaimReviewProgressDecision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if not _is_utc(self.started_at_utc):
            raise ValueError("atomic_claim_review_progress_timestamp_not_utc")
        identities = [decision.identity for decision in self.decisions]
        if len(identities) != len(set(identities)):
            raise ValueError("atomic_claim_review_progress_identity_duplicate")
        return self


@dataclass(frozen=True)
class AtomicClaimReviewDisplayClaim:
    binding: AtomicClaimFullHashBinding
    question: str
    source_evidence: tuple[tuple[str, str], ...]
    answer: str
    context_items: tuple[str, ...]
    required_fact: str


@dataclass(frozen=True)
class AtomicClaimReviewLoadedInput:
    source: AtomicClaimSourceContract
    scope_manifest_sha256: str
    private_input_sha256: str
    review_scope_fingerprint: str
    claims: tuple[AtomicClaimReviewDisplayClaim, ...]


def prepare_review_scope(
    *,
    source_contract_bytes: bytes,
    source_contract: AtomicClaimSourceContract,
    authority_summary_bytes: bytes,
    authority_summary: _AuthoritySummary,
    legacy_review_bytes: bytes,
    legacy_review: _LegacyReviewManifest,
) -> AtomicClaimReviewScopeManifest:
    if not model_bytes_match(source_contract_bytes, source_contract):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_source_contract_bytes_model_mismatch"
        )
    try:
        authority_payload = json.loads(authority_summary_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_authority_summary_unreadable"
        ) from exc
    if _contains_forbidden_raw_key(authority_payload):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_authority_raw_field_rejected"
        )
    _validate_source_against_authority(source_contract, authority_summary)

    decisions = legacy_review.decisions
    calibration = authority_summary.atomic_calibration
    if len(decisions) != calibration.reviewed_observation_count:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_legacy_observation_count_drift"
        )
    if calibration.hash_matched_observation_count != len(decisions):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_legacy_hash_coverage_drift"
        )

    authority_by_case = {case.case_id: case for case in authority_summary.cases}
    all_case_ids: set[str] = set()
    targets: list[AtomicClaimReviewScopeTarget] = []
    for decision in decisions:
        authority_case = authority_by_case.get(decision.case_id)
        if authority_case is None or decision.answer_hash not in authority_case.o_answer_hashes:
            raise EvaluationAtomicClaimReviewWorkflowError(
                "atomic_claim_review_legacy_answer_hash_unbound"
            )
        all_case_ids.add(decision.case_id)
        if authority_case.answerable:
            targets.append(
                AtomicClaimReviewScopeTarget(
                    case_id=decision.case_id,
                    answer_hash=decision.answer_hash,
                )
            )

    if len(all_case_ids) != calibration.unique_hash_matched_case_count:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_legacy_case_count_drift"
        )
    if len(targets) != calibration.answerable_hash_matched_observation_count:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_answerable_target_count_drift"
        )
    sorted_targets = tuple(sorted(targets, key=lambda item: item.identity))
    target_fingerprint = _target_scope_fingerprint(source_contract, sorted_targets)
    return AtomicClaimReviewScopeManifest(
        schema_version="phase3.oracle_atomic_claim_review_scope.v1",
        source=source_contract,
        review_scope_id=_REVIEW_SCOPE_ID,
        authority_summary_sha256=_sha256_bytes(authority_summary_bytes),
        legacy_review_manifest_sha256=_sha256_bytes(legacy_review_bytes),
        legacy_reviewer_type=legacy_review.reviewer_type,
        target_scope_fingerprint=target_fingerprint,
        target_observation_count=len(sorted_targets),
        target_case_count=len({target.case_id for target in sorted_targets}),
        candidate_results_present=False,
        candidate_identifiers_present=False,
        raw_content_persisted=False,
        targets=sorted_targets,
    )


def prepare_review_scope_from_paths(
    source_contract_path: Path,
    authority_summary_path: Path,
    legacy_review_path: Path,
) -> AtomicClaimReviewScopeManifest:
    source_bytes, source_payload = _read_object(
        source_contract_path,
        "atomic_claim_review_source_contract_unreadable",
    )
    authority_bytes, authority_payload = _read_object(
        authority_summary_path,
        "atomic_claim_review_authority_summary_unreadable",
    )
    legacy_bytes, legacy_payload = _read_object(
        legacy_review_path,
        "atomic_claim_review_legacy_manifest_unreadable",
    )
    try:
        source = AtomicClaimSourceContract.model_validate(source_payload)
        authority = _AuthoritySummary.model_validate(authority_payload)
        legacy = _LegacyReviewManifest.model_validate(legacy_payload)
    except ValidationError as exc:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_prepare_schema_invalid"
        ) from exc
    return prepare_review_scope(
        source_contract_bytes=source_bytes,
        source_contract=source,
        authority_summary_bytes=authority_bytes,
        authority_summary=authority,
        legacy_review_bytes=legacy_bytes,
        legacy_review=legacy,
    )


def load_review_input(
    scope_manifest_path: Path,
    private_input_path: Path,
) -> AtomicClaimReviewLoadedInput:
    scope_bytes, scope_payload = _read_object(
        scope_manifest_path,
        "atomic_claim_review_scope_manifest_unreadable",
    )
    private_bytes, private_payload = _read_object(
        private_input_path,
        "atomic_claim_review_private_input_unreadable",
    )
    try:
        scope = AtomicClaimReviewScopeManifest.model_validate(scope_payload)
        private = AtomicClaimPrivateReviewBundle.model_validate(private_payload)
    except ValidationError as exc:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_input_schema_invalid"
        ) from exc
    if not model_bytes_match(scope_bytes, scope):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_scope_bytes_model_mismatch"
        )
    if private.scope_manifest_sha256 != _sha256_bytes(scope_bytes):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_private_scope_hash_drift"
        )
    private_by_target = {
        (observation.case_id, observation.answer_hash): observation
        for observation in private.observations
    }
    expected_targets = {target.identity for target in scope.targets}
    if set(private_by_target) != expected_targets:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_private_target_coverage_drift"
        )

    fixture = build_local_accuracy_dev_manifest()
    if fixture.content_fingerprint() != scope.source.dataset_content_fingerprint:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_dataset_fingerprint_drift"
        )
    fixture_by_case = {case.case_key: case for case in fixture.cases}
    documents = {document.source_key: document for document in fixture.corpus_documents}
    targets_by_case: dict[str, list[AtomicClaimReviewScopeTarget]] = {}
    for target in scope.targets:
        targets_by_case.setdefault(target.case_id, []).append(target)
    for case_targets in targets_by_case.values():
        case_targets.sort(key=lambda item: item.answer_hash)

    claims: list[AtomicClaimReviewDisplayClaim] = []
    for target in scope.targets:
        case = fixture_by_case.get(target.case_id)
        if case is None or not case.answerable:
            raise EvaluationAtomicClaimReviewWorkflowError(
                "atomic_claim_review_fixture_case_unbound"
            )
        observation = private_by_target[target.identity]
        source_keys = tuple(dict.fromkeys(item.source_key for item in case.expected_evidence))
        if not source_keys:
            raise EvaluationAtomicClaimReviewWorkflowError(
                "atomic_claim_review_source_evidence_missing"
            )
        try:
            evidence = tuple((documents[key].title, documents[key].body) for key in source_keys)
        except KeyError as exc:
            raise EvaluationAtomicClaimReviewWorkflowError(
                "atomic_claim_review_source_evidence_unbound"
            ) from exc
        source_hash = _sha256("\x00".join(f"{key}\x1f{documents[key].body}" for key in source_keys))
        observation_ordinal = targets_by_case[target.case_id].index(target)
        facts = tuple(case.required_facts)
        for fact_index, fact in enumerate(facts):
            claim_ordinal = observation_ordinal * len(facts) + fact_index
            binding = AtomicClaimFullHashBinding(
                case_id=target.case_id,
                question_hash=_sha256(case.question),
                source_hash=source_hash,
                answer_hash=target.answer_hash,
                context_hash=observation.context_hash,
                required_fact_id=fact.fact_id,
                required_fact_hash=_sha256(fact.statement),
                claim_ordinal=claim_ordinal,
            )
            claims.append(
                AtomicClaimReviewDisplayClaim(
                    binding=binding,
                    question=case.question,
                    source_evidence=evidence,
                    answer=observation.answer_text,
                    context_items=observation.context_items,
                    required_fact=fact.statement,
                )
            )

    sorted_claims = tuple(
        sorted(
            claims,
            key=lambda item: (
                item.binding.case_id,
                item.binding.required_fact_id,
                item.binding.claim_ordinal,
            ),
        )
    )
    identities = [claim.binding.identity for claim in sorted_claims]
    if len(identities) != len(set(identities)):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_claim_identity_duplicate"
        )
    decisions = tuple(
        AtomicClaimBlindReferenceDecision(
            **claim.binding.model_dump(mode="json"),
            reference_supported=False,
        )
        for claim in sorted_claims
    )
    fingerprint = compute_review_scope_fingerprint(scope.source, decisions)
    return AtomicClaimReviewLoadedInput(
        source=scope.source,
        scope_manifest_sha256=_sha256_bytes(scope_bytes),
        private_input_sha256=_sha256_bytes(private_bytes),
        review_scope_fingerprint=fingerprint,
        claims=sorted_claims,
    )


def validate_review_input(
    scope_manifest_path: Path,
    private_input_path: Path,
) -> dict[str, object]:
    loaded = load_review_input(scope_manifest_path, private_input_path)
    return {
        "status": "review_ready",
        "schema_version": "phase3.oracle_atomic_claim_private_review_input.v1",
        "claim_count": len(loaded.claims),
        "review_scope_fingerprint": loaded.review_scope_fingerprint,
        "candidate_results_present": False,
        "candidate_identifiers_present": False,
        "raw_content_persisted": False,
    }


class AtomicClaimReviewSession:
    def __init__(
        self,
        *,
        loaded: AtomicClaimReviewLoadedInput,
        progress_path: Path,
        reference_path: Path,
        commitment_path: Path,
        reviewer_provenance: str,
    ) -> None:
        self._loaded = loaded
        self._progress_path = progress_path
        self._reference_path = reference_path
        self._commitment_path = commitment_path
        self._reviewer_provenance = _validate_reviewer_provenance(reviewer_provenance)
        self._session_token = secrets.token_urlsafe(32)
        self._csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._progress = self._load_or_create_progress()

    @property
    def session_token(self) -> str:
        return self._session_token

    @property
    def csrf_token(self) -> str:
        return self._csrf_token

    def state(self, index: int) -> dict[str, object]:
        with self._lock:
            if index < 0 or index >= len(self._loaded.claims):
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_index_out_of_range"
                )
            claim = self._loaded.claims[index]
            progress = self._progress.decisions[index]
            completed = sum(
                item.reference_supported is not None for item in self._progress.decisions
            )
            return {
                "status": "reviewing",
                "index": index,
                "total": len(self._loaded.claims),
                "completed": completed,
                "pending": len(self._loaded.claims) - completed,
                "decision": (
                    "pending"
                    if progress.reference_supported is None
                    else "supported"
                    if progress.reference_supported
                    else "unsupported"
                ),
                "csrf_token": self._csrf_token,
                "claim": {
                    "case_id": claim.binding.case_id,
                    "required_fact_id": claim.binding.required_fact_id,
                    "claim_ordinal": claim.binding.claim_ordinal,
                    "question": claim.question,
                    "source_evidence": [
                        {"title": title, "body": body} for title, body in claim.source_evidence
                    ],
                    "answer": claim.answer,
                    "context_items": list(claim.context_items),
                    "required_fact": claim.required_fact,
                },
            }

    def vote(self, index: int, decision: str) -> dict[str, object]:
        with self._lock:
            if index < 0 or index >= len(self._loaded.claims):
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_index_out_of_range"
                )
            if decision not in {"supported", "unsupported", "pending"}:
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_decision_invalid"
                )
            value = None if decision == "pending" else decision == "supported"
            decisions = list(self._progress.decisions)
            decisions[index] = decisions[index].model_copy(update={"reference_supported": value})
            self._progress = self._progress.model_copy(update={"decisions": tuple(decisions)})
            write_model_json(self._progress_path, self._progress)
            return {
                "status": "saved",
                "completed": sum(item.reference_supported is not None for item in decisions),
                "pending": sum(item.reference_supported is None for item in decisions),
            }

    def finalize(self, *, confirm_human_signoff: bool) -> dict[str, object]:
        with self._lock:
            if not confirm_human_signoff:
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_explicit_confirmation_required"
                )
            if any(item.reference_supported is None for item in self._progress.decisions):
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_pending_decisions"
                )
            now = datetime.now(UTC)
            reference_decisions = tuple(
                AtomicClaimBlindReferenceDecision(
                    **decision.model_dump(
                        mode="json",
                        exclude={"reference_supported"},
                    ),
                    reference_supported=cast(bool, decision.reference_supported),
                )
                for decision in self._progress.decisions
            )
            reference = AtomicClaimBlindReviewManifest(
                schema_version="phase3.oracle_atomic_claim_blind_review.v1",
                source=self._loaded.source,
                review_scope_id=_REVIEW_SCOPE_ID,
                review_scope_fingerprint=self._loaded.review_scope_fingerprint,
                reviewer_provenance=self._reviewer_provenance,
                reviewer_type="human",
                review_tool=_REVIEW_TOOL,
                review_tool_version=_REVIEW_TOOL_VERSION,
                reviewed_at_utc=self._progress.started_at_utc,
                review_status="human_signed_off",
                requires_human_signoff=False,
                human_signoff_provenance=self._reviewer_provenance,
                human_signoff_at_utc=now,
                candidate_results_observed=False,
                candidate_identifiers_present=False,
                raw_content_persisted=False,
                decisions=reference_decisions,
            )
            reference_bytes = canonical_json_bytes(reference)
            commitment = build_phase_a_commitment(
                reference_bytes,
                reference,
                committed_at_utc=now,
            )
            write_raw_free_text(self._reference_path, reference_bytes.decode("utf-8"))
            write_model_json(self._commitment_path, commitment)
            return {
                "status": "finalized",
                "claim_count": len(reference_decisions),
                "reference_manifest_sha256": _sha256_bytes(reference_bytes),
                "commitment_schema_version": commitment.schema_version,
                "candidate_results_observed": False,
                "candidate_identifiers_present": False,
                "raw_content_persisted": False,
            }

    def _load_or_create_progress(self) -> AtomicClaimReviewProgressManifest:
        bindings = tuple(
            AtomicClaimReviewProgressDecision(
                **claim.binding.model_dump(mode="json"),
                reference_supported=None,
            )
            for claim in self._loaded.claims
        )
        if self._progress_path.exists():
            progress_bytes, payload = _read_object(
                self._progress_path,
                "atomic_claim_review_progress_unreadable",
            )
            try:
                progress = AtomicClaimReviewProgressManifest.model_validate(payload)
            except ValidationError as exc:
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_progress_schema_invalid"
                ) from exc
            if not model_bytes_match(progress_bytes, progress):
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_progress_bytes_model_mismatch"
                )
            expected_bindings = [
                item.model_dump(mode="json", exclude={"reference_supported"}) for item in bindings
            ]
            actual_bindings = [
                item.model_dump(mode="json", exclude={"reference_supported"})
                for item in progress.decisions
            ]
            if (
                progress.source != self._loaded.source
                or progress.review_scope_fingerprint != self._loaded.review_scope_fingerprint
                or progress.scope_manifest_sha256 != self._loaded.scope_manifest_sha256
                or progress.private_input_sha256 != self._loaded.private_input_sha256
                or progress.reviewer_provenance != self._reviewer_provenance
                or actual_bindings != expected_bindings
            ):
                raise EvaluationAtomicClaimReviewWorkflowError(
                    "atomic_claim_review_progress_fingerprint_drift"
                )
            return progress

        progress = AtomicClaimReviewProgressManifest(
            schema_version="phase3.oracle_atomic_claim_review_progress.v1",
            source=self._loaded.source,
            review_scope_id=_REVIEW_SCOPE_ID,
            review_scope_fingerprint=self._loaded.review_scope_fingerprint,
            scope_manifest_sha256=self._loaded.scope_manifest_sha256,
            private_input_sha256=self._loaded.private_input_sha256,
            reviewer_provenance=self._reviewer_provenance,
            review_tool=_REVIEW_TOOL,
            review_tool_version=_REVIEW_TOOL_VERSION,
            started_at_utc=datetime.now(UTC),
            candidate_results_observed=False,
            candidate_identifiers_present=False,
            raw_content_persisted=False,
            decisions=bindings,
        )
        write_model_json(self._progress_path, progress)
        return progress


class AtomicClaimReviewHTTPServer(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        session: AtomicClaimReviewSession,
    ) -> None:
        self.review_session = session
        super().__init__(server_address, AtomicClaimReviewRequestHandler)

    @property
    def local_url(self) -> str:
        host, port = cast(tuple[str, int], self.server_address)
        return f"http://{host}:{port}"


class AtomicClaimReviewRequestHandler(BaseHTTPRequestHandler):
    server: AtomicClaimReviewHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if not self._valid_host():
            self._send_error("atomic_claim_review_host_rejected", HTTPStatus.BAD_REQUEST)
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send_bytes(
                INDEX_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                set_cookie=True,
            )
            return
        if parsed.path == "/app.css":
            self._send_bytes(APP_CSS.encode("utf-8"), "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send_bytes(APP_JS.encode("utf-8"), "text/javascript; charset=utf-8")
            return
        if parsed.path == "/api/state":
            if not self._valid_session():
                self._send_error("atomic_claim_review_session_rejected", HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query, keep_blank_values=False)
            try:
                index = int(query.get("index", ["0"])[0])
                payload = self.server.review_session.state(index)
            except (ValueError, EvaluationAtomicClaimReviewWorkflowError) as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, EvaluationAtomicClaimReviewWorkflowError)
                    else "atomic_claim_review_index_invalid"
                )
                self._send_error(reason, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload)
            return
        self._send_error("atomic_claim_review_route_not_found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._valid_host():
            self._send_error("atomic_claim_review_host_rejected", HTTPStatus.BAD_REQUEST)
            return
        if not self._valid_session():
            self._send_error("atomic_claim_review_session_rejected", HTTPStatus.UNAUTHORIZED)
            return
        expected_origin = self.server.local_url
        if self.headers.get("Origin") != expected_origin:
            self._send_error("atomic_claim_review_origin_rejected", HTTPStatus.FORBIDDEN)
            return
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site not in {None, "same-origin"}:
            self._send_error("atomic_claim_review_fetch_site_rejected", HTTPStatus.FORBIDDEN)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_error(
                "atomic_claim_review_content_type_rejected",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return
        payload = self._read_json_body()
        if payload is None:
            return
        csrf = payload.get("csrf_token")
        if not isinstance(csrf, str) or not hmac.compare_digest(
            csrf,
            self.server.review_session.csrf_token,
        ):
            self._send_error("atomic_claim_review_csrf_rejected", HTTPStatus.FORBIDDEN)
            return
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/vote":
                index = payload.get("index")
                decision = payload.get("decision")
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not isinstance(decision, str)
                ):
                    raise EvaluationAtomicClaimReviewWorkflowError(
                        "atomic_claim_review_vote_schema_invalid"
                    )
                result = self.server.review_session.vote(index, decision)
                self._send_json(result)
                return
            if parsed.path == "/api/finalize":
                confirm = payload.get("confirm_human_signoff")
                if not isinstance(confirm, bool):
                    raise EvaluationAtomicClaimReviewWorkflowError(
                        "atomic_claim_review_finalize_schema_invalid"
                    )
                result = self.server.review_session.finalize(confirm_human_signoff=confirm)
                self._send_json(result)
                return
        except EvaluationAtomicClaimReviewWorkflowError as exc:
            status = (
                HTTPStatus.CONFLICT
                if str(exc) == "atomic_claim_review_pending_decisions"
                else HTTPStatus.BAD_REQUEST
            )
            self._send_error(str(exc), status)
            return
        self._send_error("atomic_claim_review_route_not_found", HTTPStatus.NOT_FOUND)

    def _read_json_body(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_error(
                "atomic_claim_review_request_length_invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return None
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            self._send_error(
                "atomic_claim_review_request_size_rejected",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return None
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send_error(
                "atomic_claim_review_request_json_invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return None
        if not isinstance(payload, dict):
            self._send_error(
                "atomic_claim_review_request_schema_invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return None
        return cast(dict[str, object], payload)

    def _valid_host(self) -> bool:
        _, port = cast(tuple[str, int], self.server.server_address)
        return self.headers.get("Host") == f"127.0.0.1:{port}"

    def _valid_session(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        expected = f"{_SESSION_COOKIE}={self.server.review_session.session_token}"
        return any(
            hmac.compare_digest(part.strip(), expected)
            for part in cookie.split(";")
            if part.strip()
        )

    def _send_error(self, reason_code: str, status: HTTPStatus) -> None:
        self._send_json(
            {"status": "blocked", "reason_code": reason_code},
            status=status,
        )

    def _send_json(
        self,
        payload: dict[str, object],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8"),
            "application/json; charset=utf-8",
            status=status,
        )

    def _send_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        set_cookie: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), clipboard-write=()",
        )
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"{_SESSION_COOKIE}={self.server.review_session.session_token}; "
                "HttpOnly; SameSite=Strict; Path=/",
            )
        self.end_headers()
        self.wfile.write(payload)


def create_review_session(
    *,
    scope_manifest_path: Path,
    private_input_path: Path,
    output_dir: Path,
    reviewer_provenance: str,
) -> AtomicClaimReviewSession:
    if output_dir.exists() and output_dir.is_symlink():
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_output_symlink_rejected"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    loaded = load_review_input(scope_manifest_path, private_input_path)
    return AtomicClaimReviewSession(
        loaded=loaded,
        progress_path=output_dir / "rag83-review-progress.json",
        reference_path=output_dir / "rag83-reference-manifest.json",
        commitment_path=output_dir / "rag83-phase-a-commitment.json",
        reviewer_provenance=reviewer_provenance,
    )


def create_review_server(
    session: AtomicClaimReviewSession,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> AtomicClaimReviewHTTPServer:
    if host != "127.0.0.1":
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_non_loopback_bind_rejected"
        )
    if port < 0 or port > 65_535:
        raise EvaluationAtomicClaimReviewWorkflowError("atomic_claim_review_port_invalid")
    return AtomicClaimReviewHTTPServer((host, port), session)


def _read_object(path: Path, unreadable_reason: str) -> tuple[bytes, dict[str, object]]:
    try:
        return read_json_object(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationAtomicClaimReviewWorkflowError(unreadable_reason) from exc


def _validate_source_against_authority(
    source: AtomicClaimSourceContract,
    authority: _AuthoritySummary,
) -> None:
    expected = (
        source.source_evaluation_run_id,
        source.dataset_name,
        source.dataset_content_fingerprint,
        source.case_set_fingerprint,
        source.generation_config_fingerprint,
        source.generation_prompt_profile,
        source.generation_prompt_fingerprint,
        source.resolved_generation_model,
        source.generation_temperature,
        source.generation_max_context_chars,
        source.generation_max_output_chars,
        source.generation_max_output_tokens,
    )
    actual = (
        authority.source_evaluation_run_id,
        authority.dataset_name,
        authority.dataset_content_fingerprint,
        authority.case_set_fingerprint,
        authority.generation_config_fingerprint,
        authority.generation_prompt_profile,
        authority.generation_prompt_fingerprint,
        authority.resolved_generation_model,
        authority.generation_temperature,
        authority.generation_max_context_chars,
        authority.generation_max_output_chars,
        authority.generation_max_output_tokens,
    )
    if expected != actual:
        raise EvaluationAtomicClaimReviewWorkflowError("atomic_claim_review_source_authority_drift")
    if (
        build_local_accuracy_dev_manifest().content_fingerprint()
        != source.dataset_content_fingerprint
    ):
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_dataset_fingerprint_drift"
        )


def _target_scope_fingerprint(
    source: AtomicClaimSourceContract,
    targets: tuple[AtomicClaimReviewScopeTarget, ...],
) -> str:
    payload: dict[str, object] = {
        "source": source.model_dump(mode="json"),
        "targets": [
            target.model_dump(mode="json")
            for target in sorted(targets, key=lambda item: item.identity)
        ],
    }
    return _sha256_bytes(canonical_json_bytes(payload))


def _contains_forbidden_raw_key(payload: object) -> bool:
    if isinstance(payload, list):
        return any(_contains_forbidden_raw_key(item) for item in payload)
    if isinstance(payload, dict):
        return any(
            str(key).casefold() in _RAW_FIELD_NAMES or _contains_forbidden_raw_key(value)
            for key, value in payload.items()
        )
    return False


def _validate_reviewer_provenance(value: str) -> str:
    class _Reviewer(StrictRawFreeModel):
        value: SafeId

    try:
        return _Reviewer(value=value).value
    except ValidationError as exc:
        raise EvaluationAtomicClaimReviewWorkflowError(
            "atomic_claim_review_reviewer_provenance_invalid"
        ) from exc


def _sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)
