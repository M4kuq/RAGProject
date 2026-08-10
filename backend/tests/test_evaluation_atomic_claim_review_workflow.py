from __future__ import annotations

import hashlib
import http.client
import json
import multiprocessing
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.rag.generation import AnswerGenerationError, GenerationRequest, GenerationResult
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
    _join_review_case_process,
    create_review_server,
    create_review_session,
    generate_review_only_calibration_run,
    prepare_review_scope_from_paths,
    validate_review_input,
)

_DOM_BINDING_HARNESS = r"""
const baseUrl = process.argv[1];
const mode = process.argv[2] || "render";
const nativeFetch = globalThis.fetch;
let activeCookie = "";
let postCount = 0;
let successfulPostCount = 0;
let sameOriginPostCount = 0;

function createElement() {
  const listeners = new Map();
  const attributes = new Map();
  return {
    textContent: "",
    children: [],
    disabled: false,
    checked: false,
    clickCount: 0,
    replaceChildren() {
      this.children = [];
      this.textContent = "";
    },
    appendChild(child) {
      this.children.push(child);
      this.textContent = this.children.map((item) => item.textContent).join("");
    },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    setAttribute(name, value) {
      attributes.set(name, value);
    },
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    async click() {
      const handler = listeners.get("click");
      if (!handler) return false;
      this.clickCount += 1;
      await handler();
      return true;
    },
    hasListener(type) {
      return listeners.has(type);
    },
  };
}

function createDom() {
  const ids = [
    "progress", "question", "source", "answer", "context", "fact", "status",
    "previous", "next", "supported", "unsupported", "pending", "finalize", "confirm",
  ];
  const elements = new Map(ids.map((id) => [id, createElement()]));
  globalThis.document = {
    getElementById: (id) => elements.get(id),
    createElement,
  };
  return elements;
}

async function browserFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Cookie", activeCookie);
  const target = new URL(path, baseUrl);
  const method = (options.method || "GET").toUpperCase();
  if (method === "POST") {
    headers.set("Origin", new URL(baseUrl).origin);
    postCount += 1;
    if (target.origin === new URL(baseUrl).origin) sameOriginPostCount += 1;
  }
  const response = await nativeFetch(target, {...options, headers});
  if (method === "POST" && response.ok) successfulPostCount += 1;
  return response;
}

async function bootstrap() {
  const rootHeaders = activeCookie ? {Cookie: activeCookie} : {};
  const rootResponse = await nativeFetch(baseUrl + "/", {headers: rootHeaders});
  const html = await rootResponse.text();
  const issuedCookie = (rootResponse.headers.get("set-cookie") || "").split(";", 1)[0];
  if (issuedCookie) activeCookie = issuedCookie;
  const csp = rootResponse.headers.get("content-security-policy") || "";
  const scriptResponse = await nativeFetch(baseUrl + "/app.js");
  const script = await scriptResponse.text();
  const cssResponse = await nativeFetch(baseUrl + "/app.css");
  const css = await cssResponse.text();
  const elements = createDom();
  globalThis.fetch = browserFetch;

  try {
    Function(script)();
  } catch {
    process.stdout.write(JSON.stringify({
      status: "error",
      error_code: "review_script_parse_failed",
    }));
    return null;
  }
  for (let attempt = 0; attempt < 100 && !elements.get("progress").textContent; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return {
    csp,
    css,
    cssCache: cssResponse.headers.get("cache-control") || "",
    elements,
    html,
    scriptCache: scriptResponse.headers.get("cache-control") || "",
  };
}

async function readState(index = 0) {
  const stateResponse = await nativeFetch(baseUrl + "/api/state?index=" + String(index), {
    headers: {Cookie: activeCookie},
  });
  if (!stateResponse.ok) throw new Error("review_state_request_failed");
  return stateResponse.json();
}

async function verifyRender(page) {
  const state = await readState();
  const fieldLengths = Object.fromEntries(
    ["question", "source", "answer", "context", "fact"].map((id) => [
      id,
      page.elements.get(id).textContent.length,
    ]),
  );
  const allFieldsPopulated = Object.values(fieldLengths).every((length) => length > 0);
  process.stdout.write(JSON.stringify({
    status: allFieldsPopulated ? "ok" : "error",
    error_code: allFieldsPopulated ? null : "review_script_fields_empty",
    total: state.total,
    pending: state.pending,
    field_lengths: fieldLengths,
    script_linked: page.html.includes('src="/app.js"'),
    csp_default_none: page.csp.includes("default-src 'none'"),
    csp_script_self: page.csp.includes("script-src 'self'"),
    csp_connect_self: page.csp.includes("connect-src 'self'"),
    csp_unsafe_inline: page.csp.includes("'unsafe-inline'"),
  }));
}

async function verifyInteractions(page) {
  const elements = page.elements;
  const initial = await readState();
  const total = initial.total;
  const handlersBound = ["pending", "supported", "unsupported"].every(
    (id) => elements.get(id).hasListener("click"),
  );
  const supportedInvoked = await elements.get("supported").click();
  const supported = await readState();
  const counterUpdated = supported.completed === 1 && supported.pending === total - 1;
  const supportedSelected = elements.get("supported").getAttribute("aria-pressed") === "true";
  const unsupportedInvoked = await elements.get("unsupported").click();
  const unsupportedSelected = (
    elements.get("unsupported").getAttribute("aria-pressed") === "true"
  );
  const pendingInvoked = await elements.get("pending").click();
  const pending = await readState();
  const pendingSelected = elements.get("pending").getAttribute("aria-pressed") === "true";
  await elements.get("supported").click();
  const nextInvoked = await elements.get("next").click();
  const nextMoved = elements.get("progress").textContent.startsWith("2 /");
  const previousInvoked = await elements.get("previous").click();
  const previousMoved = elements.get("progress").textContent.startsWith("1 /");

  const reloaded = await bootstrap();
  if (!reloaded) throw new Error("review_reload_failed");
  const reloadedState = await readState();
  const reloadPreserved = (
    reloadedState.completed === 1 &&
    reloadedState.pending === total - 1 &&
    reloaded.elements.get("supported").getAttribute("aria-pressed") === "true"
  );
  await reloaded.elements.get("pending").click();
  const restored = await readState();

  process.stdout.write(JSON.stringify({
    status: "ok",
    error_code: null,
    total,
    handlers_bound: handlersBound,
    pending_handler_invoked: pendingInvoked,
    supported_handler_invoked: supportedInvoked,
    unsupported_handler_invoked: unsupportedInvoked,
    posts_same_origin: postCount === sameOriginPostCount,
    posts_succeeded: postCount === successfulPostCount,
    post_count: postCount,
    supported_selected: supportedSelected,
    unsupported_selected: unsupportedSelected,
    pending_selected: pendingSelected,
    selected_style_present: page.css.includes('button[aria-pressed="true"]'),
    counter_updated: counterUpdated,
    pending_restored_before_reload: pending.completed === 0 && pending.pending === total,
    reload_preserved: reloadPreserved,
    next_invoked: nextInvoked,
    next_moved: nextMoved,
    previous_invoked: previousInvoked,
    previous_moved: previousMoved,
    normal_reload_no_store: (
      page.scriptCache.includes("no-store") &&
      page.cssCache.includes("no-store") &&
      reloaded.scriptCache.includes("no-store") &&
      reloaded.cssCache.includes("no-store")
    ),
    final_completed: restored.completed,
    final_pending: restored.pending,
  }));
}

async function main() {
  const page = await bootstrap();
  if (!page) return;
  if (mode === "interactions") {
    await verifyInteractions(page);
    return;
  }
  await verifyRender(page);
}

main().catch(() => {
  process.stdout.write(JSON.stringify({
    status: "error",
    error_code: "review_script_execution_failed",
  }));
});
"""


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


def test_generate_review_only_run_uses_all_answerable_cases_and_is_raw_free(
    tmp_path: Path,
) -> None:
    raw_free = tmp_path / "raw-free"
    private = tmp_path / "private" / "input.json"

    generated = generate_review_only_calibration_run(
        review_run_id="rag83-review-only-test",
        raw_free_output_dir=raw_free,
        private_input_path=private,
        generator=_FixedReviewGenerator(),
        started_at_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )

    expected_cases = sum(case.answerable for case in build_local_accuracy_dev_manifest().cases)
    assert generated.selected_case_count == expected_cases
    assert generated.succeeded_case_count == expected_cases
    assert generated.pipeline_failure_count == 0
    assert generated.claim_count > expected_cases
    run_payload = json.loads(generated.run_manifest_path.read_text(encoding="utf-8"))
    scope_payload = json.loads(generated.scope_manifest_path.read_text(encoding="utf-8"))
    assert run_payload["source"]["review_run_id"] == "rag83-review-only-test"
    assert run_payload["source"]["selection_rule"] == "all_answerable_cases"
    assert run_payload["source"]["accuracy_metric_eligible"] is False
    assert scope_payload["review_scope_id"] == (
        "rag83_review_only_calibration_all_answerable_dev_v1"
    )
    rendered_raw_free = json.dumps(
        {"run": run_payload, "scope": scope_payload},
        sort_keys=True,
    )
    assert "answer_text" not in rendered_raw_free
    assert "context_items" not in rendered_raw_free
    ready = validate_review_input(generated.scope_manifest_path, private)
    assert ready["review_run_id"] == "rag83-review-only-test"
    assert ready["claim_count"] == generated.claim_count


def test_generate_review_only_run_retains_pipeline_failure_reason(
    tmp_path: Path,
) -> None:
    generated = generate_review_only_calibration_run(
        review_run_id="rag83-review-only-failure-test",
        raw_free_output_dir=tmp_path / "raw-free",
        private_input_path=tmp_path / "private" / "input.json",
        generator=_FixedReviewGenerator(fail_first=True),
        started_at_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert generated.pipeline_failure_count == 1
    assert generated.succeeded_case_count == generated.selected_case_count - 1
    run_payload = json.loads(generated.run_manifest_path.read_text(encoding="utf-8"))
    failed = [outcome for outcome in run_payload["outcomes"] if outcome["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["pipeline_failure_reason_code"] == "review_generation_test_failure"


def test_review_case_process_is_terminated_at_wall_clock_deadline() -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=time.sleep, args=(60.0,))
    process.start()

    completed = _join_review_case_process(process, timeout_seconds=0.05)

    assert completed is False
    assert process.is_alive() is False


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


def test_served_script_populates_all_five_review_fields_by_length(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_runtime_unavailable")
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

    try:
        completed = subprocess.run(
            [node, "-e", _DOM_BINDING_HARNESS, server.local_url],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert completed.returncode == 0
    rendered = json.loads(completed.stdout)
    assert rendered["status"] == "ok"
    assert rendered["error_code"] is None
    assert rendered["total"] >= 1
    assert rendered["pending"] == rendered["total"]
    assert all(
        int(rendered["field_lengths"][field]) > 0
        for field in ("question", "source", "answer", "context", "fact")
    )
    assert rendered["script_linked"] is True
    assert rendered["csp_default_none"] is True
    assert rendered["csp_script_self"] is True
    assert rendered["csp_connect_self"] is True
    assert rendered["csp_unsafe_inline"] is False


def test_served_script_buttons_update_and_resume_synthetic_review(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_runtime_unavailable")
    generated = generate_review_only_calibration_run(
        review_run_id="rag83-review-button-test",
        raw_free_output_dir=tmp_path / "raw-free",
        private_input_path=tmp_path / "private" / "input.json",
        generator=_FixedReviewGenerator(),
        started_at_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    output_dir = tmp_path / "output"
    session = create_review_session(
        scope_manifest_path=generated.scope_manifest_path,
        private_input_path=generated.private_input_path,
        output_dir=output_dir,
        reviewer_provenance="human:test-reviewer",
    )
    server = create_review_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        completed = subprocess.run(
            [node, "-e", _DOM_BINDING_HARNESS, server.local_url, "interactions"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert completed.returncode == 0
    rendered = json.loads(completed.stdout)
    assert rendered["status"] == "ok"
    assert rendered["error_code"] is None
    assert generated.claim_count == 36
    assert rendered["total"] == generated.claim_count
    for field in (
        "handlers_bound",
        "pending_handler_invoked",
        "supported_handler_invoked",
        "unsupported_handler_invoked",
        "posts_same_origin",
        "posts_succeeded",
        "supported_selected",
        "unsupported_selected",
        "pending_selected",
        "selected_style_present",
        "counter_updated",
        "pending_restored_before_reload",
        "reload_preserved",
        "next_invoked",
        "next_moved",
        "previous_invoked",
        "previous_moved",
        "normal_reload_no_store",
    ):
        assert rendered[field] is True, field
    assert rendered["post_count"] == 5
    assert rendered["final_completed"] == 0
    assert rendered["final_pending"] == rendered["total"]
    _assert_outputs_are_raw_free(
        [output_dir / "rag83-review-progress.json"],
        _private_runtime_values(generated.private_input_path),
    )


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


class _FixedReviewGenerator:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise AnswerGenerationError(error_category="test_failure")
        return GenerationResult(content="synthetic fixed answer [1]", usage=None)


def _prepare_input_files(tmp_path: Path) -> dict[str, Path]:
    fixture = build_local_accuracy_dev_manifest()
    case = next(item for item in fixture.cases if item.answerable)
    unrelated_failure_case = next(item for item in fixture.cases if not item.answerable)
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
            "reviewed_observation_count": 3,
            "hash_matched_observation_count": 3,
            "unique_hash_matched_case_count": 1,
            "answerable_hash_matched_observation_count": 3,
        },
        "cases": [
            {
                "case_id": case.case_key,
                "answerable": True,
                "required_fact_ids": [fact.fact_id for fact in case.required_facts],
                "o_answer_hashes": [answer_hash, answer_hash, answer_hash],
            },
            {
                "case_id": unrelated_failure_case.case_key,
                "answerable": False,
                "required_fact_ids": [],
                "o_answer_hashes": [None, "f" * 64, "f" * 64],
            },
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
            },
            {
                "profile": "non_baseline_candidate",
                "case_id": unrelated_failure_case.case_key,
                "answer_hash": "e" * 64,
                "codex_review_pass": False,
                "manual_context_utilization": 0.0,
            },
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
