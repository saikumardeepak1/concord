"""Escalation gate (ADR-005, ADR-010, ADR-011).

Implements the nine independent triggers documented in Section 4.4 of the
project brief. Hard triggers fire on their own. Soft triggers (confidence,
sentiment) only fire when two or more are present at once.

The gate is a pure function of its inputs: a `GateContext` snapshot built by
the orchestrator. It returns a structured `EscalationVerdict` that records
which triggers fired, so traces and the metrics dashboard can attribute every
escalation to a specific cause (this is what enables the escalation-precision
metric).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concord.config import get_settings
from concord.models import (
    CustomerContext,
    EscalationHandoff,
    RoutingDecision,
    Sensitivity,
    SpecialistOutput,
    ToolCallResult,
)
from concord.observability.metrics import get_metrics


# Sensitivity values that always force a hard escalation (trigger 3).
_ALWAYS_ESCALATE = {Sensitivity.LEGAL, Sensitivity.SECURITY}

# Minimum top retrieval score below which we treat the result set as "no
# relevant passages" (trigger 6). Calibrated against observed scores; chunks
# scoring above this consistently contained the right answer in the eval suite.
_RETRIEVAL_WEAK_TOP_SCORE = 0.30


@dataclass
class GateContext:
    """Snapshot of everything the gate needs to decide on a given turn.

    The orchestrator builds this once per turn and passes it in. Keeping the
    inputs explicit makes the gate testable in isolation.
    """

    routing: RoutingDecision
    specialist_output: SpecialistOutput | None
    attempts: int
    tool_results: list[ToolCallResult] = field(default_factory=list)
    top_retrieval_score: float | None = None
    cost_micro_usd_so_far: float = 0.0


@dataclass
class EscalationVerdict:
    """What the gate decided and why."""

    escalate: bool
    triggers_fired: list[int]
    reason: str

    @property
    def primary_trigger(self) -> int | None:
        return self.triggers_fired[0] if self.triggers_fired else None


# Mapping for trace + metric labels so escalation precision can be analyzed
# per trigger.
_TRIGGER_NAMES: dict[int, str] = {
    1: "low_confidence",
    2: "explicit_human_request",
    3: "sensitivity",
    4: "repeated_failed_resolution",
    5: "verifier_rejection",
    6: "knowledge_gap",
    7: "tool_failure",
    8: "cost_budget_exceeded",
    9: "frustrated_customer",
}


class EscalationGate:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._metrics = get_metrics()

    # ------------------------------------------------------------------ public

    def evaluate(self, ctx: GateContext) -> EscalationVerdict:
        """Evaluate all triggers. Hard triggers fire alone; soft combine."""
        hard: list[tuple[int, str]] = []
        soft: list[tuple[int, str]] = []

        # Trigger 2: explicit human request (hard)
        if ctx.routing.explicit_human_request:
            hard.append((2, "customer explicitly requested a human"))

        # Trigger 3: sensitivity (hard)
        if ctx.routing.sensitivity in _ALWAYS_ESCALATE:
            hard.append((3, f"sensitivity={ctx.routing.sensitivity.value}"))
        elif ctx.routing.sensitivity == Sensitivity.BILLING_DISPUTE:
            # Billing disputes escalate when the disputed amount looks large.
            # We do not have the amount on the routing decision, so we treat
            # any billing_dispute tag as a hard escalation here. The router
            # only emits this tag for chargeback / unauthorized-charge style
            # cases that should always reach a human.
            hard.append((3, "sensitivity=billing_dispute"))
        elif ctx.routing.sensitivity == Sensitivity.CHURN_RISK:
            hard.append((3, "sensitivity=churn_risk"))

        # Trigger 4: repeated failed resolution / max turns (hard)
        if ctx.attempts >= self._settings.max_turns:
            hard.append((4, f"attempts={ctx.attempts} reached max_turns"))

        # Trigger 5: verifier rejection (hard).
        # The orchestrator marks specialist_output.needs_escalation when a tool
        # call comes back denied by the verifier (or by permission), so we read
        # that signal here. We disambiguate verifier denial from other escalate
        # reasons by checking for the denial fingerprint in the reason text.
        sp = ctx.specialist_output
        if sp is not None and sp.needs_escalation:
            reason = sp.escalation_reason or "specialist requested escalation"
            if "verification" in reason.lower() or "denied" in reason.lower():
                hard.append((5, reason))
            else:
                hard.append((5, reason))  # fall through; still a hard signal

        # Trigger 6: knowledge gap (hard).
        # Fires when retrieval brought back nothing usable (no passages OR top
        # score below the weak threshold). Without grounded passages, the
        # specialist is likely to hallucinate; better to escalate.
        if sp is not None and (
            not sp.citations
            or (ctx.top_retrieval_score is not None
                and ctx.top_retrieval_score < _RETRIEVAL_WEAK_TOP_SCORE)
        ):
            hard.append((6, "retrieval returned no usable passages"))

        # Trigger 7: critical tool failure (hard).
        # Any tool that failed for non-denial reasons (timeout, handler crash,
        # backend unreachable) is treated as an outage signal. Denials are
        # covered by trigger 5.
        for result in ctx.tool_results:
            if result.success:
                continue
            err = (result.error or "").lower()
            if "denied" in err or "permission" in err or "verification" in err:
                continue  # trigger 5 territory
            hard.append((7, f"tool {result.tool_name} failed: {result.error}"))
            break  # one is enough

        # Trigger 8: cost budget exceeded (hard).
        # The per-request cost budget is a safety valve against pathological
        # loops. We use the request token budget * a conservative price
        # ceiling as the cap.
        cost_cap_micro = self._settings.max_tokens_per_request * 75.0  # opus output rate
        if ctx.cost_micro_usd_so_far > cost_cap_micro:
            hard.append((8, f"cost {ctx.cost_micro_usd_so_far:.0f} micro-USD over cap"))

        # Trigger 1: low confidence (soft).
        if sp is not None and sp.confidence < self._settings.confidence_escalate:
            soft.append((1, f"specialist confidence {sp.confidence:.2f} below threshold"))
        elif sp is None and ctx.routing.confidence < self._settings.confidence_escalate:
            # Pre-specialist phase: use router confidence as the proxy.
            soft.append((1, f"router confidence {ctx.routing.confidence:.2f} below threshold"))

        # Trigger 9: frustrated customer (soft).
        if ctx.routing.customer_frustration:
            soft.append((9, "customer_frustration detected"))

        # Combine.
        fired: list[int] = []
        reasons: list[str] = []
        if hard:
            fired.extend(t for t, _ in hard)
            reasons.extend(r for _, r in hard)
        if len(soft) >= 2:
            fired.extend(t for t, _ in soft)
            reasons.extend(r for _, r in soft)
        elif soft and not hard:
            # A single soft signal is not enough to escalate on its own.
            pass

        escalate = bool(fired)
        if escalate:
            primary = fired[0]
            self._metrics.escalations_total.labels(
                reason=_TRIGGER_NAMES.get(primary, "other")
            ).inc()
        return EscalationVerdict(
            escalate=escalate,
            triggers_fired=fired,
            reason="; ".join(reasons) if reasons else "",
        )

    # ----------------------------------------------------- back-compat shim

    def should_escalate(
        self,
        *,
        routing: RoutingDecision,
        specialist_output: SpecialistOutput | None,
        attempts: int,
    ) -> tuple[bool, str]:
        """Legacy signature kept so existing call sites (and tests) keep
        working. New call sites should build a GateContext and call evaluate().
        """
        ctx = GateContext(
            routing=routing,
            specialist_output=specialist_output,
            attempts=attempts,
            tool_results=specialist_output.executed_actions if specialist_output else [],
            top_retrieval_score=(
                specialist_output.citations[0].score
                if specialist_output and specialist_output.citations
                else None
            ),
        )
        v = self.evaluate(ctx)
        return v.escalate, v.reason

    # ------------------------------------------------------------ handoff

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
        triggers_fired: list[int] | None = None,
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

        trigger_labels = (
            [_TRIGGER_NAMES.get(t, str(t)) for t in (triggers_fired or [])]
        )
        summary_parts = [
            f"Customer (plan={customer.plan}, status={customer.account_status}, tenure={customer.tenure_days}d).",
            f"Routed intent={routing.primary_intent.value} (conf={routing.confidence:.2f}).",
            f"Sensitivity={routing.sensitivity.value}, urgency={routing.urgency}.",
            (
                f"Escalation triggers: {', '.join(trigger_labels)}."
                if trigger_labels
                else f"Escalation reason: {reason}."
            ),
        ]

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
