"""Verifies the governed action layer without invoking the model.

The refund tests use cust-001 (Alice) from the mock directory because the
new permission predicate requires the transaction_id to reference a real
charge on the customer's account. The directory's transaction fixtures are
the source of truth.
"""

import pytest

from concord.actions.service import ActionService
from concord.actions.tools import get_backend
from concord.customers import get_directory
from concord.models import CustomerContext, ToolCallProposal


@pytest.fixture
def alice() -> CustomerContext:
    return get_directory().verify("cust-001").to_context()


@pytest.fixture
def bob_suspended() -> CustomerContext:
    return get_directory().verify("cust-002").to_context()


@pytest.fixture
def alice_duplicate_tx_id() -> str:
    # Alice has two $45 charges; either transaction_id is valid for a refund.
    txns = get_directory().find_transactions(
        "cust-001", within_days=10, approximate_amount_usd=45.00,
    )
    assert txns, "fixture changed: Alice should have $45 charges"
    return txns[0].transaction_id


@pytest.fixture(autouse=True)
def _reset_backend():
    get_backend().clear()
    yield
    get_backend().clear()


# ----------------------------------------------------- refund happy + sad paths


async def test_refund_with_real_transaction_succeeds(
    alice: CustomerContext, alice_duplicate_tx_id: str
) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={
                "transaction_id": alice_duplicate_tx_id,
                "amount_usd": 45.0,
                "reason": "duplicate charge",
            },
            rationale="duplicate confirmed via lookup_transaction",
            estimated_impact="medium",
        ),
        customer=alice,
        original_request="please refund the duplicate $45 charge",
        request_id="req-happy",
        trace_id="trace-happy",
    )
    assert result.success
    assert result.result["amount_usd"] == 45.0
    assert result.result["against_transaction_id"] == alice_duplicate_tx_id


async def test_refund_without_transaction_id_is_blocked(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={"amount_usd": 50.0, "reason": "duplicate charge"},
            rationale="naive proposal without lookup",
            estimated_impact="medium",
        ),
        customer=alice,
        original_request="refund please",
        request_id="req-no-tx",
        trace_id="trace-no-tx",
    )
    assert not result.success
    # Schema validation fires first (transaction_id is required); either
    # message is acceptable evidence the gate worked.
    err = (result.error or "").lower()
    assert "transaction_id" in err or "missing required" in err


async def test_refund_with_fake_transaction_id_is_blocked(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={
                "transaction_id": "tx_invented_by_model",
                "amount_usd": 45.0,
                "reason": "duplicate",
            },
            rationale="hallucinated transaction id",
            estimated_impact="medium",
        ),
        customer=alice,
        original_request="please refund",
        request_id="req-fake",
        trace_id="trace-fake",
    )
    assert not result.success
    assert "does not exist" in (result.error or "")


async def test_refund_amount_must_match_charge(
    alice: CustomerContext, alice_duplicate_tx_id: str
) -> None:
    # Asking for $80 against a $45 charge is rejected.
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={
                "transaction_id": alice_duplicate_tx_id,
                "amount_usd": 80.0,
                "reason": "claimed more than charge",
            },
            rationale="amount mismatch attempt",
            estimated_impact="medium",
        ),
        customer=alice,
        original_request="refund 80",
        request_id="req-mismatch",
        trace_id="trace-mismatch",
    )
    assert not result.success
    assert "does not match" in (result.error or "")


async def test_refund_over_cap_is_blocked(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={
                "transaction_id": "tx_anything",
                "amount_usd": 250.0,
                "reason": "customer request",
            },
            rationale="big refund",
            estimated_impact="high",
        ),
        customer=alice,
        original_request="please refund",
        request_id="req-cap",
        trace_id="trace-cap",
    )
    assert not result.success
    assert "exceeds auto-approval cap" in (result.error or "")


async def test_refund_on_suspended_account_blocked(
    bob_suspended: CustomerContext, alice_duplicate_tx_id: str
) -> None:
    svc = ActionService()
    # Even with a (technically valid-looking) transaction_id, suspended
    # account is rejected by the permission predicate before transaction
    # validation runs.
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="issue_refund",
            arguments={
                "transaction_id": alice_duplicate_tx_id,
                "amount_usd": 45.0,
                "reason": "refund",
            },
            rationale="suspended customer trying",
            estimated_impact="medium",
        ),
        customer=bob_suspended,
        original_request="refund please",
        request_id="req-suspended",
        trace_id="trace-suspended",
    )
    assert not result.success
    assert "suspended" in (result.error or "").lower() or "not eligible" in (result.error or "")


# ----------------------------------------------------- lookup tools


async def test_lookup_account_returns_real_state(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="lookup_account",
            arguments={},
            rationale="grounding",
            estimated_impact="low",
        ),
        customer=alice,
        original_request="what's my plan",
        request_id="req-look",
        trace_id="trace-look",
    )
    assert result.success
    assert result.result["plan"] == "pro"
    assert result.result["account_status"] == "active"


async def test_lookup_transaction_finds_alice_duplicates(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="lookup_transaction",
            arguments={"approximate_amount_usd": 45.0, "within_days": 10},
            rationale="searching for duplicate",
            estimated_impact="low",
        ),
        customer=alice,
        original_request="find my charges",
        request_id="req-tx",
        trace_id="trace-tx",
    )
    assert result.success
    assert result.result["matched_count"] >= 2


async def test_lookup_transaction_empty_when_no_match(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="lookup_transaction",
            arguments={"approximate_amount_usd": 9999.0, "within_days": 30},
            rationale="searching for fake charge",
            estimated_impact="low",
        ),
        customer=alice,
        original_request="find $9999 charge",
        request_id="req-empty",
        trace_id="trace-empty",
    )
    assert result.success
    assert result.result["matched_count"] == 0
    assert result.result["transactions"] == []


# ----------------------------------------------------- existing checks


async def test_unknown_tool_is_rejected(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="delete_everything",
            arguments={},
            rationale="bad",
            estimated_impact="high",
        ),
        customer=alice,
        original_request="please",
        request_id="req-unknown",
        trace_id="trace-unknown",
    )
    assert not result.success
    assert "unknown tool" in (result.error or "")


async def test_idempotent_replay(alice: CustomerContext) -> None:
    svc = ActionService()
    proposal = ToolCallProposal(
        tool_name="create_ticket",
        arguments={"subject": "follow up", "summary": "investigate billing"},
        rationale="needs eng follow-up",
        estimated_impact="low",
    )
    r1 = await svc.execute(
        proposal=proposal, customer=alice, original_request="please",
        request_id="req-rep", trace_id="trace-rep",
    )
    r2 = await svc.execute(
        proposal=proposal, customer=alice, original_request="please",
        request_id="req-rep", trace_id="trace-rep",
    )
    assert r1.success and r2.success
    assert r1.result["ticket_id"] == r2.result["ticket_id"]


async def test_mfa_reset_requires_manager_flag(alice: CustomerContext) -> None:
    svc = ActionService()
    result = await svc.execute(
        proposal=ToolCallProposal(
            tool_name="reset_mfa",
            arguments={"manager_approved": False},
            rationale="user lost device",
            estimated_impact="high",
        ),
        customer=alice,
        original_request="please reset my mfa",
        request_id="req-mfa",
        trace_id="trace-mfa",
    )
    assert not result.success
    assert "manager approval" in (result.error or "")
