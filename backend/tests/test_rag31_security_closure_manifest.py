import json
from pathlib import Path
from typing import Any

_MANIFEST_PATH = (
    Path(__file__).parents[2] / "docs" / "security" / "rag31_security_closure_manifest.json"
)


def _load_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_rag31_closure_manifest_preserves_remote_evidence() -> None:
    manifest = _load_manifest()

    assert manifest["schema_version"] == "rag31-security-closure-v1"
    assert manifest["main_sha"] == "96e5fc82ad2c253d000633ff9e9431e88998f74a"

    integration = manifest["audited_integration"]
    assert integration["pull_request"] == 141
    assert integration["state"] == "open"
    assert integration["draft"] is True
    assert integration["mergeable_state"] == "clean"
    assert integration["changed_file_count"] == 96
    assert {check["conclusion"] for check in integration["checks"]} == {"success"}

    sources = manifest["source_pull_requests"]
    assert [source["pull_request"] for source in sources] == list(range(133, 141))
    assert all(source["all_checks_success"] for source in sources)
    assert (
        next(source for source in sources if source["pull_request"] == 135)["base_sha"]
        == (next(source for source in sources if source["pull_request"] == 133)["head_sha"])
    )

    ancestry = manifest["ancestry_verification"]
    assert ancestry["stacked_relation"] == {
        "base_pull_request": 133,
        "head_pull_request": 135,
        "ahead_by": 1,
        "behind_by": 0,
        "base_is_merge_base": True,
    }
    source_ancestry = ancestry["source_heads_to_integration"]
    assert {entry["pull_request"] for entry in source_ancestry} == set(range(133, 141))
    assert all(entry["behind_by"] == 0 for entry in source_ancestry)
    assert all(entry["base_is_merge_base"] for entry in source_ancestry)

    file_sets = manifest["file_set_verification"]
    assert file_sets["source_union_count"] == 93
    assert file_sets["integration_count"] == 96
    assert file_sets["missing_from_integration"] == []
    assert set(file_sets["integration_only"]) == {
        "backend/alembic/versions/0024_security_integration_merge.py",
        "backend/tests/test_rag31_security_integration_gate.py",
        "docs/security/rag31_security_integration_gate.md",
    }


def test_rag31_closure_manifest_does_not_overclaim_completion() -> None:
    manifest = _load_manifest()
    criteria = {entry["id"]: entry for entry in manifest["acceptance_criteria"]}

    assert set(criteria) == set(range(1, 8))
    assert {
        criterion_id for criterion_id, entry in criteria.items() if entry["status"] == "partial"
    } == {
        2,
        5,
        6,
    }
    assert criteria[2]["follow_up"] == "RAG-72"
    assert criteria[5]["follow_up"] == "RAG-70"
    assert criteria[6]["follow_up"] == "RAG-71"

    decision = manifest["closure_decision"]
    assert decision["rag31_status"] == "not_closed"
    assert decision["canonical_review_pull_request"] == 141
    assert decision["do_not_merge_source_pull_requests_separately_if_141_is_selected"] is True
    assert decision["do_not_close_source_pull_requests_before_141_is_on_main"] is True
    assert set(decision["follow_up_issues"]) == {"RAG-70", "RAG-71", "RAG-72"}


def test_rag31_closure_manifest_contains_no_raw_payload_fields() -> None:
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
