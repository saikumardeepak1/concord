# Concord Architecture

This document walks through how a request flows through Concord and why each
piece exists. Every architectural choice is anchored to an ADR in
`PROJECT_BRIEF.md` section 7.

## Component map

| Stage           | Module                         | Responsibility                                                  | ADR  |
|-----------------|--------------------------------|------------------------------------------------------------------|------|
| Intake          | `concord/intake/`              | normalize, detect PII, summarize long threads                    | 008, 009 |
| Router          | `concord/router/`              | fast classifier — intent, sensitivity, urgency                   | 001, 003 |
| Specialist      | `concord/specialists/`         | scoped knowledge + scoped tools per intent                       | 001 |
| Retrieval       | `concord/retrieval/`           | Chroma vector store, markdown-aware chunking, scope filtering    | 004, 006 |
| Action layer    | `concord/actions/`             | schema → permission → verification → idempotency → execute → audit | 002, 004 |
| Escalation gate | `concord/escalation/`          | confidence + rule-based handoff with structured packet           | 005, 010 |
| Synthesis       | `concord/synthesis/`           | leakage scrub, tone normalization, response assembly             | — |
| Observability   | `concord/observability/`       | per-request traces and Prometheus metrics                        | — |
| Persistence     | `concord/state.py`             | conversation state, audit log, trace store                       | 007 |

## Request lifecycle

```
SupportRequest
  ↓
[ Tracer.start_trace ]   ← span context activated for the rest of the request
  ↓
IntakeStage.process
  • normalize
  • detect_pii  → tags, redacted text
  • summarize history if needed
  ↓
RouterAgent.route  (FAST tier, structured output)
  → RoutingDecision { intent, confidence, sensitivity, urgency, ... }
  ↓
EscalationGate.should_escalate (early — explicit human / sensitive)
  └─ if yes:  build_handoff → FinalResponse(outcome=ESCALATED)
  ↓
Specialist.handle  (STANDARD tier, structured output)
  • retrieval scoped to specialist
  • model proposes 0..N ToolCallProposals
  → SpecialistOutput { draft, confidence, citations, proposed_actions, ... }
  ↓
[ For each ToolCallProposal: ]
  ActionService.execute
    ① schema validate
    ② permission predicate (rule-based, fast, deterministic)
    ③ if impact in {medium, high}: VerificationAgent.verify (HIGH tier)
    ④ idempotency lookup against audit_log
    ⑤ handler with asyncio.wait_for(timeout=15s)
    ⑥ AuditLog.record (always, success or failure)
  → ToolCallResult
  ↓
EscalationGate.should_escalate (late — low confidence, specialist-signaled, action denied)
  ↓
ResponseSynthesizer.finalize
  • internal leakage scrub
  • tone normalization (empathy beat if frustrated)
  • action results appended
  ↓
FinalResponse { text, outcome, citations, confidence, escalation? , trace_id }
  ↓
TraceStore.save  +  metrics.requests_total++
```

## Why specialists are scoped

Each specialist declares:

- `intent`: the routing intent it handles (one of billing/technical/account)
- `scope`: the knowledge folder it queries (`billing/`, `technical/`, etc.)
- `allowed_tools`: derived from the tool registry's `intent_scope`

Three benefits:

1. **Attention.** A specialist sees only its own tools and its own knowledge.
   Tool-selection accuracy is materially better than a single agent with all
   tools in context.
2. **Permission boundary.** The orchestrator strips any proposed tool not in
   the specialist's allow-list before it ever reaches the action service.
   The action service then re-checks. Two gates, not one.
3. **Localized failures.** If the billing specialist misbehaves, the technical
   and account agents are unaffected. We can update one prompt without
   regression-testing all three.

## Why a separate verification agent

Section 4.2 and ADR-002. The verification agent is given only:

1. The customer's original (redacted) message.
2. The proposed tool call (name, arguments, rationale).
3. The relevant policy passages already retrieved by the specialist.

It does **not** see the specialist's chain of thought. Empirically, a model
that produced a plan is biased toward confirming it; an independent instance
without the generation context catches policy violations and request-mismatch
errors much more reliably.

For low-impact tools (e.g. `create_ticket`), the verification pass is skipped
to keep latency and cost down — the permission predicate is the only gate.

## Audit log invariants

Every call to `ActionService.execute` writes an audit entry, regardless of
outcome:

- approved = True / False (combined approve gate: schema + permission + verify)
- arguments: the validated arguments
- result: the handler's return or null
- verification_rationale: human-readable reason for the decision
- idempotency_key: hash of `(request_id, customer_id, tool, arguments)`

The idempotency key is the same primitive `Stripe-Idempotency-Key` uses: replay
of the same triple returns the original result, so a network retry from the
client cannot double-act.

## Tracing

`concord/observability/tracing.py` provides an `asyncio` context manager
`span(name, **attrs)`. Each stage opens spans:

- `orchestrator.run`
- `intake.process`
- `router.classify`
- `specialist.handle`
- `retrieval.query`
- `action.execute`, `action.verify`
- `llm.complete` (per model call)

The active trace is stored in a `contextvars.ContextVar` so spans nest
correctly across async tasks without per-call plumbing. Traces are flushed to
the SQLite `traces` table at end-of-request and served at `/traces/{id}`.

Swapping to OpenTelemetry is a one-file change: replace `Tracer` with an OTel
exporter and the call sites are unchanged.

## Cost and latency budget

| Stage          | Tier     | Typical tokens | Approx ms |
|----------------|----------|----------------|-----------|
| Router         | fast     | ~400 in / 80 out | 300 |
| Retrieval      | (local)  | —              | 30-80     |
| Specialist     | standard | ~2k in / 500 out | 1200 |
| Verification   | high     | ~1k in / 200 out | 800 |
| Synthesis      | (local)  | —              | <5 |

A read-only request (no action) costs ~$0.005 with default tier choices.
An action-taking request adds ~$0.02 for verification. The cost dashboard
(`/metrics`) tracks `concord_cost_micro_usd_total` so any regression is
visible immediately.
