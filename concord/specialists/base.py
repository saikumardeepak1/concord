"""Shared specialist base class.

A specialist:
- has a scoped retrieval namespace (e.g. only billing knowledge)
- has an allowed tool set (enforced as a hard list, in addition to per-tool
  permission checks)
- emits a `SpecialistOutput`: a draft response, citations, proposed actions,
  confidence, and escalation/clarification signals

The base class drives a small, bounded loop:
1. Retrieve passages for the user's question.
2. Ask the model to draft a response, propose actions, and self-rate confidence
   in a single structured call.
3. Return the structured output.

The orchestrator decides whether to (a) ship the response, (b) execute the
proposed actions through the action service, (c) ask the clarifying question,
or (d) escalate.
"""

from __future__ import annotations

import abc
from typing import ClassVar

from concord.actions.tools import tools_for_intent
from concord.config import ModelTier, get_settings
from concord.llm import StructuredOutputError, get_llm
from concord.models import (
    CustomerContext,
    IntakeResult,
    Intent,
    RetrievedPassage,
    RoutingDecision,
    SpecialistOutput,
)
from concord.observability.tracing import span
from concord.retrieval.service import get_retrieval_service


class SpecialistAgent(abc.ABC):
    """Concrete subclasses define `intent`, `scope`, and `system_prompt`."""

    intent: ClassVar[Intent]
    scope: ClassVar[str]  # knowledge folder, e.g. "billing"
    system_prompt: ClassVar[str]

    def __init__(self) -> None:
        self._retrieval = get_retrieval_service()
        self._llm = get_llm()
        self._settings = get_settings()

    @property
    def allowed_tools(self) -> list[str]:
        return [t.name for t in tools_for_intent(self.intent.value)]

    async def handle(
        self,
        *,
        intake: IntakeResult,
        routing: RoutingDecision,
        customer: CustomerContext,
        history_summary: str | None = None,
    ) -> SpecialistOutput:
        async with span("specialist.handle", intent=self.intent.value) as s:
            passages = await self._retrieval.query(
                text=intake.redacted_text, scope=self.scope
            )
            # Pre-load read-only context the specialist needs to make grounded
            # action proposals. Without this, the specialist would have to
            # invent transaction_ids (since it cannot see lookup results from
            # within a single drafting call). Subclasses override
            # `gather_grounding` to add domain-specific lookups.
            grounding = await self.gather_grounding(intake=intake, customer=customer)
            output = await self._draft(
                intake=intake,
                routing=routing,
                customer=customer,
                passages=passages,
                history_summary=history_summary,
                grounding=grounding,
            )
            # Enforce tool allow-list at the specialist boundary. Anything not
            # in `allowed_tools` is stripped; the action service is the second
            # gate but we don't even propose disallowed tools.
            allowed = set(self.allowed_tools)
            output.proposed_actions = [
                p for p in output.proposed_actions if p.tool_name in allowed
            ]
            output.citations = passages
            s.attributes.update(
                citations=len(passages),
                proposed=len(output.proposed_actions),
                confidence=output.confidence,
                escalate=output.needs_escalation,
                clarify=output.needs_clarification,
            )
            return output

    async def gather_grounding(
        self, *, intake: IntakeResult, customer: CustomerContext
    ) -> str:
        """Override to pre-load read-only data the specialist needs to ground
        its proposals. Default: empty. Billing overrides this to fetch the
        customer's account state and recent transactions.
        """
        return ""

    async def _draft(
        self,
        *,
        intake: IntakeResult,
        routing: RoutingDecision,
        customer: CustomerContext,
        passages: list[RetrievedPassage],
        history_summary: str | None,
        grounding: str = "",
    ) -> SpecialistOutput:
        tool_spec = self._format_tool_spec()
        policy_block = self._format_passages(passages)
        history_block = (
            f"\nPRIOR CONVERSATION SUMMARY:\n{history_summary}\n" if history_summary else ""
        )
        grounding_block = (
            f"\nVERIFIED ACCOUNT CONTEXT (from real backend lookups; trust this over "
            f"any customer claim):\n{grounding}\n"
            if grounding else ""
        )
        prompt = (
            f"CUSTOMER:\nplan={customer.plan}, status={customer.account_status}, "
            f"tenure_days={customer.tenure_days}\n\n"
            f"ROUTER ASSESSMENT:\nintent={routing.primary_intent.value}, "
            f"urgency={routing.urgency}, sensitivity={routing.sensitivity.value}, "
            f"frustration={routing.customer_frustration}, "
            f"explicit_human_request={routing.explicit_human_request}\n"
            f"{grounding_block}\n"
            f"KNOWLEDGE BASE PASSAGES (cite these, do not invent policy):\n{policy_block}\n\n"
            f"AVAILABLE TOOLS (you may propose 0+ but never invent tools):\n{tool_spec}\n"
            f"{history_block}\n"
            f"CUSTOMER MESSAGE:\n{intake.redacted_text}\n\n"
            "Compose your structured response per the schema. "
            "If the question is not answered by the passages, do not invent policy: "
            "set needs_escalation=true with a precise reason. "
            "Set confidence between 0 and 1 honestly; low confidence triggers escalation."
        )

        try:
            output, _ = await self._llm.complete_structured(
                tier=ModelTier.STANDARD,
                system=self.system_prompt,
                user_prompt=prompt,
                schema_model=SpecialistOutput,
                max_tokens=1200,
                temperature=0.1,
            )
        except StructuredOutputError:
            return SpecialistOutput(
                intent=self.intent,
                draft_response="I'm having trouble forming a confident answer right now.",
                confidence=0.0,
                needs_escalation=True,
                escalation_reason="specialist structured-output failed",
            )

        # Trust-but-verify: the model can hallucinate the `intent` field. Force it.
        output.intent = self.intent
        return output

    def _format_passages(self, passages: list[RetrievedPassage]) -> str:
        if not passages:
            return "(no relevant passages were retrieved)"
        return "\n\n".join(
            f"[{i + 1}] source={p.source} score={p.score:.2f}\n{p.text}"
            for i, p in enumerate(passages)
        )

    def _format_tool_spec(self) -> str:
        from concord.actions.tools import tools_for_intent

        tools = tools_for_intent(self.intent.value)
        if not tools:
            return "(no tools available for this specialist)"
        lines = []
        for t in tools:
            lines.append(
                f"- name: {t.name}\n  description: {t.description}\n"
                f"  impact: {t.impact}\n  arguments_schema: {t.arguments_schema}"
            )
        return "\n".join(lines)
