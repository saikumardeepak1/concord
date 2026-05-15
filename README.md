# Concord

> Enterprise multi-agent customer support operations platform built on Claude.
> Production-grade architecture: governed actions, independent verification,
> retrieval-grounded answers, full audit trail, evals, and observability.

Concord ingests inbound customer support requests, pulls answers from internal
knowledge, takes real state-changing actions through a governed tool layer,
escalates to humans when the case is hard or sensitive, and emits a complete
trace for every request.

It is not a chatbot. It is a reference implementation of the architecture an
enterprise would need to safely deploy a Claude-powered agent in front of
real customers.

---

## What's inside

```
concord/
├── intake/         normalize inbound text, detect & redact PII, summarize long threads
├── router/         cheap classifier — intent, sensitivity, urgency
├── specialists/    billing / technical / account agents, scoped tools + scoped KB
├── retrieval/      Chroma-backed RAG with markdown-aware chunking
├── actions/        permission checks + independent verification + audit log
├── escalation/     hybrid confidence + rule-based human handoff
├── synthesis/      leakage scrub, tone normalization, final response assembly
├── observability/  per-request traces + Prometheus metrics
├── mcp_servers/    retrieval & actions exposed over MCP (stdio)
└── api.py          FastAPI app, live trace viewer, /metrics, /healthz
evals/              eval harness + 150+ graded cases (happy/edge/adversarial/escalation)
tests/              deterministic unit tests (no model calls)
web/                single-page demo UI with live trace panel
docs/               architecture overview, runbook, ADRs
```

## The architecture in one diagram

```
                                                ┌───────────────────┐
                                                │  Knowledge base   │
                                                │  (markdown docs)  │
                                                └────────┬──────────┘
                                                         │ chunk + embed
                                                         ▼
   ┌────────┐    ┌────────┐    ┌────────────┐    ┌──────────────┐
 ──▶ Intake ├───▶ Router ├───▶ Specialist  ├───▶│  Retrieval   │
   │  PII   │    │ fast   │    │  scoped    │    │  (Chroma)    │
   └────────┘    └───┬────┘    └──────┬─────┘    └──────────────┘
                    │                  │
                    │ explicit human / │ proposes action
                    │ sensitive case   ▼
                    ▼            ┌──────────────────────────────────┐
              ┌──────────┐       │   Action Service                  │
              │Escalation│◀──────┤   ① schema validate                │
              │  Gate    │       │   ② permission predicate          │
              └────┬─────┘       │   ③ INDEPENDENT verification pass │
                   │             │   ④ idempotency check             │
                   ▼             │   ⑤ execute with timeout          │
              human queue        │   ⑥ append-only audit log         │
                                  └──────────────────────────────────┘
                                                │
                                                ▼
                                        ┌──────────────┐
                                        │  Synthesis   │
                                        │ leakage scrub│
                                        └──────┬───────┘
                                                ▼
                                      customer response
                                      + structured trace
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full walkthrough and
[the project brief](docs/PROJECT_BRIEF.md) for the architectural decision
records that drive every choice.

## Quick start

### 1. Get an Anthropic API key

You need a key with access to Claude Haiku 4.5, Sonnet 4.6, and Opus 4.7.
(The model IDs are in `.env.example` and configurable — see ADR-003.)

### 2. Install + configure

```bash
git clone <your fork> concord && cd concord
cp .env.example .env                  # then put your key in .env
pip install .                          # or: pip install .[dev] for tests
```

### 3. Index the knowledge base, run the server

```bash
concord index                          # one-time, builds the Chroma index
concord serve                          # binds 0.0.0.0:8080
```

Open <http://localhost:8080> for the demo UI with the live trace panel.

### 4. Try a single request from the CLI

```bash
concord ask "I was charged twice for my Pro subscription — please refund the duplicate"
```

### 5. Run the eval suite

```bash
concord evals --suite all
# pass: 142 / 153
#   adversarial: 25/25     perfect — zero successful prompt injections / policy bypasses
#   edge:        34/35     gibberish, multi-intent, PII, frustrated customers all handled
#   escalation:  34/35     legal, security, churn risk, big refunds all caught
#   happy_path:  49/58     most "failures" are wording mismatches or
#                          design-correct clarifying behavior, not real bugs
```

The composition matters more than the headline number. Concord is calibrated
to refuse adversarial input perfectly and to escalate the right cases
reliably. On routine happy-path resolution, the agent asks a clarifying
question when the customer's request lacks specifics (refund without amount,
403 error without endpoint), which is what makes it safe to deploy. Several
"happy_path failures" are the eval expecting `outcome=resolved` when
`outcome=clarifying` is the correct support-agent behavior.

## Run with Docker

```bash
docker compose up --build
# UI: http://localhost:8080
# metrics: http://localhost:8080/metrics
# traces: http://localhost:8080/traces
```

## Connect Concord's MCP servers to any MCP-compatible client

Concord ships two MCP servers (stdio transport):

```jsonc
// .mcp.json or Claude Desktop config
{
  "mcpServers": {
    "concord-retrieval": {
      "command": "python",
      "args": ["-m", "concord.mcp_servers.retrieval_server"]
    },
    "concord-actions": {
      "command": "python",
      "args": ["-m", "concord.mcp_servers.actions_server"]
    }
  }
}
```

Once configured, any MCP client (Claude Code, Claude Desktop, a custom agent)
can search the knowledge base and take governed actions — subject to the same
permission, verification, and audit pipeline as the orchestrator itself. There
is no privileged path.

## Production deployment

The system is built to deploy without exotic infrastructure. The defaults run
on a single host with SQLite and a local Chroma index. For real workloads:

| Concern              | Default (demo)        | Production swap                          |
|----------------------|-----------------------|------------------------------------------|
| Conversation state   | SQLite + aiosqlite    | Postgres (change `CONCORD_DB_URL`)       |
| Vector store         | Chroma persistent     | Chroma server / Pinecone / pgvector      |
| Embeddings           | sentence-transformers | Voyage / Cohere / OpenAI                 |
| Metrics              | /metrics scrape       | Prometheus + Grafana                     |
| Traces               | SQLite, served at /traces | OpenTelemetry exporter (drop in)     |
| Secrets              | .env                  | Cloud secret manager                     |
| Identity for tools   | mock backend          | Your CRM / billing / identity provider   |

All of these are config-driven boundaries, not code rewrites. Replacing the
mock action backend is the only deployment-specific work — wire each tool
handler in `concord/actions/tools.py` to your real systems.

## How a request actually flows

1. `POST /support` arrives → `Concord.handle_request`.
2. **Intake** normalizes text, detects PII, redacts, summarizes long threads.
3. **Router** (fast tier) emits a typed `RoutingDecision`.
4. **Early escalation gate** catches explicit human requests and legal/security
   sensitivity before any specialist is invoked.
5. **Specialist** (standard tier) retrieves scoped passages, drafts a response,
   proposes 0+ tool calls, and self-rates confidence.
6. **Action Service** runs each proposed tool through five gates: schema,
   permission predicate, independent verification (high tier, no context
   from the specialist), idempotency check, execution, audit-log append.
7. **Escalation gate** catches low-confidence or specialist-requested handoff.
8. **Synthesizer** scrubs internal-leakage tokens, normalizes tone, attaches
   action results.
9. Trace persisted, metrics incremented, response returned with citations.

Every step opens a span on the request's trace. The web UI surfaces it live.

## Why the choices are what they are

Every major decision has an ADR with alternatives considered, rejection
reasons, and what could prove it wrong. See `docs/PROJECT_BRIEF.md` sections
4 and 7. Highlights:

- **Router + specialists, not monolith.** Attention dilution and permission
  boundaries argue against one big agent.
- **Independent verification pass.** A model that just proposed a plan is a
  poor reviewer of that plan; a separate instance with no reasoning context
  is far more reliable. This is the multi-instance review pattern, applied
  to the riskiest step.
- **Tiered models.** Routing is a fast/cheap call; specialist reasoning is
  mid-tier; verification of high-impact actions is opus-tier. Model IDs
  live in config, never hardcoded.
- **MCP for tools, not inline functions.** Retrieval and actions are reusable
  by any MCP client. Adds protocol overhead; that overhead pays for itself
  in testability and reusability as tool count grows.
- **Escalation as a first-class outcome.** We measure escalation precision,
  not escalation rate. A wrong answer is more expensive than a slow one.

## License

MIT.

## Acknowledgements

Built against the architecture defined in `docs/PROJECT_BRIEF.md`. The brief
is the source of truth; the code follows it.
