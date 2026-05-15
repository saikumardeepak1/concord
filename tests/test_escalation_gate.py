from concord.escalation.gate import EscalationGate
from concord.models import Intent, RoutingDecision, Sensitivity, SpecialistOutput


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


def test_explicit_human_request_escalates() -> None:
    gate = EscalationGate()
    decision = _routing(explicit_human_request=True)
    ok, reason = gate.should_escalate(routing=decision, specialist_output=None, attempts=0)
    assert ok and "human" in reason


def test_security_sensitivity_escalates() -> None:
    gate = EscalationGate()
    decision = _routing(sensitivity=Sensitivity.SECURITY)
    ok, reason = gate.should_escalate(routing=decision, specialist_output=None, attempts=0)
    assert ok and "security" in reason


def test_low_specialist_confidence_escalates() -> None:
    gate = EscalationGate()
    decision = _routing(confidence=0.9)
    spec = SpecialistOutput(
        intent=Intent.BILLING,
        draft_response="not sure",
        confidence=0.1,
    )
    ok, reason = gate.should_escalate(routing=decision, specialist_output=spec, attempts=1)
    assert ok and "confidence" in reason


def test_high_confidence_resolved_does_not_escalate() -> None:
    gate = EscalationGate()
    decision = _routing()
    spec = SpecialistOutput(
        intent=Intent.BILLING,
        draft_response="here is your answer",
        confidence=0.92,
    )
    ok, _ = gate.should_escalate(routing=decision, specialist_output=spec, attempts=1)
    assert not ok
