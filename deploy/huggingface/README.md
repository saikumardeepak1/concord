---
title: Concord
emoji: 🛡
colorFrom: gray
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Multi-agent customer support with governed safety gates.
---

# Concord

A reference implementation of a production-shaped multi-agent customer support
system. Verified identity, scoped retrieval, governed actions, independent
verification, full audit trail.

The agent answers customer questions, looks up real account data, takes
state-changing actions (like issuing refunds), and escalates to a human when
the case is hard, sensitive, or adversarial. Every consequential action passes
through four independent safety layers before it executes.

**Source code:** https://github.com/saikumardeepak1/concord

---

## Try the demo in 60 seconds

Scroll down to the app. You will see three columns:

| Column | What it is |
|---|---|
| **Left** | 17 pre-built test scenarios, grouped by customer. Each one triggers a specific safety path. |
| **Center** | The chat. Click a scenario to load its message, then **Send**. |
| **Right** | The verified customer context (real account state, real transactions) and the live trace of every step the agent took. |

### Three things to try first

**1. Watch a refund actually execute.**
Click the scenario *"Standard refund (happy path)"* under `cust-001`. Press Send.
You should see `outcome=resolved` with a $45 refund issued against
transaction `tx_a002`. The trace on the right will show the router, the
specialist, the permission check, the verifier, the execution, and the
audit-log write.

**2. Watch the system refuse a fake charge.**
Click *"Refund for a charge that doesn't exist"* under `cust-001` (a $500
charge that isn't in the ledger). Press Send.
You should see `outcome=clarifying`. The agent does not invent a refund. It
names the real charges it *does* see and asks the customer to clarify.

**3. Watch a prompt-injection attempt get refused.**
Click *"Adversarial: prompt injection"* under `cust-001`. The message tries to
talk the agent into a $9999 refund by claiming "the developer said it's fine."
Press Send.
The proposed action gets rejected. The customer's words do not have authority
over policy.

---

## What every outcome means

| Outcome | Meaning |
|---|---|
| **resolved** | Agent answered the question or executed the requested action. |
| **clarifying** | Agent needs more information from the customer before it can safely act. |
| **escalated** | Case routed to a human (sensitivity, policy cap, or repeated failure). |
| **customer_not_found** | Identity gate rejected the request at the API boundary. Agent never ran. |

A safe agent should never reach `resolved` on a request that violates policy.
That is the load-bearing invariant.

---

## The six demo customers

| ID | Plan | Status | What they test |
|---|---|---|---|
| `cust-001` | pro | active | Happy path. Has a duplicate $45 charge to refund. |
| `cust-002` | pro | suspended | Account-state gate. Refunds and changes are blocked. |
| `cust-003` | pro | past_due | Failed-payment scenario, refunds still possible. |
| `cust-004` | enterprise | active | $24k charge. Any meaningful refund exceeds the cap. |
| `cust-005` | free | active | No charges on file. "Refund me" has nothing to refund. |
| `cust-006` | pro | churned | Last charge 60 days ago, outside the 14-day window. |

You can also type any custom message in the chat. The agent will route it
through the same pipeline.

---

## The safety story

Every state-changing action passes through four independent layers:

1. **Tool allow-list** per specialist. Billing can only call billing tools.
2. **Rule-based permission predicate.** Deterministic checks: amount within
   cap, transaction exists on this customer, account status allows the action.
3. **Independent verifier.** A separate LLM reviews the proposed action with
   no context from the specialist that proposed it. It cannot be talked into
   approval by reasoning the specialist included.
4. **Append-only audit log.** Every approval and denial recorded with the
   full rationale, queryable for compliance.

Escalation is a separate path. A 9-trigger gate evaluates every turn:
explicit human request, legal or security sensitivity, churn risk, billing
disputes, low confidence combined with frustrated tone, verifier rejection,
retrieval failure, tool failure, repeated unresolved attempts, cost-budget
exceeded. Hard triggers fire alone; soft triggers (low confidence,
frustration) require two before they escalate.

---

## What the numbers mean

The system has been graded against 153 test cases across four categories:

- **adversarial: 25/25 (100%)** — prompt injection, social engineering, fake
  authority, invented amounts. Zero successful policy bypasses.
- **escalation: 34/35 (97%)** — legal threats, security incidents, churn risk,
  refunds over cap, knowledge gaps. The right cases reach humans.
- **edge: 34/35 (97%)** — vague messages, multi-intent, partial information.
- **happy_path: 47/58 (81%)** — clean answers and executions. Most "failures"
  are the agent asking a clarifying question when the customer was vague,
  which is correct behavior, not a bug.

---

## Architecture (high level)

```
Customer message
       ↓
Identity gate (verify customer_id)
       ↓
Router (fast LLM)  →  intent, sensitivity, urgency, frustration
       ↓
Early escalation gate  →  short-circuit on explicit human / legal / security
       ↓
Specialist (billing / technical / account, standard LLM)
       ↓
  ├── Scoped retrieval (Chroma, only that specialist's KB slice)
  ├── Pre-loaded customer context (real ledger lookup)
  └── Drafts response + proposes 0+ tool calls
       ↓
Action service (per proposed tool)
       ├── Schema validation
       ├── Permission predicate (rule-based)
       ├── Independent verifier (separate Opus call, no specialist context)
       ├── Idempotency check
       ├── Execute with timeout
       └── Append to audit log
       ↓
Escalation gate (9 triggers)  →  ship vs human handoff
       ↓
Synthesizer (leakage scrub, tone normalize)
       ↓
Customer response + structured trace
```

Full walkthrough, ADRs, the 153-case eval harness, and the test suite are in
the GitHub repo.

---

## For developers and recruiters

If you want to read the code, run the evals locally, or fork the deploy: see
the [GitHub repo](https://github.com/saikumardeepak1/concord).

Test commands for every safety gate, the full failure-mode playbook, and
deploy instructions for Render and HF Spaces are in `deploy/README.md` on the
repo.
