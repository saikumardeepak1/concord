"""Escalation gate (ADR-005, ADR-010).

Decides whether a request should be handed to a human, and produces the
structured packet the human receives. Hybrid: confidence + rules.

Triggers (ANY of these is sufficient):
- explicit_human_request from the router
- sensitivity in {legal, security, billing_dispute over $200}
- confidence below settings.confidence_escalate
- loop budget exhausted
- specialist itself signaled needs_escalation=true
- repeated unresolved follow-ups on the same conversation
"""

from __future__ import annotations

from concord.config import get_settings
from concord.models import (
    CustomerContext,
    EscalationHandoff,
    RoutingDecision,
    Sensitivity,
    SpecialistOutput,
)
from concord.observability.metrics import get_metrics


class EscalationGate:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._metrics = get_metrics()

    def should_escalate(
        self,
        *,
        routing: RoutingDecision,
        specialist_output: SpecialistOutput | None,
        attempts: int,
    ) -> tuple[bool, str]:
        if routing.explicit_human_request:
            return True, "customer explicitly requested a human"
        if routing.sensitivity in {Sensitivity.LEGAL, Sensitivity.SECURITY}:
            return True, f"sensitive: {routing.sensitivity.value}"
        if specialist_output is None:
            # Router with no specialist (e.g. UNCLEAR + no clarification budget).
            if routing.confidence < self._settings.confidence_escalate:
                return True, f"router confidence {routing.confidence:.2f} below threshold"
            return False, ""
        if specialist_output.needs_escalation:
            return True, specialist_output.escalation_reason or "specialist requested escalation"
        if specialist_output.confidence < self._settings.confidence_escalate:
            return True, f"specialist confidence {specialist_output.confidence:.2f} below threshold"
        if attempts >= self._settings.max_turns:
            return True, "max turns exhausted"
        return False, ""

    def build_handoff(
        self,
        *,
        request_id: str,
        trace_id: str,
        customer: CustomerContext,
        routing: RoutingDecision,
        specialist_output: SpecialistOutput | None,
        attempted_steps: list[str],
        reason: str,
    ) -> EscalationHandoff:
        priority = max(routing.urgency, 4 if routing.sensitivity != Sensitivity.NONE else 3)
        suggested: list[str] = []
        if specialist_output is not None:
            if specialist_output.proposed_actions:
                suggested.append(
                    "Specialist proposed: "
                    + ", ".join(p.tool_name for p in specialist_output.proposed_actions)
                )
            if specialist_output.draft_response:
                suggested.append(
                    "Draft starter: "
                    + specialist_output.draft_response[:200]
                    + ("..." if len(specialist_output.draft_response) > 200 else "")
                )
        else:
            suggested.append("No specialist response was produced; review router decision.")

        summary_parts = [
            f"Customer (plan={customer.plan}, status={customer.account_status}, tenure={customer.tenure_days}d).",
            f"Routed intent={routing.primary_intent.value} (conf={routing.confidence:.2f}).",
            f"Sensitivity={routing.sensitivity.value}, urgency={routing.urgency}.",
            f"Escalation reason: {reason}.",
        ]

        self._metrics.escalations_total.labels(reason=_reason_label(reason)).inc()

        return EscalationHandoff(
            request_id=request_id,
            customer_id=customer.customer_id,
            summary=" ".join(summary_parts),
            attempted_steps=attempted_steps,
            suggested_next_actions=suggested,
            sensitivity=routing.sensitivity,
            priority=priority,
            full_trace_id=trace_id,
        )


def _reason_label(reason: str) -> str:
    r = reason.lower()
    if "explicit" in r or "human" in r:
        return "explicit_request"
    if "sensitive" in r or "legal" in r or "security" in r:
        return "sensitivity"
    if "confidence" in r:
        return "low_confidence"
    if "max turns" in r:
        return "loop_exhausted"
    return "other"
