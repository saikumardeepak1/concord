"""Mock customer directory.

Stands in for the company's real CRM / billing / identity systems during
demos and tests. Six fixture customers, each set up to exercise a different
failure path so reviewers can verify the safety mechanisms work end to end.

In a real deployment this module is replaced by adapters that call the
actual CRM (Salesforce, Hubspot), billing system (Stripe, Chargebee), and
identity provider (Auth0, Okta). The interface stays the same; only the
implementation behind `MockCustomerDirectory` changes.

Test customer matrix (see docs/DEMO_SCENARIOS.md for the full failure-mode
playbook):

    cust-001  Alice    pro          active     normal happy-path customer
    cust-002  Bob      pro          suspended  account-issue case
    cust-003  Carol    pro          past_due   failed-payment case
    cust-004  Dave     enterprise   active     big-refund-needs-escalation
    cust-005  Eve      free         active     no charges, no refund possible
    cust-006  Frank    pro          churned    outside the 14-day window

Any customer_id not in this list is rejected at the API boundary with a
CustomerNotFoundError. This is the identity-verification gate the demo
previously lacked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from concord.models import CustomerContext


class CustomerNotFoundError(Exception):
    """Raised when a request references a customer_id that is not in the
    directory. The API layer catches this and returns a 401-style response;
    no agent ever sees the request.
    """


@dataclass(slots=True)
class Transaction:
    transaction_id: str
    customer_id: str
    occurred_at: datetime
    amount_usd: float
    description: str
    kind: str  # "charge", "refund", "credit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "occurred_at": self.occurred_at.isoformat(),
            "amount_usd": self.amount_usd,
            "description": self.description,
            "kind": self.kind,
        }


@dataclass(slots=True)
class CustomerRecord:
    customer_id: str
    name: str
    email: str
    plan: str
    account_status: str
    tenure_days: int
    transactions: list[Transaction] = field(default_factory=list)

    def to_context(self) -> CustomerContext:
        return CustomerContext(
            customer_id=self.customer_id,
            email=self.email,
            plan=self.plan,
            account_status=self.account_status,
            tenure_days=self.tenure_days,
        )


# --------------------------------------------------------- fixtures


def _now() -> datetime:
    return datetime.now(UTC)


def _build_fixtures() -> dict[str, CustomerRecord]:
    today = _now()

    # cust-001 Alice: standard active Pro. Has a recent duplicate charge that
    # SHOULD be refundable. The canonical "happy path" customer.
    alice = CustomerRecord(
        customer_id="cust-001",
        name="Alice Hernandez",
        email="alice@example.com",
        plan="pro",
        account_status="active",
        tenure_days=412,
        transactions=[
            Transaction("tx_a001", "cust-001", today - timedelta(days=3), 45.00,
                        "Pro subscription monthly", "charge"),
            Transaction("tx_a002", "cust-001", today - timedelta(days=3), 45.00,
                        "Pro subscription monthly (duplicate)", "charge"),
            Transaction("tx_a003", "cust-001", today - timedelta(days=33), 45.00,
                        "Pro subscription monthly", "charge"),
        ],
    )

    # cust-002 Bob: suspended. Permission predicates should reject most
    # actions on his account.
    bob = CustomerRecord(
        customer_id="cust-002",
        name="Bob Singh",
        email="bob@example.com",
        plan="pro",
        account_status="suspended",
        tenure_days=180,
        transactions=[
            Transaction("tx_b001", "cust-002", today - timedelta(days=22), 45.00,
                        "Pro subscription monthly", "charge"),
        ],
    )

    # cust-003 Carol: past_due. Payment failed; refunds may still apply but
    # account is in a special state.
    carol = CustomerRecord(
        customer_id="cust-003",
        name="Carol Martinez",
        email="carol@example.com",
        plan="pro",
        account_status="past_due",
        tenure_days=89,
        transactions=[
            Transaction("tx_c001", "cust-003", today - timedelta(days=2), 45.00,
                        "Pro subscription monthly - PAYMENT FAILED", "charge"),
        ],
    )

    # cust-004 Dave: enterprise. Any meaningful refund will exceed the $200
    # auto-approval cap and force escalation.
    dave = CustomerRecord(
        customer_id="cust-004",
        name="Dave Okafor",
        email="dave@bigco.example.com",
        plan="enterprise",
        account_status="active",
        tenure_days=820,
        transactions=[
            Transaction("tx_d001", "cust-004", today - timedelta(days=60), 24000.00,
                        "Enterprise annual subscription", "charge"),
            Transaction("tx_d002", "cust-004", today - timedelta(days=10), 1200.00,
                        "Add-on: priority support", "charge"),
        ],
    )

    # cust-005 Eve: free tier. Has no charges, so any refund request is
    # categorically impossible. Tests the "no transaction found" path.
    eve = CustomerRecord(
        customer_id="cust-005",
        name="Eve Tanaka",
        email="eve@example.com",
        plan="free",
        account_status="active",
        tenure_days=21,
        transactions=[],
    )

    # cust-006 Frank: churned. Last charge was 60 days ago; outside the
    # 14-day refund window even though policy might otherwise allow it.
    frank = CustomerRecord(
        customer_id="cust-006",
        name="Frank Lambert",
        email="frank@example.com",
        plan="pro",
        account_status="churned",
        tenure_days=730,
        transactions=[
            Transaction("tx_f001", "cust-006", today - timedelta(days=60), 45.00,
                        "Pro subscription monthly (final)", "charge"),
        ],
    )

    return {c.customer_id: c for c in [alice, bob, carol, dave, eve, frank]}


# --------------------------------------------------------- directory


class MockCustomerDirectory:
    """In-memory directory. Thread-safe enough for this demo (read-mostly)."""

    def __init__(self) -> None:
        self._records: dict[str, CustomerRecord] = _build_fixtures()

    def known_customer_ids(self) -> list[str]:
        return list(self._records.keys())

    def verify(self, customer_id: str) -> CustomerRecord:
        """Identity-verification gate. Raises if the customer is unknown.

        In production, replace this with a JWT-validation step against the
        identity provider plus a CRM lookup. The contract stays the same:
        given a customer_id from a trusted caller, return the verified record
        or raise.
        """
        rec = self._records.get(customer_id)
        if rec is None:
            raise CustomerNotFoundError(
                f"customer_id {customer_id!r} not found. "
                "Use one of the demo IDs (cust-001 through cust-006) or "
                "see docs/DEMO_SCENARIOS.md for the test matrix."
            )
        return rec

    def find_transactions(
        self,
        customer_id: str,
        *,
        within_days: int | None = None,
        approximate_amount_usd: float | None = None,
        amount_tolerance_usd: float = 1.00,
    ) -> list[Transaction]:
        """Look up real transactions for a verified customer.

        This is what specialists call BEFORE proposing a refund. Returns
        only transactions the customer actually has. If the customer claims
        a charge that does not exist, the result is an empty list and the
        specialist should ask a clarifying question rather than guess.
        """
        rec = self._records.get(customer_id)
        if rec is None:
            return []
        results = list(rec.transactions)
        if within_days is not None:
            cutoff = _now() - timedelta(days=within_days)
            results = [t for t in results if t.occurred_at >= cutoff]
        if approximate_amount_usd is not None:
            results = [
                t for t in results
                if abs(t.amount_usd - approximate_amount_usd) <= amount_tolerance_usd
            ]
        return results

    def reset(self) -> None:
        """Reset the directory between tests."""
        self._records = _build_fixtures()


_singleton: MockCustomerDirectory | None = None


def get_directory() -> MockCustomerDirectory:
    global _singleton
    if _singleton is None:
        _singleton = MockCustomerDirectory()
    return _singleton
