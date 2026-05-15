"""Canonical pydantic models that flow through the pipeline.

These types are the contract between stages: intake -> router -> specialist ->
action -> synthesis. Every stage takes a typed input and returns a typed output,
which is what makes the pipeline testable and traceable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Intent(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    GENERAL = "general"
    UNCLEAR = "unclear"


class Sensitivity(str, Enum):
    NONE = "none"
    LEGAL = "legal"
    SECURITY = "security"
    CHURN_RISK = "churn_risk"
    BILLING_DISPUTE = "billing_dispute"


class Outcome(str, Enum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    CLARIFYING = "clarifying"
    FAILED = "failed"


class PIIType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    ACCOUNT_ID = "account_id"
    IP_ADDRESS = "ip_address"


class PIITag(BaseModel):
    type: PIIType
    span: tuple[int, int]
    value_hash: str  # never store raw PII; keep a salted hash for dedup/audit


class CustomerContext(BaseModel):
    """Minimal customer profile passed alongside the request.

    In a real deploy this would be loaded from CRM at intake time. We keep it
    here so authorization checks (plan tier, account state) have a target.
    """

    customer_id: str
    email: str | None = None
    plan: str = "free"  # free, pro, enterprise
    account_status: str = "active"  # active, past_due, suspended, churned
    tenure_days: int = 0
    locale: str = "en-US"


class Message(BaseModel):
    role: Role
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)


class IntakeResult(BaseModel):
    """Output of the intake stage."""

    normalized_text: str
    redacted_text: str
    pii_tags: list[PIITag] = Field(default_factory=list)
    thread_summary: str | None = None  # populated if thread was summarized
    original_thread_message_count: int = 0
    language: str = "en"


class RoutingDecision(BaseModel):
    """Output of the router agent. Strictly structured, never prose."""

    primary_intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity = Sensitivity.NONE
    urgency: int = Field(default=2, ge=1, le=5)  # 1=lowest, 5=critical
    customer_frustration: bool = False
    explicit_human_request: bool = False
    rationale: str  # short, for trace inspection


class RetrievedPassage(BaseModel):
    doc_id: str
    title: str
    text: str
    source: str  # e.g. "policy/refunds.md"
    score: float
    chunk_index: int = 0


class ToolCallProposal(BaseModel):
    """A specialist's proposed action, before permission + verification."""

    tool_name: str
    arguments: dict[str, Any]
    rationale: str
    estimated_impact: str  # "low" | "medium" | "high" — drives gating


class ToolCallResult(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0
    idempotency_key: str | None = None


class SpecialistOutput(BaseModel):
    """What a specialist returns to the orchestrator."""

    intent: Intent
    draft_response: str
    citations: list[RetrievedPassage] = Field(default_factory=list)
    proposed_actions: list[ToolCallProposal] = Field(default_factory=list)
    executed_actions: list[ToolCallResult] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    needs_escalation: bool = False
    escalation_reason: str | None = None


class VerificationResult(BaseModel):
    """Output of the independent verification agent (ADR-002)."""

    approved: bool
    policy_violation: str | None = None
    mismatch_with_request: str | None = None
    rationale: str


class EscalationHandoff(BaseModel):
    """Structured packet handed to a human agent."""

    request_id: str
    customer_id: str
    summary: str
    attempted_steps: list[str]
    suggested_next_actions: list[str]
    sensitivity: Sensitivity
    priority: int = Field(ge=1, le=5)
    full_trace_id: str


class FinalResponse(BaseModel):
    """What goes back to the customer."""

    request_id: str
    response_text: str
    outcome: Outcome
    citations: list[RetrievedPassage] = Field(default_factory=list)
    confidence: float
    escalation: EscalationHandoff | None = None
    trace_id: str


class SupportRequest(BaseModel):
    """Inbound request to the platform."""

    request_id: str = Field(default_factory=_new_id)
    customer: CustomerContext
    message: str
    conversation_id: str | None = None  # for multi-turn threads
    received_at: datetime = Field(default_factory=_utcnow)
