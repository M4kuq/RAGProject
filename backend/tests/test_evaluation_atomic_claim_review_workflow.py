from __future__ import annotations

import hashlib
import http.client
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.scripts.run_evaluation_atomic_claim_review_workflow import main
from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
)
from app.services.evaluation_atomic_claim_calibration_service import (
    AtomicClaimSourceContract as CalibrationSourceContract,
)
from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimSourceContract,
    canonical_json_bytes,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    APP_JS,
    AtomicClaimPrivateReviewBundle,
    EvaluationAtomicClaimReviewWorkflowError,
    create_review_server,
    create_review_session,
    prepare_review_scope_from_paths,
    validate_review_input,
)


def test_common_contract_is_legacy_api_compatible() -> None:
    assert CalibrationSourceContract is AtomicClaimSourceContract


def test_prepare_scope_strips_legacy_review_results(tmp_path: Path) -> None:
    files = _prepare_input_files(tmp_path)
    scope = prepare_review_scope_from_paths(
        files["source"],
        files["summary"],
        files["legacy"],
    )
    rendered = json.dumps(scope.model_dump(mode="json"), sort_keys=True)

    assert scope.target_observation_count == 1
    assert scope.target_case_count == 1
    assert scope.candidate_results_present is False
    assert scope.candidate_identifiers_present is False
    assert "codex_review_pass" not in rendered
    assert "manual_context_utilization" not in rendered


def test_private_input_is_exactly_bound_and_candidate_fields_are_rejected(
    tmp_path: Path,
) -> None:
    files = _prepare_input_files(tmp_path)
    ready = validate_review_input(files["scope"], files["private"])

    assert ready["status"] == "review_ready"
    claim_count = ready["claim_count"]
    assert isinstance(claim_count, int)
    assert claim_count >= 1
    payload = json.loads(files["private"].read_text(encoding="utf-8"))
    payload["candidate_id"] = "must-not-enter-phase-a"
    with pytest.raises(ValidationError):
        AtomicClaimPrivateReviewBundle.model_validate(payload)

    payload = json.loads(files["private"].read_text(encoding="utf-8"))
    payload["scope_manifest_sha256"] = "0" * 64
    drifted = tmp_path / "private-drifted.json"
    _write_json(drifted, payload)
    with pytest.raises(
        EvaluationAtomicClaimReviewWorkflowError,
        match="atomic_claim_review_private_scope_hash_drift",
    ):
        validate_review_input(files["scope"], drifted)


def test_missing_private_input_fails_with_stable_cli_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _prepare_input_files(tmp_path)
    missing = tmp_path / "not-present.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rag83-review",
            "validate-input",
            "--scope-manifest",
            str(files["scope"]),
            "--private-input",
            str(missing),
        ],
    )

    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "atomic_claim_review_private_input_unreadable",
        "status": "blocked",
    }


def test_non_loopback_bind_is_rejected(tmp_path: Path) -> None:
    files = _prepare_input_files(tmp_path)
    session = create_review_session(
        scope_manifest_path=files["scope"],
        private_input_path=files["private"],
        output_dir=tmp_path / "output",
        reviewer_provenance="human:test-reviewer",
    )
    with pytest.raises(
        EvaluationAtomicClaimReviewWorkflowError,
        match="atomic_claim_review_non_loopback_bind_rejected",
    ):
        create_review_server(session, host="0.0.0.0", port=0)


def test_review_progress_resumes_without_persisting_raw_values(tmp_path: Path) -> None:
    files = _prepare_input_files(tmp_path)
    output_dir = tmp_path / "output"
    first = create_review_session(
        scope_manifest_path=files["scope"],
        private_input_path=files["private"],
        output_dir=output_dir,
        reviewer_provenance="human:test-reviewer",
    )
    first.vote(0, "supported")

    resumed = create_review_session(
        scope_manifest_path=files["scope"],
        private_input_path=files["private"],
        output_dir=output_dir,
        reviewer_provenance="human:test-reviewer",
    )
    state = resumed.state(0)
    assert state["completed"] == 1
    assert state["decision"] == "supported"
    _assert_outputs_are_raw_free(
        [output_dir / "rag83-review-progress.json"],
        _private_runtime_values(files["private"]),
    )


def test_local_http_review_security_and_synthetic_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _prepare_input_files(tmp_path)
    output_dir = tmp_path / "output"
    session = create_review_session(
        scope_manifest_path=files["scope"],
        private_input_path=files["private"],
        output_dir=output_dir,
        reviewer_provenance="human:test-reviewer",
    )
    server = create_review_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_address[1]}"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)

    try:
        connection.request("GET", "/", headers={"Host": host})
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store, max-age=0"
        assert "default-src 'none'" in (response.getheader("Content-Security-Policy") or "")
        cookie = (response.getheader("Set-Cookie") or "").split(";", 1)[0]
        assert cookie.startswith("rag83_review_session=")
        assert "HttpOnly" in (response.getheader("Set-Cookie") or "")
        assert "SameSite=Strict" in (response.getheader("Set-Cookie") or "")

        state = _request_json(
            connection,
            "GET",
            "/api/state?index=0",
            host=host,
            cookie=cookie,
        )
        total = int(state["total"])
        csrf = str(state["csrf_token"])

        wrong_origin = _request_json(
            connection,
            "POST",
            "/api/vote",
            host=host,
            cookie=cookie,
            origin="http://example.invalid",
            payload={"csrf_token": csrf, "index": 0, "decision": "supported"},
            expected_status=403,
        )
        assert wrong_origin["reason_code"] == "atomic_claim_review_origin_rejected"

        pending = _request_json(
            connection,
            "POST",
            "/api/finalize",
            host=host,
            cookie=cookie,
            origin=server.local_url,
            payload={"csrf_token": csrf, "confirm_human_signoff": True},
            expected_status=409,
        )
        assert pending["reason_code"] == "atomic_claim_review_pending_decisions"

        for index in range(total):
            saved = _request_json(
                connection,
                "POST",
                "/api/vote",
                host=host,
                cookie=cookie,
                origin=server.local_url,
                payload={"csrf_token": csrf, "index": index, "decision": "supported"},
            )
            assert saved["status"] == "saved"

        unconfirmed = _request_json(
            connection,
            "POST",
            "/api/finalize",
            host=host,
            cookie=cookie,
            origin=server.local_url,
            payload={"csrf_token": csrf, "confirm_human_signoff": False},
            expected_status=400,
        )
        assert unconfirmed["reason_code"] == "atomic_claim_review_explicit_confirmation_required"

        finalized = _request_json(
            connection,
            "POST",
            "/api/finalize",
            host=host,
            cookie=cookie,
            origin=server.local_url,
            payload={"csrf_token": csrf, "confirm_human_signoff": True},
        )
        assert finalized["status"] == "finalized"
        assert finalized["claim_count"] == total
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    reference_path = output_dir / "rag83-reference-manifest.json"
    commitment_path = output_dir / "rag83-phase-a-commitment.json"
    progress_path = output_dir / "rag83-review-progress.json"
    reference_bytes = reference_path.read_bytes()
    reference = AtomicClaimBlindReviewManifest.model_validate_json(reference_bytes)
    commitment = AtomicClaimBlindReviewCommitment.model_validate_json(commitment_path.read_bytes())
    assert reference.reviewer_type == "human"
    assert reference.requires_human_signoff is False
    assert reference.candidate_results_observed is False
    assert commitment.reference_manifest_sha256 == hashlib.sha256(reference_bytes).hexdigest()
    _assert_outputs_are_raw_free(
        [reference_path, commitment_path, progress_path],
        _private_runtime_values(files["private"]),
    )
    assert capsys.readouterr().out == ""


def test_http_host_session_csrf_and_browser_storage_controls(tmp_path: Path) -> None:
    files = _prepare_input_files(tmp_path)
    session = create_review_session(
        scope_manifest_path=files["scope"],
        private_input_path=files["private"],
        output_dir=tmp_path / "output",
        reviewer_provenance="human:test-reviewer",
    )
    server = create_review_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    host = f"127.0.0.1:{server.server_address[1]}"

    try:
        rejected = _request_json(
            connection,
            "GET",
            "/api/state?index=0",
            host="attacker.invalid",
            expected_status=400,
        )
        assert rejected["reason_code"] == "atomic_claim_review_host_rejected"

        connection.request("GET", "/", headers={"Host": host})
        response = connection.getresponse()
        response.read()
        cookie = (response.getheader("Set-Cookie") or "").split(";", 1)[0]

        no_session = _request_json(
            connection,
            "GET",
            "/api/state?index=0",
            host=host,
            expected_status=401,
        )
        assert no_session["reason_code"] == "atomic_claim_review_session_rejected"

        bad_csrf = _request_json(
            connection,
            "POST",
            "/api/vote",
            host=host,
            cookie=cookie,
            origin=server.local_url,
            payload={"csrf_token": "wrong", "index": 0, "decision": "supported"},
            expected_status=403,
        )
        assert bad_csrf["reason_code"] == "atomic_claim_review_csrf_rejected"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "localStorage" not in APP_JS
    assert "sessionStorage" not in APP_JS
    assert "caches." not in APP_JS
    assert "innerHTML" not in APP_JS


def _prepare_input_files(tmp_path: Path) -> dict[str, Path]:
    fixture = build_local_accuracy_dev_manifest()
    case = next(item for item in fixture.cases if item.answerable)
    answer_text = "synthetic-" + "answer-runtime-only"
    context_items = ("synthetic-" + "context-runtime-only",)
    answer_hash = _sha(answer_text)
    context_hash = _sha("\x00".join(context_items))
    source_payload = {
        "source_evaluation_run_id": 112,
        "dataset_name": "local_accuracy_dev_v1",
        "dataset_content_fingerprint": fixture.content_fingerprint(),
        "case_set_fingerprint": "1" * 64,
        "generation_config_fingerprint": "2" * 64,
        "generation_prompt_profile": "baseline",
        "generation_prompt_fingerprint": "3" * 64,
        "generation_budget_fingerprint": "4" * 64,
        "resolved_generation_model": "qwen/qwen3.5-9b",
        "generation_temperature": 0.0,
        "generation_max_context_chars": 6000,
        "generation_max_output_chars": 12000,
        "generation_max_output_tokens": 8192,
    }
    summary_payload = {
        "schema_version": "phase3.oracle_context_confirm.v1",
        **{
            key: value
            for key, value in source_payload.items()
            if key != "generation_budget_fingerprint"
        },
        "atomic_calibration": {
            "reviewed_observation_count": 1,
            "hash_matched_observation_count": 1,
            "unique_hash_matched_case_count": 1,
            "answerable_hash_matched_observation_count": 1,
        },
        "cases": [
            {
                "case_id": case.case_key,
                "answerable": True,
                "required_fact_ids": [fact.fact_id for fact in case.required_facts],
                "o_answer_hashes": [answer_hash],
            }
        ],
    }
    legacy_payload = {
        "schema_version": "phase3.oracle_codex_assisted_review.v1",
        "dataset_name": "local_accuracy_dev_v1",
        "source_evaluation_run_id": 112,
        "reviewer_type": "codex_assisted",
        "raw_content_persisted": False,
        "decisions": [
            {
                "profile": "baseline",
                "case_id": case.case_key,
                "answer_hash": answer_hash,
                "codex_review_pass": True,
                "manual_context_utilization": 1.0,
            }
        ],
    }
    source_path = tmp_path / "source.json"
    summary_path = tmp_path / "summary.json"
    legacy_path = tmp_path / "legacy.json"
    scope_path = tmp_path / "scope.json"
    private_path = tmp_path / "private.json"
    _write_json(source_path, source_payload)
    _write_json(summary_path, summary_payload)
    _write_json(legacy_path, legacy_payload)
    scope = prepare_review_scope_from_paths(source_path, summary_path, legacy_path)
    scope_bytes = canonical_json_bytes(scope)
    scope_path.write_bytes(scope_bytes)
    private_payload = {
        "schema_version": "phase3.oracle_atomic_claim_private_review_input.v1",
        "scope_manifest_sha256": hashlib.sha256(scope_bytes).hexdigest(),
        "export_provenance": "synthetic_test_adapter",
        "export_tool": "pytest_runtime_fixture",
        "export_tool_version": "v1",
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "candidate_results_present": False,
        "candidate_identifiers_present": False,
        "raw_content_runtime_only": True,
        "observations": [
            {
                "case_id": case.case_key,
                "answer_hash": answer_hash,
                "context_hash": context_hash,
                "answer_text": answer_text,
                "context_items": list(context_items),
            }
        ],
    }
    _write_json(private_path, private_payload)
    return {
        "source": source_path,
        "summary": summary_path,
        "legacy": legacy_path,
        "scope": scope_path,
        "private": private_path,
    }


def _request_json(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    *,
    host: str,
    cookie: str | None = None,
    origin: str | None = None,
    payload: dict[str, object] | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    headers = {"Host": host}
    body = None
    if cookie is not None:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response_body = response.read()
    assert response.status == expected_status
    parsed = json.loads(response_body)
    assert isinstance(parsed, dict)
    return parsed


def _private_runtime_values(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for item in payload["observations"]:
        values.append(item["answer_text"])
        values.extend(item["context_items"])
    return tuple(values)


def _assert_outputs_are_raw_free(paths: list[Path], raw_values: tuple[str, ...]) -> None:
    for path in paths:
        output = path.read_text(encoding="utf-8")
        if any(value in output for value in raw_values):
            pytest.fail("raw_content_persisted_in_review_output", pytrace=False)
        payload = json.loads(output)
        rendered = json.dumps(payload, sort_keys=True)
        for key in (
            '"question"',
            '"source_text"',
            '"answer_text"',
            '"context_items"',
            '"required_fact"',
            '"claim_text"',
            '"model_output"',
            '"pii"',
            '"secret"',
        ):
            if key in rendered:
                pytest.fail("raw_field_persisted_in_review_output", pytrace=False)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
