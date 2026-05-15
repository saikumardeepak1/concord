"""Tool registry: declarative definitions of every action the platform can take.

Each tool defines:
- name (matches what specialists propose)
- a JSON schema for its arguments (used in the model tool-spec and validation)
- the rule-based permission predicate (first-gate check)
- the "impact" level used to decide whether the verification pass is required
- the actual handler (idempotent, side-effecting in the toy backend)

In a real deployment, handlers would call the company's billing system, CRM,
identity provider, etc. For Concord we implement them against an in-memory
mock backend so the entire stack runs end-to-end without external systems.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from concord.models import CustomerContext

# ----------------------------- mock backend -----------------------------------


class MockBackend:
    """Pretend CRM/billing/identity store.

    Holds per-customer balances, charges, and refunds. Stable across the
    process lifetime, reset between tests via `clear()`.
    """

    def __init__(self) -> None:
        self._refunds: dict[str, list[dict[str, Any]]] = {}
        self._daily_refund_total: dict[str, float] = {}  # cust_id -> USD this UTC day
        self._daily_key: str = ""
        self._reset_passwords: dict[str, str] = {}  # cust_id -> temp_token
        self._mfa_resets: dict[str, datetime] = {}
        self._plan_changes: dict[str, str] = {}
        self._tickets: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        self.__init__()

    def _rotate_day(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._daily_key:
            self._daily_key = today
            self._daily_refund_total.clear()

    def add_refund(self, customer_id: str, amount_usd: float, reason: str) -> dict[str, Any]:
        self._rotate_day()
        record = {
            "refund_id": f"rf_{secrets.token_hex(6)}",
            "customer_id": customer_id,
            "amount_usd": round(amount_usd, 2),
            "reason": reason,
            "issued_at": datetime.now(UTC).isoformat(),
        }
        self._refunds.setdefault(customer_id, []).append(record)
        self._daily_refund_total[customer_id] = (
            self._daily_refund_total.get(customer_id, 0.0) + amount_usd
        )
        return record

    def daily_refund_total(self, customer_id: str) -> float:
        self._rotate_day()
        return self._daily_refund_total.get(customer_id, 0.0)


_mock_backend = MockBackend()


def get_backend() -> MockBackend:
    return _mock_backend


# ----------------------------- tool definitions -------------------------------


PermissionFn = Callable[[CustomerContext, dict[str, Any]], "PermissionResult"]
HandlerFn = Callable[[CustomerContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class PermissionResult:
    allowed: bool
    reason: str = ""


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    intent_scope: tuple[str, ...]  # which specialists may call it
    impact: str  # "low" | "medium" | "high" — drives verification gating
    arguments_schema: dict[str, Any]
    permission: PermissionFn
    handler: HandlerFn
    idempotent: bool = True


# Permission predicates kept inline so they're auditable in one read.

def _allow_refund(ctx: CustomerContext, args: dict[str, Any]) -> PermissionResult:
    amount = float(args.get("amount_usd", 0))
    if amount <= 0:
        return PermissionResult(False, "amount must be positive")
    if amount > 200:
        return PermissionResult(False, "amount exceeds auto-approval cap ($200)")
    if ctx.account_status not in {"active", "past_due"}:
        return PermissionResult(False, f"account_status={ctx.account_status} not eligible")
    daily = _mock_backend.daily_refund_total(ctx.customer_id) + amount
    if daily > 500:
        return PermissionResult(False, "daily refund cap ($500) would be exceeded")
    return PermissionResult(True)


async def _do_refund(ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    return _mock_backend.add_refund(
        customer_id=ctx.customer_id,
        amount_usd=float(args["amount_usd"]),
        reason=args.get("reason", "customer support refund"),
    )


def _allow_password_reset(ctx: CustomerContext, args: dict[str, Any]) -> PermissionResult:
    if ctx.account_status == "suspended":
        return PermissionResult(False, "suspended accounts cannot self-reset")
    return PermissionResult(True)


async def _do_password_reset(ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    token = secrets.token_urlsafe(16)
    _mock_backend._reset_passwords[ctx.customer_id] = token
    return {
        "customer_id": ctx.customer_id,
        "reset_link": f"https://app.acme.example/reset?token={token}",
        "expires_in_minutes": 30,
    }


def _allow_mfa_reset(ctx: CustomerContext, args: dict[str, Any]) -> PermissionResult:
    # MFA reset is sensitive; require manager flag in args. The verification
    # pass will catch attempts to spoof this.
    if not args.get("manager_approved"):
        return PermissionResult(False, "manager approval required for MFA reset")
    return PermissionResult(True)


async def _do_mfa_reset(ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    _mock_backend._mfa_resets[ctx.customer_id] = datetime.now(UTC)
    return {"customer_id": ctx.customer_id, "mfa_reset": True}


def _allow_plan_change(ctx: CustomerContext, args: dict[str, Any]) -> PermissionResult:
    new_plan = args.get("new_plan")
    if new_plan not in {"free", "pro", "enterprise"}:
        return PermissionResult(False, f"unknown plan {new_plan!r}")
    if new_plan == "enterprise":
        return PermissionResult(False, "enterprise pricing is sales-negotiated; escalate")
    return PermissionResult(True)


async def _do_plan_change(ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    new_plan = args["new_plan"]
    _mock_backend._plan_changes[ctx.customer_id] = new_plan
    return {"customer_id": ctx.customer_id, "new_plan": new_plan}


def _allow_create_ticket(ctx: CustomerContext, args: dict[str, Any]) -> PermissionResult:
    return PermissionResult(True)


async def _do_create_ticket(ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    tid = f"tk_{secrets.token_hex(5)}"
    _mock_backend._tickets[tid] = {
        "ticket_id": tid,
        "customer_id": ctx.customer_id,
        "subject": args.get("subject", "Customer support inquiry"),
        "priority": args.get("priority", "normal"),
        "summary": args.get("summary", ""),
        "created_at": datetime.now(UTC).isoformat(),
    }
    return _mock_backend._tickets[tid]


_TOOLS: dict[str, Tool] = {
    "issue_refund": Tool(
        name="issue_refund",
        description=(
            "Issue a refund to the customer. Use only when the customer is "
            "within the documented refund window OR there is a confirmed "
            "billing error. Amount must be positive USD."
        ),
        intent_scope=("billing",),
        impact="high",
        arguments_schema={
            "type": "object",
            "properties": {
                "amount_usd": {"type": "number", "minimum": 0.01, "maximum": 1000},
                "reason": {"type": "string", "minLength": 3},
            },
            "required": ["amount_usd", "reason"],
            "additionalProperties": False,
        },
        permission=_allow_refund,
        handler=_do_refund,
    ),
    "send_password_reset": Tool(
        name="send_password_reset",
        description="Send a password reset link to the customer's verified email.",
        intent_scope=("account",),
        impact="medium",
        arguments_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        permission=_allow_password_reset,
        handler=_do_password_reset,
    ),
    "reset_mfa": Tool(
        name="reset_mfa",
        description=(
            "Reset MFA for the customer. SENSITIVE: only proceed when the "
            "customer has been verified out-of-band by a manager."
        ),
        intent_scope=("account",),
        impact="high",
        arguments_schema={
            "type": "object",
            "properties": {"manager_approved": {"type": "boolean"}},
            "required": ["manager_approved"],
            "additionalProperties": False,
        },
        permission=_allow_mfa_reset,
        handler=_do_mfa_reset,
    ),
    "change_plan": Tool(
        name="change_plan",
        description="Change the customer's subscription plan.",
        intent_scope=("billing", "account"),
        impact="medium",
        arguments_schema={
            "type": "object",
            "properties": {"new_plan": {"type": "string", "enum": ["free", "pro", "enterprise"]}},
            "required": ["new_plan"],
            "additionalProperties": False,
        },
        permission=_allow_plan_change,
        handler=_do_plan_change,
    ),
    "create_ticket": Tool(
        name="create_ticket",
        description="Create a follow-up support ticket for engineering or another team.",
        intent_scope=("billing", "technical", "account"),
        impact="low",
        arguments_schema={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "summary": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            },
            "required": ["subject", "summary"],
            "additionalProperties": False,
        },
        permission=_allow_create_ticket,
        handler=_do_create_ticket,
    ),
}


def get_tool(name: str) -> Tool | None:
    return _TOOLS.get(name)


def tools_for_intent(intent: str) -> list[Tool]:
    return [t for t in _TOOLS.values() if intent in t.intent_scope]


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())


async def _call_handler(tool: Tool, ctx: CustomerContext, args: dict[str, Any]) -> dict[str, Any]:
    """Execute the tool handler with a hard timeout."""
    return await asyncio.wait_for(tool.handler(ctx, args), timeout=15)
