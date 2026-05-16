"""Tests for the mock customer directory and identity-verification gate."""

import pytest

from concord.customers import CustomerNotFoundError, get_directory


def test_known_customers_present() -> None:
    d = get_directory()
    ids = d.known_customer_ids()
    assert {"cust-001", "cust-002", "cust-003", "cust-004", "cust-005", "cust-006"}.issubset(ids)


def test_verify_returns_record_for_known_id() -> None:
    rec = get_directory().verify("cust-001")
    assert rec.customer_id == "cust-001"
    assert rec.plan == "pro"
    assert rec.account_status == "active"


def test_verify_raises_for_unknown_id() -> None:
    with pytest.raises(CustomerNotFoundError):
        get_directory().verify("cust-does-not-exist")


def test_unknown_customer_error_mentions_demo_ids() -> None:
    try:
        get_directory().verify("cust-9999")
    except CustomerNotFoundError as exc:
        assert "cust-001" in str(exc) or "DEMO_SCENARIOS" in str(exc)


def test_alice_has_duplicate_charge() -> None:
    # The canonical happy-path setup: Alice has two $45 charges on the same
    # day that should be findable by lookup_transaction.
    txns = get_directory().find_transactions(
        "cust-001", within_days=10, approximate_amount_usd=45.00,
    )
    assert len(txns) >= 2
    assert all(abs(t.amount_usd - 45.00) < 0.01 for t in txns)


def test_eve_has_no_charges() -> None:
    # Free-tier customer with nothing to refund.
    txns = get_directory().find_transactions("cust-005")
    assert txns == []


def test_lookup_unknown_customer_returns_empty() -> None:
    # Lookup must not throw for an unverified ID; it returns empty so the
    # specialist treats it as "no transactions found" rather than crashing.
    assert get_directory().find_transactions("cust-9999") == []


def test_amount_filter_narrows_results() -> None:
    # Searching for a $999 charge on Alice should find none even though she
    # has $45 charges in the window.
    txns = get_directory().find_transactions(
        "cust-001", within_days=30, approximate_amount_usd=999.00,
    )
    assert txns == []


def test_to_context_strips_internal_fields() -> None:
    # The CustomerContext passed to the orchestrator should only contain the
    # subset of fields the rest of the pipeline expects.
    rec = get_directory().verify("cust-001")
    ctx = rec.to_context()
    assert ctx.customer_id == "cust-001"
    assert ctx.plan == "pro"
    assert ctx.account_status == "active"
