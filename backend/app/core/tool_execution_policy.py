from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

TOOL_EXECUTION_POLICY_SCHEMA_VERSION = "security.tool_execution.v1"

MCP_READ_TOOL_NAMES = (
    "get_document_status",
    "get_evaluation_result",
    "get_job_status",
    "list_documents",
    "list_evaluation_runs",
    "rag_ask",
    "rag_ask_agentic",
    "rag_ask_auto",
    "rag_ask_hybrid",
    "rag_ask_langchain_agentic",
    "rag_ask_langgraph_agentic",
    "rag_compare_strategies",
    "rag_get_evaluation_summary",
    "rag_get_retrieval_trace",
    "rag_search",
    "rag_search_agentic",
    "rag_search_hybrid",
)

ToolEffect = Literal["read", "write"]
ToolArgumentProfile = Literal["passthrough", "search", "trace", "finalize"]
ToolPolicyMode = Literal["enforce", "legacy"]
ToolAuditDecision = Literal["allowed", "denied", "completed", "failed"]

_SAFE_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_SURFACES = {
    "langchain_agentic",
    "langgraph_agentic",
    "llm_tool_orchestrator",
    "mcp",
}
_AUDITABLE_TOOL_NAMES = frozenset(
    (
        *MCP_READ_TOOL_NAMES,
        "dense_search",
        "finalize_answer",
        "hybrid_search",
        "inspect_retrieval_trace",
        "sparse_search",
    )
)
_LOGGER = logging.getLogger("app.security.tool_execution")


@dataclass(frozen=True)
class ToolCapability:
    name: str
    effect: ToolEffect
    argument_profile: ToolArgumentProfile


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    stable_tool_name: str
    effect: ToolEffect | None
    reason_code: str
    arguments: dict[str, object]


def evaluate_tool_call(
    *,
    capabilities: dict[str, ToolCapability],
    tool_name: str,
    arguments: object,
    allowed_tools: Collection[str],
    allow_write_tools: bool,
    policy_mode: ToolPolicyMode,
    max_query_chars: int = 1000,
    tool_call_id_prefixes: tuple[str, ...] = ("tc_", "lc_", "lg_"),
) -> ToolPolicyDecision:
    """Authorize and sanitize a tool call without retaining raw values."""

    capability = capabilities.get(tool_name)
    if capability is None:
        return _denied("unknown", None, "tool_not_registered")
    if capability.effect == "write" and not allow_write_tools:
        return _denied(capability.name, capability.effect, "write_tool_denied")
    if policy_mode == "enforce" and tool_name not in set(allowed_tools):
        return _denied(capability.name, capability.effect, "tool_not_allowed")
    if not isinstance(arguments, dict):
        return _denied(capability.name, capability.effect, "tool_arguments_not_object")
    if policy_mode == "legacy" or capability.argument_profile == "passthrough":
        return _allowed(capability, dict(arguments))

    sanitized = _validated_arguments(
        capability.argument_profile,
        arguments,
        max_query_chars=max_query_chars,
        tool_call_id_prefixes=tool_call_id_prefixes,
    )
    if sanitized is None:
        return _denied(capability.name, capability.effect, "tool_arguments_invalid")
    return _allowed(capability, sanitized)


def emit_tool_execution_audit(
    *,
    enabled: bool,
    surface: str,
    decision: ToolAuditDecision,
    stable_tool_name: str,
    effect: ToolEffect | None,
    reason_code: str,
    policy_mode: ToolPolicyMode,
    argument_count: int,
    call_index: int | None = None,
    duration_ms: float | None = None,
) -> dict[str, object]:
    """Emit a raw-free, bounded execution event and return it for deterministic tests."""

    event: dict[str, object] = {
        "schema_version": TOOL_EXECUTION_POLICY_SCHEMA_VERSION,
        "surface": surface if surface in _SAFE_SURFACES else "unknown",
        "decision": decision,
        "tool_name": (
            stable_tool_name
            if stable_tool_name in _AUDITABLE_TOOL_NAMES or stable_tool_name == "unknown"
            else "unknown"
        ),
        "effect": effect or "unknown",
        "reason_code": (
            reason_code if _SAFE_REASON_CODE.fullmatch(reason_code) else "invalid_reason_code"
        ),
        "policy_mode": policy_mode,
        "argument_count": max(0, min(int(argument_count), 64)),
    }
    if call_index is not None:
        event["call_index"] = max(0, min(int(call_index), 1000))
    if duration_ms is not None and math.isfinite(duration_ms):
        event["duration_ms"] = round(max(0.0, min(float(duration_ms), 86_400_000.0)), 3)
    if enabled:
        _LOGGER.info(
            "tool_execution_audit %s",
            json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
    return event


def _validated_arguments(
    profile: ToolArgumentProfile,
    arguments: dict[object, object],
    *,
    max_query_chars: int,
    tool_call_id_prefixes: tuple[str, ...],
) -> dict[str, object] | None:
    if not all(isinstance(key, str) for key in arguments):
        return None
    keys = set(arguments)
    if profile == "search":
        if keys != {"query"}:
            return None
        query = arguments.get("query")
        if not isinstance(query, str):
            return None
        normalized = " ".join(query.replace("\x00", " ").split())
        if not normalized:
            return None
        return {"query": normalized[: max(1, min(max_query_chars, 1000))]}
    if profile == "trace":
        if not keys.issubset({"retrieval_run_id"}):
            return None
        if "retrieval_run_id" not in arguments:
            return {}
        run_id = arguments.get("retrieval_run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            return None
        return {"retrieval_run_id": run_id}
    if profile == "finalize":
        if not keys.issubset({"answer_intent", "selected_tool_call_ids"}):
            return None
        answer_intent = arguments.get("answer_intent")
        if answer_intent is not None and answer_intent != "final_answer":
            return None
        selected = arguments.get("selected_tool_call_ids")
        if selected is None:
            return {"answer_intent": "final_answer"} if answer_intent is not None else {}
        if not isinstance(selected, list) or len(selected) > 20:
            return None
        call_id_pattern = re.compile(
            rf"(?:{'|'.join(re.escape(prefix) for prefix in tool_call_id_prefixes)})[1-9][0-9]*"
        )
        if not all(
            isinstance(item, str)
            and len(item) <= 40
            and call_id_pattern.fullmatch(item) is not None
            for item in selected
        ):
            return None
        sanitized: dict[str, object] = {"selected_tool_call_ids": list(selected)}
        if answer_intent is not None:
            sanitized["answer_intent"] = "final_answer"
        return sanitized
    return None


def _allowed(capability: ToolCapability, arguments: dict[str, object]) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        allowed=True,
        stable_tool_name=capability.name,
        effect=capability.effect,
        reason_code="tool_allowed",
        arguments=arguments,
    )


def _denied(
    stable_tool_name: str,
    effect: ToolEffect | None,
    reason_code: str,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        allowed=False,
        stable_tool_name=stable_tool_name,
        effect=effect,
        reason_code=reason_code,
        arguments={},
    )
