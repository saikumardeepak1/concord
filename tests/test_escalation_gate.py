from concord.escalation.gate import EscalationGate, GateContext
from concord.models import Intent, RetrievedPassage, RoutingDecision, Sensitivity, SpecialistOutput


def _routing(**kw) -> RoutingDecision:
    base = dict(
        primary_intent=Intent.BILLING,
        confidence=0.9,
        sensitivity=Sensitivity.NONE,
        urgency=2,
        rationale="test",
    )
    base.update(kw)
    return RoutingDecision(**base)


def _good_spec(confidence: float = 0.92, **kw) -> SpecialistOutput:
    base: dict = dict(
        intent=Intent.BILLING,
        draft_response="here is your answer",
        confidence=confidence,
        citations=[
            RetrievedPassage(
                doc_id="d1",
                title="Refunds",
                text="...",
                source="billing/refund-policy.md",
                score=0.8,
            )
        ],
    )
    base.update(kw)
    return SpecialistOutput(**base)


# -------------------------- hard triggers --------------------------


def test_explicit_human_request_escalates() -> None:
    # Trigger 2.
    gate = EscalationGate()
    ctx = GateContext(routing=_routing(explicit_human_request=True), specialist_output=None, attempts=0)
    v = gate.evaluate(ctx)
    assert v.escalate and 2 in v.triggers_fired


def test_security_sensitivity_escalates() -> None:
    # Trigger 3.
    gate = EscalationGate()
    ctx = GateContext(routing=_routing(sensitivity=Sensitivity.SECURITY), specialist_output=None, attempts=0)
    v = gate.evaluate(ctx)
    assert v.escalate and 3 in v.triggers_fired


def test_churn_risk_escalates() -> None:
    # Trigger 3 expanded.
    gate = EscalationGate()
    ctx = GateContext(routing=_routing(sensitivity=Sensitivity.CHURN_RISK), specialist_output=None, attempts=0)
    v = gate.evaluate(ctx)
    assert v.escalate and 3 in v.triggers_fired


def test_knowledge_gap_escalates() -> None:
    # Trigger 6: specialist returns no citations.
    gate = EscalationGate()
    spec = SpecialistOutput(
        intent=Intent.BILLING, draft_response="no idea", confidence=0.92, citations=[]
    )
    ctx = GateContext(routing=_routing(), specialist_output=spec, attempts=1, top_retrieval_score=None)
    v = gate.evaluate(ctx)
    assert v.escalate and 6 in v.triggers_fired


def test_weak_top_score_escalates() -> None:
    # Trigger 6: passages came back but top score is below the weak threshold.
    gate = EscalationGate()
    spec = _good_spec(confidence=0.92)
    ctx = GateContext(
        routing=_routing(),
        specialist_output=spec,
        attempts=1,
        top_retrieval_score=0.10,  # well below threshold
    )
    v = gate.evaluate(ctx)
    assert v.escalate and 6 in v.triggers_fired


# -------------------------- soft triggers --------------------------


def test_single_soft_trigger_does_not_escalate() -> None:
    # Trigger 1 alone (low confidence) should not fire the gate.
    gate = EscalationGate()
    spec = _good_spec(confidence=0.30)
    ctx = GateContext(routing=_routing(), specialist_output=spec, attempts=1, top_retrieval_score=0.7)
    v = gate.evaluate(ctx)
    assert not v.escalate


def test_two_soft_triggers_escalate() -> None:
    # Trigger 1 + Trigger 9: low confidence AND frustrated customer.
    gate = EscalationGate()
    spec = _good_spec(confidence=0.30)
    ctx = GateContext(
        routing=_routing(customer_frustration=True),
        specialist_output=spec,
        attempts=1,
        top_retrieval_score=0.7,
    )
    v = gate.evaluate(ctx)
    assert v.escalate and 1 in v.triggers_fired and 9 in v.triggers_fired


def test_frustration_alone_does_not_escalate() -> None:
    gate = EscalationGate()
    ctx = GateContext(
        routing=_routing(customer_frustration=True),
        specialist_output=_good_spec(),
        attempts=1,
        top_retrieval_score=0.8,
    )
    v = gate.evaluate(ctx)
    assert not v.escalate


# -------------------------- non-escalating --------------------------


def test_high_confidence_resolved_does_not_escalate() -> None:
    gate = EscalationGate()
    ctx = GateContext(
        routing=_routing(),
        specialist_output=_good_spec(confidence=0.92),
        attempts=1,
        top_retrieval_score=0.8,
    )
    v = gate.evaluate(ctx)
    assert not v.escalate
