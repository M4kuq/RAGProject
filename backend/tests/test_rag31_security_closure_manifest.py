import json
from pathlib import Path
from typing import Any

_MANIFEST_PATH = (
    Path(__file__).parents[2] / "docs" / "security" / "rag31_security_closure_manifest.json"
)


def _load_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_rag31_final_manifest_preserves_reviewable_history_and_file_union() -> None:
    manifest = _load_manifest()

    assert manifest["schema_version"] == "rag31-security-closure-v2"
    assert manifest["jira_issue"] == "RAG-73"
    assert manifest["main_sha"] == "96e5fc82ad2c253d000633ff9e9431e88998f74a"
    assert manifest["previous_audit"] == {
        "jira_issue": "RAG-69",
        "canonical_pull_request": 141,
        "preserved_without_rewrite": True,
    }

    integration = manifest["audited_integration"]
    assert integration["pull_request"] == 145
    assert integration["draft"] is True
    assert integration["base_pull_request"] == 141
    assert integration["base_sha"] == "c2ff8bf57a674e881efc4741d32eb440d87c2bfe"
    assert integration["merge_parents"] == [
        "fb4b47f3a7d8d58c055399a82b26d8ea961a49b1",
        "6f430554aee08d7070ed04bc3a6e65cdb159a4aa",
    ]

    ancestry = manifest["ancestry_verification"]
    assert ancestry["all_source_heads_are_ancestors"] is True
    assert set(ancestry["source_heads"]) == {
        "6f430554aee08d7070ed04bc3a6e65cdb159a4aa",
        "041a0d0e4abf275b04c43bbe8f8dd0b7fcca7ed4",
        "fb4b47f3a7d8d58c055399a82b26d8ea961a49b1",
    }

    files = manifest["file_set_verification"]
    assert files["pull_request_142_count"] == 8
    assert files["pull_request_144_count"] == 44
    assert files["source_intersection_count"] == 0
    assert files["source_union_count"] == 52
    assert files["integration_count"] == 55
    assert files["missing_from_integration"] == []
    assert set(files["integration_only"]) == {
        "backend/tests/test_rag31_security_closure_manifest.py",
        "docs/security/rag31_security_closure_manifest.json",
        "docs/security/rag31_security_closure_matrix.md",
    }

    migration = manifest["migration_verification"]
    assert migration["head_count"] == 1
    assert migration["head_revision"] == "0025_qwen_cost_controls"


def test_rag31_final_manifest_maps_all_acceptance_criteria_without_score_mixing() -> None:
    manifest = _load_manifest()
    criteria = {entry["id"]: entry for entry in manifest["acceptance_criteria"]}

    assert set(criteria) == set(range(1, 8))
    assert {entry["status"] for entry in criteria.values()} == {"satisfied"}
    assert set(manifest["individual_evidence"]) == {
        "rag70",
        "rag71",
        "rag72",
        "aggregation_policy",
    }
    assert "Do not combine" in manifest["individual_evidence"]["aggregation_policy"]

    combined = manifest["combined_gate"]
    assert combined["expected_status"] == "pass"
    assert combined["transport_mode"] == "mocked_synthetic_only"
    assert combined["live_provider_calls"] == 0
    assert combined["clean_non_regression_required"] is True
    assert "pipeline_failure" in combined["required_zero_metrics"]

    decision = manifest["closure_decision"]
    assert decision["rag31_implementation_status"] == "implementation_closure_ready"
    assert decision["production_secure_or_deployed"] is False
    assert decision["jira_status_must_remain_review"] is True
    assert decision["canonical_review_pull_request"] == 145
    assert decision["do_not_merge_close_retarget_or_undraft_existing_pull_requests"] is True


def test_rag31_final_manifest_has_explicit_non_destructive_rollback() -> None:
    rollback = _load_manifest()["rollback"]

    assert rollback["primary_action"] == "Do not merge Draft PR 145."
    assert rollback["code_origins"] == [
        "fb4b47f3a7d8d58c055399a82b26d8ea961a49b1",
        "6f430554aee08d7070ed04bc3a6e65cdb159a4aa",
    ]
    assert rollback["state_reset_required"] is False
    assert len(rollback["config_actions"]) == 3


def test_rag31_final_manifest_contains_no_raw_payload_fields() -> None:
    prohibited_keys = {
        "answer",
        "canary",
        "chunk",
        "context",
        "pii",
        "prompt",
        "question",
        "raw_text",
        "secret",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            assert prohibited_keys.isdisjoint({str(key).lower() for key in value})
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(_load_manifest())
