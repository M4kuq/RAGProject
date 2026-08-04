from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from app.core.config import Settings
from app.core.model_egress import ModelEgressBlockedError, ModelEgressGuard
from app.core.tool_execution_policy import ToolCapability, evaluate_tool_call
from app.evaluation.security_gate_phase2 import (
    evaluate_phase2_control,
    load_phase2_security_dataset,
)
from app.rag.model_cascade_guard import CascadeRequestOrigin, authorize_model_tier
from app.rag.retrieval import RetrievalFilters, _payload_matches_filters

_PII_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pii_egress_dev_v1.json"


def test_rag31_security_controls_compose_with_zero_bypass() -> None:
    settings = Settings(_env_file=None, app_env="test")
    metrics = _phase2_metrics()
    metrics.update(_egress_metrics())
    metrics.update(_cascade_metrics())
    metrics.update(_tool_metrics())
    metrics.update(_corpus_visibility_metrics())
    metrics["default_policy_drift_count"] = sum(
        (
            settings.external_model_egress_policy != "deny",
            not settings.rag_abuse_control_enabled,
            settings.rag_user_model_selection_enabled,
            not settings.local_model_endpoint_policy_enabled,
            settings.agent_tool_policy_mode != "enforce",
            settings.mcp_allow_write_tools,
        )
    )

    assert metrics == {
        "prompt_control_bypass_count": 0,
        "prompt_clean_utility_failure_count": 0,
        "prompt_detector_gap_count": 0,
        "pii_leakage_count": 0,
        "egress_policy_failure_count": 0,
        "egress_clean_utility_failure_count": 0,
        "budget_bypass_count": 0,
        "budget_clean_utility_failure_count": 0,
        "unauthorized_tool_allowance_count": 0,
        "tool_clean_utility_failure_count": 0,
        "quarantine_visibility_bypass_count": 0,
        "corpus_clean_visibility_failure_count": 0,
        "default_policy_drift_count": 0,
    }


def _phase2_metrics() -> dict[str, int]:
    outcomes = [evaluate_phase2_control(case) for case in load_phase2_security_dataset().cases]
    return {
        "prompt_control_bypass_count": sum(outcome.control_bypass for outcome in outcomes),
        "prompt_clean_utility_failure_count": sum(
            not outcome.clean_utility_pass for outcome in outcomes
        ),
        "prompt_detector_gap_count": sum(
            not outcome.expected_detector_covered for outcome in outcomes
        ),
    }


def _egress_metrics() -> dict[str, int]:
    fixture = cast(dict[str, object], json.loads(_PII_FIXTURE_PATH.read_text(encoding="utf-8")))
    cases = cast(list[dict[str, object]], fixture["cases"])
    guard = ModelEgressGuard(
        policy="mask",
        allowed_providers=("openai",),
        pii_masking_enabled=True,
    )
    leakage_count = 0
    policy_failure_count = 0
    clean_failure_count = 0
    for case in cases:
        inputs = tuple(cast(list[str], case["inputs"]))
        expected_action = cast(str, case["expected_action"])
        if expected_action == "blocked":
            try:
                guard.protect_texts(inputs, provider="openai", purpose="integration_gate")
            except ModelEgressBlockedError:
                continue
            policy_failure_count += 1
            continue

        protected = guard.protect_texts(
            inputs,
            provider="openai",
            purpose="integration_gate",
        )
        policy_failure_count += int(protected.audit.action != expected_action)
        for raw_value in cast(list[str], case["pii_values"]):
            leakage_count += sum(raw_value in text for text in protected.texts)
        if expected_action == "allowed":
            clean_failure_count += int(protected.texts != inputs)
    return {
        "pii_leakage_count": leakage_count,
        "egress_policy_failure_count": policy_failure_count,
        "egress_clean_utility_failure_count": clean_failure_count,
    }


def _cascade_metrics() -> dict[str, int]:
    untrusted_origins: tuple[CascadeRequestOrigin, ...] = (
        "user_input",
        "retrieved_context",
        "tool_result",
    )
    untrusted = [
        authorize_model_tier(
            requested_tier="plus",
            origin=origin,
            policy_eligible=True,
            escalations_in_request=0,
            max_escalations_per_request=1,
            estimated_cost_units=10,
            remaining_user_cost_units=100,
            remaining_daily_cost_units=1000,
        )
        for origin in untrusted_origins
    ]
    exhausted = authorize_model_tier(
        requested_tier="plus",
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=1,
        max_escalations_per_request=1,
        estimated_cost_units=20,
        remaining_user_cost_units=10,
        remaining_daily_cost_units=15,
    )
    clean = authorize_model_tier(
        requested_tier="plus",
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=0,
        max_escalations_per_request=1,
        estimated_cost_units=10,
        remaining_user_cost_units=100,
        remaining_daily_cost_units=1000,
    )
    return {
        "budget_bypass_count": sum(decision.allowed for decision in (*untrusted, exhausted)),
        "budget_clean_utility_failure_count": int(not clean.allowed),
    }


def _tool_metrics() -> dict[str, int]:
    read = ToolCapability("dense_search", "read", "search")
    write = ToolCapability("archive_document", "write", "passthrough")
    denied = (
        evaluate_tool_call(
            capabilities={"archive_document": write},
            tool_name="archive_document",
            arguments={},
            allowed_tools={"archive_document"},
            allow_write_tools=False,
            policy_mode="enforce",
        ),
        evaluate_tool_call(
            capabilities={"dense_search": read},
            tool_name="unregistered_tool",
            arguments={},
            allowed_tools={"dense_search"},
            allow_write_tools=False,
            policy_mode="enforce",
        ),
    )
    clean = evaluate_tool_call(
        capabilities={"dense_search": read},
        tool_name="dense_search",
        arguments={"query": "alpha"},
        allowed_tools={"dense_search"},
        allow_write_tools=False,
        policy_mode="enforce",
    )
    return {
        "unauthorized_tool_allowance_count": sum(decision.allowed for decision in denied),
        "tool_clean_utility_failure_count": int(not clean.allowed),
    }


def _corpus_visibility_metrics() -> dict[str, int]:
    filters = RetrievalFilters()
    base_payload: dict[str, object] = {
        "is_active": True,
        "document_version_status": "ready",
        "logical_document_status": "active",
        "modality": "text",
    }
    unsafe_payloads = [
        {**base_payload, "security_review_status": status}
        for status in ("pending", "quarantined", "unknown")
    ]
    approved = {**base_payload, "security_review_status": "approved"}
    return {
        "quarantine_visibility_bypass_count": sum(
            _payload_matches_filters(payload, filters) for payload in unsafe_payloads
        ),
        "corpus_clean_visibility_failure_count": sum(
            (
                not _payload_matches_filters(approved, filters),
                not _payload_matches_filters(base_payload, filters),
            )
        ),
    }
