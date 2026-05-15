"""Verifies the governed action layer without invoking the model."""

import pytest

from concord.actions.service import ActionService
from concord.actions.tools import get_backend
from concord.models import CustomerContext, ToolCallProposal


@pytest.fixture
def customer() -> CustomerContext:
    return CustomerContext(customer_id="cust-test-1", plan="pro", account_status="active")


@pytest.fixture(autouse=True)
def _reset_backend():
    get_backend().clear()
    yield
    get_backend().clear()


async def test_refund_under_cap_is_allowed(customer: CustomerContext) -> None:
    svc = ActionService()
    proposal = ToolCallProposal(
        tool_name="issue_refund",
        arguments={"amount_usd": 50.0, "reason": "duplicate charge"},
        rationale="customer reported duplicate charge",
        estimated_impact="medium",
    )
    result = await svc.execute(
        proposal=proposal,
        customer=customer,
        original_request="please refund the duplicate charge",
        request_id="req-1",
        trace_id="trace-1",
        policy_passages=[],
    )
    assert result.success
    assert result.result and result.result["amount_usd"] == 50.0


async def test_refund_over_cap_is_blocked(customer: CustomerContext) -> None:
    svc = ActionService()
    proposal = ToolCallProposal(
        tool_name="issue_refund",
        arguments={"amount_usd": 250.0, "reason": "customer request"},
        rationale="big refund",
        estimated_impact="high",
    )
    result = await svc.execute(
        proposal=proposal,
        customer=customer,
        original_request="please refund",
        request_id="req-2",
        trace_id="trace-2",
    )
    assert not result.success
    assert "exceeds auto-approval cap" in (result.error or "")


async def test_unknown_tool_is_rejected(customer: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="delete_everything",
            arguments={},
            rationale="bad",
            estimated_impact="high",
        ),
        customer=customer,
        original_request="please",
        request_id="req-3",
        trace_id="trace-3",
    )
    assert not result.success
    assert "unknown tool" in (result.error or "")


async def test_idempotent_replay(customer: CustomerContext) -> None:
    svc = ActionService()
    proposal = ToolCallProposal(
        tool_name="create_ticket",
        arguments={"subject": "follow up", "summary": "investigate billing"},
        rationale="needs eng follow-up",
        estimated_impact="low",
    )
    r1 = await svc.execute(
        proposal=proposal, customer=customer, original_request="please",
        request_id="req-rep", trace_id="trace-rep",
    )
    r2 = await svc.execute(
        proposal=proposal, customer=customer, original_request="please",
        request_id="req-rep", trace_id="trace-rep",
    )
    assert r1.success and r2.success
    # Replay returns the same backing result (same ticket_id).
    assert r1.result["ticket_id"] == r2.result["ticket_id"]


async def test_mfa_reset_requires_manager_flag(customer: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="reset_mfa",
            arguments={"manager_approved": False},
            rationale="user lost device",
            estimated_impact="high",
        ),
        customer=customer,
        original_request="please reset my mfa",
        request_id="req-mfa",
        trace_id="trace-mfa",
    )
    assert not result.success
    assert "manager approval" in (result.error or "")
