# Architectural Decision Records

This document captures the major architectural decisions for Concord. Each
record follows the same format: what was chosen, alternatives considered,
why those were rejected, trade-offs accepted, and what could prove the
decision wrong.

The point of these records is that any reader can interrogate a design
choice and get a defensible answer. Decisions change as the project teaches
us things; a decision being written down is not a decision being permanent,
it is a decision being explicit.

---

## ADR-001: Router-plus-specialists over a monolithic agent

**Decision.** Split the system into a cheap routing agent that classifies intent and separate specialist agents that handle each category.

**Alternatives considered.**
- (A) Single monolithic agent with all tools and all knowledge in one context.
- (B) No router; let the user pick their own category from a menu.
- (C) A hierarchical chain where a supervisor agent delegates and reviews every specialist response.

**Why we rejected them.**
- (A) fails at scale. A single agent with 15+ tools and the full knowledge base in context suffers from attention dilution (tool selection accuracy drops as tool count rises), makes permission boundaries impossible to enforce per-domain, and makes failures hard to localize because everything is one big prompt. Cost is also unpredictable because every request pays for the full context regardless of complexity.
- (B) shifts cognitive load to the user and breaks for multi-intent messages. It also means the system cannot improve routing over time because there is no routing step to measure and tune.
- (C) adds latency and cost on every request because the supervisor reviews even trivial cases. It is the right pattern when specialist outputs are high-risk and unpredictable, but for customer support the action layer's verification pass handles that more surgically.

**Trade-offs accepted.** We pay the latency of an extra model call (the router) on every request. We also accept the complexity of maintaining separate system prompts and tool sets per specialist, which means more configuration surface and more things to keep in sync. We accept that the router can misclassify, so we need a re-routing mechanism when a specialist detects it received the wrong case.

**What could prove us wrong.** If the tool count stays small (under 6) and the knowledge base is compact, a monolithic agent might be simpler and fast enough. If we find the router's misclassification rate is high enough to negate the specialist accuracy gains, the split is not paying for itself.

## ADR-002: Independent verification pass for state-changing actions

**Decision.** Before any state-changing action executes, a separate model instance (with no prior reasoning context from the specialist) reviews the proposed action against policy and the customer's actual request.

**Alternatives considered.**
- (A) Self-review: the same specialist that proposed the action also reviews it.
- (B) Rule-based validation only (hardcoded business rules, no model in the loop).
- (C) Human-in-the-loop for all state-changing actions.

**Why we rejected them.**
- (A) is unreliable. A model that just generated a plan retains the reasoning context that produced it and is biased toward confirming its own decision. Independent instances without prior reasoning context catch subtle policy violations far more reliably than self-review instructions in the same session.
- (B) works for simple validations (amount caps, account status checks) but cannot catch nuanced mismatches between what the customer asked and what the agent proposed. We use rule-based checks as the first gate, but they are not sufficient alone.
- (C) defeats the purpose. If every action needs a human, we have not automated anything. Human review should be reserved for the cases the system cannot handle confidently, not for routine operations.

**Trade-offs accepted.** Every state-changing action costs an extra model call (on the high tier, since correctness matters most here). This adds both latency and cost. For a refund or account modification, the extra second and fraction of a cent are justified by the consequence of getting it wrong. For very high-volume, low-risk actions, this might be overbuilt, and we may eventually add a "low-risk bypass" tier that skips verification for actions under a certain threshold.

**What could prove us wrong.** If the verification agent's false-positive rate is high (it blocks legitimate actions too often), it becomes a bottleneck and the specialists' accuracy might be good enough with rule-based checks alone. We track verification overrides and false-positive rate in the metrics dashboard to know.

## ADR-003: Tiered model strategy over a single model

**Decision.** Use different model tiers for different stages: fast/cheap for routing and classification, mid-tier for specialist reasoning, high-tier for verification and hard judgment calls. Model selection is in config, not hardcoded.

**Alternatives considered.**
- (A) Use the most capable model everywhere for maximum quality.
- (B) Use the cheapest model everywhere and compensate with better prompts.
- (C) Dynamic model selection per-request based on detected complexity.

**Why we rejected them.**
- (A) is wasteful. Routing and classification are solved well by smaller models; paying the most capable model's cost and latency for intent classification on every request is throwing money away with no quality gain.
- (B) is false economy. Cheap models genuinely struggle with multi-step reasoning, nuanced policy application, and adversarial input handling. Saving on model cost while increasing resolution failure rate and escalation rate costs more in the end.
- (C) is elegant in theory but adds a meta-decision layer (which model should I use?) that itself needs a model or heuristic to decide, and errors in that meta-decision are hard to debug. We may evolve toward this, but starting with static tier assignment per stage is simpler, more predictable, and easier to measure.

**Trade-offs accepted.** Static tiering means we sometimes use a more expensive model than needed (a trivial billing question still gets the mid-tier specialist) and sometimes a cheaper model than ideal (a genuinely hard routing edge case might benefit from a smarter router). We accept this in exchange for predictability and simplicity, and we track cost-per-resolution to validate the tiering is working.

**What could prove us wrong.** If model pricing or capability gaps shift dramatically (the cheap tier gets much smarter, or the mid-tier becomes cheap enough to use everywhere), the tiering adds complexity without benefit. The config-driven approach makes re-tiering a settings change, not a code change, so we can adapt.

## ADR-004: MCP servers for tools and retrieval, not inline functions

**Decision.** Expose the retrieval subsystem and the action layer as MCP (Model Context Protocol) servers rather than defining tools as inline function definitions in the agent code.

**Alternatives considered.**
- (A) Inline tool definitions (functions defined directly in the agent's codebase, called via the model's tool-use feature).
- (B) A custom REST API layer between the agent and the backends.
- (C) Direct database and service calls from the agent code with no abstraction layer.

**Why we rejected them.**
- (A) works for simple cases but couples the tool logic tightly to the agent code. You cannot test, version, or reuse the tools independently. When multiple specialists need the same tool, you duplicate or share code in ways that get messy.
- (B) is functional but non-standard. MCP is the emerging standard protocol for connecting AI agents to external tools and data; building on a standard means other MCP-compatible clients can consume the same servers.
- (C) is the fastest to prototype but the hardest to secure, test, and maintain. No permission boundary, no schema validation, no independent testability.

**Trade-offs accepted.** MCP adds setup overhead and a protocol layer that a simpler inline approach does not have. For a small system with 3 tools, MCP might be overengineered. We accept this because the protocol boundary pays for itself in testability, security, and reusability as the tool count grows.

**What could prove us wrong.** If MCP tooling is still immature enough that debugging MCP issues becomes a significant time sink, the protocol overhead might not be worth it for this size of system. We would still keep the conceptual boundary but might simplify the implementation to in-process modules with the same shape.

## ADR-005: Escalation as a first-class outcome, not a failure state

**Decision.** The system is designed and measured to escalate the right cases, not to minimize escalation. Escalation is a success when the case genuinely needed a human.

**Alternatives considered.**
- (A) Optimize for lowest possible escalation rate, treating every escalation as a system failure.
- (B) Escalate everything above a fixed confidence threshold with no further nuance.

**Why we rejected them.**
- (A) incentivizes the agent to attempt resolution on cases it should not handle, leading to bad outcomes on hard cases, frustrated customers, and potential policy violations. In enterprise support, a wrong answer is far more expensive than a slow one.
- (B) ignores context. A low-confidence billing question and a low-confidence legal/compliance question have very different escalation urgency. Sensitivity detection (legal, security, churn risk) should trigger escalation independently of confidence.

**Trade-offs accepted.** By valuing escalation quality over escalation rate, we will escalate more than a system tuned purely for automation rate. This means more human workload than the absolute minimum, and the metric looks worse on a dashboard that only shows automation percentage. We compensate by measuring escalation precision (were the escalated cases actually hard?) and by surfacing the full context summary so the human agent can resolve quickly.

**What could prove us wrong.** If the confidence scoring is poorly calibrated and escalates too many easy cases, we lose the automation benefit without gaining safety. The eval suite must include escalation-boundary cases to keep the calibration honest.

## ADR-006: RAG retrieval over fine-tuning or full-context knowledge stuffing

**Decision.** Use retrieval-augmented generation (RAG) via a vector store to surface relevant knowledge at query time, rather than fine-tuning the model on company knowledge or stuffing the full knowledge base into the context window.

**Alternatives considered.**
- (A) Fine-tune a model on the company's support docs and past tickets.
- (B) Put the entire knowledge base in the system prompt or context window.
- (C) No retrieval; rely on the base model's general knowledge plus the system prompt.

**Why we rejected them.**
- (A) is expensive to train, slow to update when docs change, hard to audit (you cannot trace which training example influenced an answer), and creates a model you have to manage and version. For enterprise support where policies change frequently, the update lag alone is disqualifying.
- (B) works only if the knowledge base is small enough to fit. It scales poorly, wastes tokens on irrelevant content for every request, and provides no mechanism to prioritize or source specific passages. Once the knowledge base exceeds a few hundred pages, this breaks.
- (C) produces hallucinated policies and product details. The base model does not know the company's specific refund policy or product configuration. Retrieval grounds answers in actual documentation.

**Trade-offs accepted.** RAG adds infrastructure complexity (vector store, chunking pipeline, embedding model, index maintenance) and introduces retrieval quality as a new failure mode. Bad retrieval (wrong chunks, stale content, poor ranking) produces bad answers even if the model is capable. We must evaluate retrieval quality independently and maintain the index as docs change.

**What could prove us wrong.** If the knowledge base is very small and stable, the full-context approach is simpler and avoids the retrieval failure mode entirely. If the project has the resources and timeline for fine-tuning, a fine-tuned model combined with retrieval can outperform either approach alone, but that is a future optimization, not a starting point.

## ADR-007: Stateless request handlers with externalized conversation state

**Decision.** Request handlers do not hold conversation state in memory. All state (conversation history, action state, thread summaries) lives in an external store.

**Alternatives considered.**
- (A) In-memory state per session, with sticky routing to the same server instance.
- (B) State encoded in the client payload (client sends full history with each request).

**Why we rejected them.**
- (A) breaks on server restarts, deploys, and horizontal scaling. Sticky sessions add infrastructure complexity and create uneven load distribution. A server crash loses all active conversations.
- (B) works for simple cases but puts the conversation history in the client's control, which is a security concern (clients can tamper with history) and a bandwidth concern as threads grow long.

**Trade-offs accepted.** External state adds a dependency (the state store must be available and fast) and adds latency for state reads and writes on every request. We also need to handle state-store failures gracefully. The system should degrade (queue the request, retry) rather than crash.

**What could prove us wrong.** For a demo or low-traffic deployment, in-memory state is simpler and fast. The external store is justified at production scale.

## ADR-008: Thread summarization over truncation or sliding window

**Decision.** When a conversation thread exceeds the context budget, summarize the older turns rather than truncating or using a sliding window.

**Alternatives considered.**
- (A) Truncate: drop the oldest messages to fit.
- (B) Sliding window: keep only the last N turns.
- (C) No limit: send the full thread every time.

**Why we rejected them.**
- (A) and (B) lose information. A customer who explained their problem in detail at the start of the thread and then went through several troubleshooting steps would have their original problem description dropped, forcing them to repeat themselves. That is a terrible support experience and a common failure in production agents.
- (C) works until it doesn't. Long threads eventually exceed the context window, and even before that, cost and latency grow linearly with thread length. A 50-turn thread with tool results in every turn can easily cost 10x a fresh request.

**Trade-offs accepted.** Summarization itself costs a model call and can lose nuance. A bad summary might drop a critical detail the customer mentioned early on. We mitigate this by logging the full thread in the trace so it can be recovered, and by evaluating summarization quality as part of the eval suite. We also accept that summarization adds latency on long threads.

**What could prove us wrong.** If context windows grow large enough and cheap enough that the full thread always fits comfortably, summarization is unnecessary complexity. Given current pricing and context limits, it is needed for production viability.

## ADR-009: PII detection at intake, not just at output

**Decision.** Detect and tag PII at the intake step before the request flows through the system, rather than only checking the final output.

**Alternatives considered.**
- (A) Detect PII only in the final response before it is sent to the customer.
- (B) No PII handling; leave it to the enterprise's existing data policies.

**Why we rejected them.**
- (A) catches PII leakage in the response but does not prevent PII from being logged in traces, passed to retrieval queries, or stored in conversation state. An enterprise audit would flag PII in internal logs even if it never reached the customer.
- (B) is a non-starter for any enterprise dealing with regulated data. The agent processes customer messages that routinely contain account numbers, email addresses, and sometimes payment details. Ignoring PII handling makes the system undeployable in any compliance-conscious environment.

**Trade-offs accepted.** Early PII detection adds processing to every request and introduces false positives (tagging non-PII as PII) and false negatives (missing actual PII). We tag rather than strip, so downstream components can make informed decisions, and we redact from logs and traces. The PII detector itself must be evaluated for precision and recall.

**What could prove us wrong.** If the enterprise already has a robust data-loss-prevention layer upstream of our system, our PII detection is redundant. We build it anyway because we cannot assume that layer exists.

## ADR-010: Confidence scoring for escalation over rule-based escalation

**Decision.** Use model-generated confidence scores combined with sensitivity detection to drive escalation decisions, rather than purely rule-based triggers.

**Alternatives considered.**
- (A) Rule-based only: escalate based on keyword matching, customer sentiment thresholds, or specific detected intents.
- (B) Always attempt resolution, escalate only on explicit user request.

**Why we rejected them.**
- (A) is brittle. Keyword lists miss novel cases, sentiment thresholds are noisy, and new edge cases require manual rule updates. Rules work well as one input but are not sufficient as the only input.
- (B) puts the burden on the customer to know when the agent is out of its depth. Customers do not always ask for a human; sometimes they just get a wrong answer and leave. The system should recognize its own uncertainty.

**Trade-offs accepted.** Model-generated confidence is not perfectly calibrated. The model may be confidently wrong or uncertain about something it could handle. We mitigate this by combining confidence with rule-based checks (certain intents always escalate regardless of confidence) and by tracking escalation precision in the eval suite to keep calibration honest over time.

**What could prove us wrong.** If confidence calibration proves too unreliable to be useful, we might fall back to a richer rule-based system that encodes more domain knowledge. The hybrid approach (confidence plus rules) is the hedge.

## ADR-011: Multiple independent escalation triggers over confidence-only escalation

**Decision.** The escalation gate evaluates nine independent triggers, not just confidence scoring. The triggers are documented inline in `concord/escalation/gate.py`: explicit human request, sensitivity (legal / security / churn / billing dispute), max turns, verifier rejection, knowledge gap, tool failure, cost budget, low confidence, and customer frustration. Hard triggers fire alone; soft triggers (confidence, frustration) only fire when two or more are present at once.

**Alternatives considered.**
- (A) Confidence-only escalation.
- (B) Rule-based keyword escalation only.
- (C) Let the specialist decide freely whether to escalate.

**Why we rejected them.**
- (A) fails because models can be confidently wrong. Confidence alone misses cases that should escalate for policy or sensitivity reasons (a high-confidence answer on a legal question is more dangerous than a low-confidence one, not less). Confidence is one signal among many, never the only signal.
- (B) is brittle. Keyword lists miss novel phrasings, new edge cases require manual rule updates, and adversarial users find the gaps. Rules work as a hard backstop for specific categories (the sensitivity triggers use rules), but cannot be the whole gate.
- (C) gives the specialist too much discretion and no hard backstops. A specialist that just generated a plan is biased toward shipping it; without an external escalation check, it can keep trying on cases it should hand off. The verification pass (ADR-002) and the escalation gate are both external checks for exactly this reason.

**Trade-offs accepted.** More triggers means more escalations overall. Some will be false positives, especially from the soft triggers (confidence and sentiment). We accept a higher escalation rate in exchange for never missing a case that should have been escalated. The metrics dashboard tracks escalation precision (were the escalated cases actually the hard ones) so we can spot over-escalation drift early.

**What could prove us wrong.** If the combined false-positive rate is so high that the human queue is overwhelmed with easy cases, we need to tune thresholds or reduce the soft trigger set. Most likely first lever: raise the confidence escalate threshold, or require two soft signals together rather than one.

---

## Trade-offs and Known Limitations

These are the honest gaps. Things we know are imperfect, things we chose not
to solve in this version, and things a reviewer should ask about. Having
the answers here is the difference between "I didn't think about it" and
"I made a deliberate choice."

**Latency.** The multi-agent topology, retrieval, and verification pass each add round-trips. Total latency for an action-taking request is meaningfully higher than a monolithic agent. We mitigate with parallelism and streaming, but end-to-end latency will be higher than a simpler system. We chose correctness and safety over speed.

**Cost.** The tiered model strategy and per-request budget control costs, but the verification pass and the retrieval call make each request more expensive than a single model call. We chose auditability and safety over cost minimization. The cost dashboard lets us monitor and optimize.

**Complexity.** This system has more moving parts than a monolithic agent. More components means more things that can fail, more configuration to maintain, and more for a new developer to learn. We chose modularity, testability, and clear boundaries over simplicity. The repo structure and documentation exist to manage that complexity.

**Retrieval quality ceiling.** RAG is only as good as the chunking, embedding, and ranking pipeline. Poorly chunked docs or a weak embedding model will produce bad answers regardless of how good the specialist agent is. Retrieval must be evaluated and maintained as a separate concern.

**Eval coverage.** 150+ cases is a reasonable starting suite but does not cover the full distribution of real customer requests. The eval suite will have blind spots. We mitigate by adding new cases as we discover failures, and by logging production requests (with PII redacted) as candidates for the eval set.

**Single-vendor model dependency.** The system uses one model provider. If that API has an outage, the entire system degrades to the human queue. A production enterprise system might add a fallback model provider, but that adds significant complexity.

**No real customer data.** The demo uses synthetic data. Real enterprise deployment would require integration with actual CRM, ticketing, and knowledge systems, plus data governance review. The MCP server architecture makes this integration straightforward, but the demo is not a full production deployment.

**Summarization fidelity.** Thread summarization can lose important details. We log the full thread in the trace for recovery, but the agent only sees the summary. If the summary drops a critical detail, the agent may ask the customer to repeat themselves. This is a known imperfect trade-off between context budget and information preservation.
