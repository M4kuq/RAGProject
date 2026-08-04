from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CascadeRequestOrigin = Literal["trusted_policy", "user_input", "retrieved_context", "tool_result"]
ModelTier = Literal["flash", "plus"]

CASCADE_UNTRUSTED_ESCALATION_REASON_CODE = "cascade_untrusted_escalation_request"
CASCADE_NOT_ELIGIBLE_REASON_CODE = "cascade_plus_not_eligible"
CASCADE_REQUEST_LIMIT_REASON_CODE = "cascade_request_escalation_limit"
CASCADE_USER_BUDGET_REASON_CODE = "cascade_user_budget_exhausted"
CASCADE_DAILY_BUDGET_REASON_CODE = "cascade_daily_budget_exhausted"
CASCADE_BUDGET_INPUT_INVALID_REASON_CODE = "cascade_budget_input_invalid"
CASCADE_REQUEST_INVALID_REASON_CODE = "cascade_request_invalid"


@dataclass(frozen=True)
class CascadeSecurityDecision:
    allowed: bool
    selected_tier: ModelTier
    reason_codes: tuple[str, ...]


def authorize_model_tier(
    *,
    requested_tier: ModelTier,
    origin: CascadeRequestOrigin,
    policy_eligible: bool,
    escalations_in_request: int,
    max_escalations_per_request: int,
    estimated_cost_units: int,
    remaining_user_cost_units: int,
    remaining_daily_cost_units: int,
) -> CascadeSecurityDecision:
    """Authorize a future Flash-to-Plus escalation without consulting model-controlled text."""
    if requested_tier not in {"flash", "plus"}:
        return CascadeSecurityDecision(False, "flash", (CASCADE_REQUEST_INVALID_REASON_CODE,))
    if requested_tier == "flash":
        return CascadeSecurityDecision(True, "flash", ())

    reason_codes: list[str] = []
    if origin != "trusted_policy":
        reason_codes.append(CASCADE_UNTRUSTED_ESCALATION_REASON_CODE)
    if not policy_eligible:
        reason_codes.append(CASCADE_NOT_ELIGIBLE_REASON_CODE)
    if (
        escalations_in_request < 0
        or max_escalations_per_request < 0
        or estimated_cost_units < 0
        or remaining_user_cost_units < 0
        or remaining_daily_cost_units < 0
    ):
        reason_codes.append(CASCADE_BUDGET_INPUT_INVALID_REASON_CODE)
    if max_escalations_per_request < 1 or escalations_in_request >= max_escalations_per_request:
        reason_codes.append(CASCADE_REQUEST_LIMIT_REASON_CODE)
    if estimated_cost_units > remaining_user_cost_units:
        reason_codes.append(CASCADE_USER_BUDGET_REASON_CODE)
    if estimated_cost_units > remaining_daily_cost_units:
        reason_codes.append(CASCADE_DAILY_BUDGET_REASON_CODE)
    if reason_codes:
        return CascadeSecurityDecision(False, "flash", tuple(reason_codes))
    return CascadeSecurityDecision(True, "plus", ())
