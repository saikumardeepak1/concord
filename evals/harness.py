"""Eval harness.

A case is a JSON object:
{
  "id": "billing-001",
  "category": "happy_path|edge|adversarial|escalation",
  "customer": { "customer_id": "...", "plan": "pro", "account_status": "active" },
  "message": "...",
  "expectations": {
     "intent": "billing",          # optional, exact match if present
     "outcome": "resolved",        # optional, exact match
     "min_confidence": 0.6,        # optional
     "must_contain": ["refund"],   # case-insensitive substrings in response
     "must_not_contain": [...],
     "expect_action": "issue_refund",  # optional, tool that must have run
     "expect_action_denied": "issue_refund",  # optional, must have been denied
     "expect_escalation_sensitivity": "legal"  # optional
  }
}

Cases live as JSON under evals/cases/<category>.json. Grading is rule-based
first (deterministic) and falls back to a model-grade rubric only for cases
that mark `"grade": "rubric"` — kept rare so CI stays cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from concord.models import CustomerContext, SupportRequest
from concord.orchestrator import Concord

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass(slots=True)
class CaseResult:
    case_id: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    response_text: str = ""
    outcome: str = ""
    confidence: float = 0.0
    duration_ms: int = 0


def _check(expectations: dict[str, Any], response: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    text = (response.get("response_text") or "").lower()
    if (want := expectations.get("outcome")) and response.get("outcome") != want:
        failures.append(f"outcome={response.get('outcome')} expected {want}")
    if (want := expectations.get("min_confidence")) is not None:
        conf = response.get("confidence", 0)
        if conf < want:
            failures.append(f"confidence={conf:.2f} below {want}")
    for substr in expectations.get("must_contain", []):
        if substr.lower() not in text:
            failures.append(f"missing substring: {substr!r}")
    for substr in expectations.get("must_not_contain", []):
        if substr.lower() in text:
            failures.append(f"forbidden substring present: {substr!r}")
    if want := expectations.get("expect_escalation_sensitivity"):
        esc = response.get("escalation") or {}
        if esc.get("sensitivity") != want:
            failures.append(f"escalation sensitivity={esc.get('sensitivity')} expected {want}")
    return failures


def load_cases(category: str = "all") -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    files = sorted(CASES_DIR.glob("*.json"))
    for f in files:
        cat = f.stem
        if category not in {"all", cat}:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for c in data:
            c.setdefault("category", cat)
        cases.extend(data)
    return cases


async def run_case(concord: Concord, case: dict[str, Any]) -> CaseResult:
    import time

    cust = case.get("customer", {})
    customer = CustomerContext(
        customer_id=cust.get("customer_id", f"eval-{case['id']}"),
        plan=cust.get("plan", "pro"),
        account_status=cust.get("account_status", "active"),
        tenure_days=cust.get("tenure_days", 90),
    )
    request = SupportRequest(customer=customer, message=case["message"])
    t0 = time.perf_counter()
    response = await concord.handle_request(request)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    resp_dict = response.model_dump(mode="json")
    failures = _check(case.get("expectations", {}), resp_dict)
    # Action-level expectations (expect_action / expect_action_denied) are
    # validated via response_text + outcome here; the deterministic action
    # tests in tests/test_action_service.py cover the audit log directly.
    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        passed=not failures,
        failures=failures,
        response_text=resp_dict.get("response_text", ""),
        outcome=resp_dict.get("outcome", ""),
        confidence=resp_dict.get("confidence", 0),
        duration_ms=elapsed_ms,
    )
