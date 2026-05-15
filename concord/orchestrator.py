"""Concord orchestrator.

This is the request-level state machine. It wires the typed pipeline:

    intake -> router -> [escalation gate] -> specialist -> [actions] ->
    [escalation gate] -> synthesizer -> final response (+ trace persisted)

The orchestrator is intentionally small. All "thinking" lives in the agents;
the orchestrator's job is sequencing, budget enforcement, and trace stitching.
"""

from __future__ import annotations

from typing import Any

import structlog

from concord.actions.service import get_action_service
from concord.config import get_settings
from concord.escalation.gate import EscalationGate
from concord.intake.pipeline import GibberishInputError, IntakeStage
from concord.models import (
    EscalationHandoff,
    FinalResponse,
    Intent,
    Outcome,
    RoutingDecision,
    Sensitivity,
    SupportRequest,
    ToolCallResult,
)
from concord.observability.metrics import get_metrics
from concord.observability.tracing import get_tracer, span
from concord.router.router import RouterAgent
from concord.specialists.account import AccountSpecialist
from concord.specialists.base import SpecialistAgent
from concord.specialists.billing import BillingSpecialist
from concord.specialists.technical import TechnicalSpecialist
from concord.state import ConversationStore, TraceStore
from concord.synthesis.responder import ResponseSynthesizer

_log = structlog.get_logger("concord.orchestrator")


class Concord:
    def __init__(self) -> None:
        self._intake = IntakeStage()
        self._router = RouterAgent()
        self._specialists: dict[Intent, SpecialistAgent] = {
            Intent.BILLING: BillingSpecialist(),
            Intent.TECHNICAL: TechnicalSpecialist(),
            Intent.ACCOUNT: AccountSpecialist(),
        }
        self._actions = get_action_service()
        self._gate = EscalationGate()
        self._synth = ResponseSynthesizer()
        self._tracer = get_tracer()
        self._traces = TraceStore()
        self._conversations = ConversationStore()
        self._metrics = get_metrics()
        self._settings = get_settings()

    async def handle_request(
        self,
        request: SupportRequest,
        history: list[dict[str, Any]] | None = None,
    ) -> FinalResponse:
        trace = self._tracer.start_trace(
            request_id=request.request_id, customer_id=request.customer.customer_id
        )
        attempted: list[str] = []
        outcome = Outcome.FAILED
        final: FinalResponse | None = None

        try:
            async with span("orchestrator.run", request_id=request.request_id):
                # 1. Intake.
                try:
                    intake = await self._intake.process(
                        raw_message=request.message, history=history
                    )
                    attempted.append("intake_ok")
                except GibberishInputError:
                    outcome = Outcome.RESOLVED  # politely declined; not a failure
                    final = FinalResponse(
                        request_id=request.request_id,
                        response_text=(
                            "I didn't quite catch your question. Could you rephrase "
                            "what you need help with?"
                        ),
                        outcome=Outcome.CLARIFYING,
                        confidence=1.0,
                        trace_id=trace.trace_id,
                    )
                    return final

                # 2. Router.
                routing = await self._router.route(intake)
                attempted.append(f"routed:{routing.primary_intent.value}")

                # 3. Early escalation (explicit human / sensitive).
                early_escalate, early_reason = self._gate.should_escalate(
                    routing=routing, specialist_output=None, attempts=0
                )
                if early_escalate and (
                    routing.explicit_human_request
                    or routing.sensitivity in {Sensitivity.LEGAL, Sensitivity.SECURITY}
                ):
                    handoff = self._gate.build_handoff(
                        request_id=request.request_id,
                        trace_id=trace.trace_id,
                        customer=request.customer,
                        routing=routing,
                        specialist_output=None,
                        attempted_steps=attempted,
                        reason=early_reason,
                    )
                    outcome = Outcome.ESCALATED
                    final = self._build_escalation_response(
                        request_id=request.request_id,
                        trace_id=trace.trace_id,
                        routing=routing,
                        handoff=handoff,
                    )
                    return final

                # 4. Pick specialist (or clarify if unclear).
                specialist = self._specialists.get(routing.primary_intent)
                if specialist is None:
                    # Unclear or general intent: ask a clarifying question once.
                    outcome = Outcome.CLARIFYING
                    final = FinalResponse(
                        request_id=request.request_id,
                        response_text=(
                            "I want to make sure I route you to the right place. "
                            "Could you tell me whether your question is about billing, "
                            "a technical issue, or your account?"
                        ),
                        outcome=Outcome.CLARIFYING,
                        confidence=routing.confidence,
                        trace_id=trace.trace_id,
                    )
                    return final

                output = await specialist.handle(
                    intake=intake,
                    routing=routing,
                    customer=request.customer,
                    history_summary=intake.thread_summary,
                )
                attempted.append(f"specialist:{routing.primary_intent.value}")

                # 5. If specialist proposed actions and confidence high, execute.
                executed: list[ToolCallResult] = []
                if (
                    output.proposed_actions
                    and not output.needs_escalation
                    and output.confidence >= self._settings.confidence_auto_action
                ):
                    for proposal in output.proposed_actions:
                        result = await self._actions.execute(
                            proposal=proposal,
                            customer=request.customer,
                            original_request=intake.redacted_text,
                            request_id=request.request_id,
                            trace_id=trace.trace_id,
                            policy_passages=output.citations,
                        )
                        executed.append(result)
                        attempted.append(
                            f"action:{result.tool_name}:{'ok' if result.success else 'fail'}"
                        )
                        if not result.success and result.error and "denied" in result.error:
                            # A denied action escalates the case rather than
                            # silently moving on.
                            output.needs_escalation = True
                            output.escalation_reason = (
                                output.escalation_reason
                                or f"action {result.tool_name} denied: {result.error}"
                            )
                            break
                output.executed_actions = executed

                # 6. Escalate if needed.
                escalate, reason = self._gate.should_escalate(
                    routing=routing,
                    specialist_output=output,
                    attempts=1,
                )
                if escalate:
                    handoff = self._gate.build_handoff(
                        request_id=request.request_id,
                        trace_id=trace.trace_id,
                        customer=request.customer,
                        routing=routing,
                        specialist_output=output,
                        attempted_steps=attempted,
                        reason=reason,
                    )
                    outcome = Outcome.ESCALATED
                    final = self._build_escalation_response(
                        request_id=request.request_id,
                        trace_id=trace.trace_id,
                        routing=routing,
                        handoff=handoff,
                        draft=output.draft_response,
                    )
                    return final

                # 7. Clarification (one round only, per ADR).
                if output.needs_clarification and output.clarifying_question:
                    outcome = Outcome.CLARIFYING
                    final = FinalResponse(
                        request_id=request.request_id,
                        response_text=output.clarifying_question,
                        outcome=Outcome.CLARIFYING,
                        confidence=output.confidence,
                        citations=output.citations,
                        trace_id=trace.trace_id,
                    )
                    return final

                # 8. Synthesize final response.
                actions_summary = _summarize_executed(executed) if executed else None
                final_text = self._synth.finalize(
                    draft=output.draft_response,
                    customer_frustrated=routing.customer_frustration,
                    executed_actions_summary=actions_summary,
                )
                outcome = Outcome.RESOLVED
                final = FinalResponse(
                    request_id=request.request_id,
                    response_text=final_text,
                    outcome=outcome,
                    citations=output.citations,
                    confidence=output.confidence,
                    trace_id=trace.trace_id,
                )
                return final

        except Exception:
            _log.exception("orchestrator_failure", request_id=request.request_id)
            outcome = Outcome.FAILED
            final = FinalResponse(
                request_id=request.request_id,
                response_text=(
                    "Something went wrong on our side. We've logged this and a human "
                    "agent will follow up shortly."
                ),
                outcome=Outcome.FAILED,
                confidence=0.0,
                trace_id=trace.trace_id,
            )
            return final
        finally:
            self._metrics.requests_total.labels(
                outcome=outcome.value, intent=_intent_label(final)
            ).inc()
            trace.metadata["outcome"] = outcome.value
            await self._traces.save(trace.to_dict(), outcome=outcome.value)
            self._tracer.end_trace()

    def _build_escalation_response(
        self,
        *,
        request_id: str,
        trace_id: str,
        routing: RoutingDecision,
        handoff: EscalationHandoff,
        draft: str | None = None,
    ) -> FinalResponse:
        if routing.explicit_human_request:
            text = (
                "I'm connecting you with a human teammate now. "
                "They will pick up this conversation with full context."
            )
        elif routing.sensitivity in {Sensitivity.LEGAL, Sensitivity.SECURITY}:
            text = (
                "This needs a specialist on our team. I've routed it to the right "
                "person with full context and they will reach out shortly."
            )
        else:
            text = (
                "I want to make sure we get this right, so I'm handing this to a "
                "human teammate who can take a closer look. They have the full "
                "context of this conversation."
            )
        return FinalResponse(
            request_id=request_id,
            response_text=text,
            outcome=Outcome.ESCALATED,
            confidence=routing.confidence,
            escalation=handoff,
            trace_id=trace_id,
        )


def _intent_label(final: FinalResponse | None) -> str:
    if final is None:
        return "unknown"
    if final.escalation is not None:
        return final.escalation.sensitivity.value
    return "handled"


def _summarize_executed(results: list[ToolCallResult]) -> str | None:
    if not results:
        return None
    lines: list[str] = []
    for r in results:
        if r.success:
            if r.tool_name == "issue_refund" and r.result:
                lines.append(
                    f"I've issued a refund of ${r.result.get('amount_usd'):.2f}. "
                    "It should appear on your statement within 5-10 business days."
                )
            elif r.tool_name == "send_password_reset" and r.result:
                lines.append(
                    "I've sent a password reset link to the email on file. "
                    "It expires in 30 minutes."
                )
            elif r.tool_name == "change_plan" and r.result:
                lines.append(f"Your plan has been changed to {r.result.get('new_plan')}.")
            elif r.tool_name == "create_ticket" and r.result:
                lines.append(
                    f"I've opened a follow-up ticket ({r.result.get('ticket_id')}). "
                    "The relevant team will reach out with next steps."
                )
        else:
            lines.append(
                "I tried to take that action but it didn't go through. "
                "I'm passing this to a teammate who can complete it."
            )
    return "\n".join(lines) if lines else None
