from __future__ import annotations

import pytest

from app.rag.model_cascade_guard import (
    CASCADE_BUDGET_INPUT_INVALID_REASON_CODE,
    CASCADE_DAILY_BUDGET_REASON_CODE,
    CASCADE_REQUEST_INVALID_REASON_CODE,
    CASCADE_REQUEST_LIMIT_REASON_CODE,
    CASCADE_UNTRUSTED_ESCALATION_REASON_CODE,
    CASCADE_USER_BUDGET_REASON_CODE,
    authorize_model_tier,
)


@pytest.mark.parametrize("origin", ["user_input", "retrieved_context", "tool_result"])
def test_untrusted_input_cannot_force_plus(origin: str) -> None:
    decision = authorize_model_tier(
        requested_tier="plus",
        origin=origin,  # type: ignore[arg-type]
        policy_eligible=True,
        escalations_in_request=0,
        max_escalations_per_request=1,
        estimated_cost_units=10,
        remaining_user_cost_units=100,
        remaining_daily_cost_units=1000,
    )

    assert decision.allowed is False
    assert decision.selected_tier == "flash"
    assert CASCADE_UNTRUSTED_ESCALATION_REASON_CODE in decision.reason_codes


def test_trusted_policy_can_select_plus_within_all_budgets() -> None:
    decision = authorize_model_tier(
        requested_tier="plus",
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=0,
        max_escalations_per_request=1,
        estimated_cost_units=10,
        remaining_user_cost_units=100,
        remaining_daily_cost_units=1000,
    )

    assert decision.allowed is True
    assert decision.selected_tier == "plus"
    assert decision.reason_codes == ()


def test_plus_fails_closed_when_request_or_cost_budget_is_exhausted() -> None:
    decision = authorize_model_tier(
        requested_tier="plus",
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=1,
        max_escalations_per_request=1,
        estimated_cost_units=20,
        remaining_user_cost_units=10,
        remaining_daily_cost_units=15,
    )

    assert decision.allowed is False
    assert decision.selected_tier == "flash"
    assert set(decision.reason_codes) == {
        CASCADE_REQUEST_LIMIT_REASON_CODE,
        CASCADE_USER_BUDGET_REASON_CODE,
        CASCADE_DAILY_BUDGET_REASON_CODE,
    }


def test_flash_never_consumes_escalation_budget() -> None:
    decision = authorize_model_tier(
        requested_tier="flash",
        origin="user_input",
        policy_eligible=False,
        escalations_in_request=99,
        max_escalations_per_request=0,
        estimated_cost_units=999,
        remaining_user_cost_units=0,
        remaining_daily_cost_units=0,
    )

    assert decision.allowed is True
    assert decision.selected_tier == "flash"


def test_negative_budget_input_fails_closed() -> None:
    decision = authorize_model_tier(
        requested_tier="plus",
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=0,
        max_escalations_per_request=1,
        estimated_cost_units=-1,
        remaining_user_cost_units=100,
        remaining_daily_cost_units=1000,
    )

    assert decision.allowed is False
    assert decision.selected_tier == "flash"
    assert CASCADE_BUDGET_INPUT_INVALID_REASON_CODE in decision.reason_codes


def test_unknown_model_tier_fails_closed_at_runtime_boundary() -> None:
    decision = authorize_model_tier(
        requested_tier="ultra",  # type: ignore[arg-type]
        origin="trusted_policy",
        policy_eligible=True,
        escalations_in_request=0,
        max_escalations_per_request=1,
        estimated_cost_units=1,
        remaining_user_cost_units=100,
        remaining_daily_cost_units=1000,
    )

    assert decision.allowed is False
    assert decision.selected_tier == "flash"
    assert decision.reason_codes == (CASCADE_REQUEST_INVALID_REASON_CODE,)
