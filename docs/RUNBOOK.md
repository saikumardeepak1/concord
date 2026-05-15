# Concord Runbook

Common failure modes and what to do. The system is designed to fail into the
human queue rather than fail on the customer, so the runbook focuses on
detection and remediation rather than emergency triage.

## Health

- **Liveness**: `GET /healthz` returns `{"status":"ok"}`. If it 500s, the
  process is hung; restart.
- **Metrics**: `GET /metrics` exposes Prometheus counters. Scrape every 15s.
- **Traces**: `GET /traces?limit=100` returns the most recent request traces.
  `GET /traces/{trace_id}` returns one in full.

## Alarms to wire

| Symptom                          | Metric                                      | What it means |
|----------------------------------|---------------------------------------------|----------------|
| Escalation rate spike            | `concord_escalations_total{reason}` rate    | Confidence calibration drift or KB stale |
| Verification deny rate spike     | `concord_verification_outcomes_total{approved="false"}` | Specialists proposing out-of-policy actions |
| Tool error rate                  | `concord_tool_calls_total{result!~"success|replay"}` | Backend integration broken |
| p95 stage latency > 5s           | `concord_stage_latency_ms` histogram        | Model API slow or retrieval slow |
| Cost-per-resolution drift        | `rate(concord_cost_micro_usd_total) / rate(concord_requests_total)` | Tiering misconfigured or thread bloat |

## Common failures

### Anthropic API rate limited

- Symptom: spans named `llm.complete` failing with `RateLimitError` after retries.
- Behavior: `LLMClient` retries with exponential backoff up to 4 attempts.
  Sustained rate-limits surface as `LLMError` and bubble up.
- Mitigation: lower `CONCORD_MAX_TOKENS_PER_REQUEST`, or shift traffic by
  scaling specialists down to `model_standard`, or raise API quota with
  Anthropic.

### Chroma index corruption / disk full

- Symptom: `retrieval.query` spans erroring; specialist falls back to
  zero-passage answers (which triggers self-escalation).
- Mitigation:
  ```bash
  rm -rf $CONCORD_CHROMA_PATH
  concord index
  ```
  Index is reproducible from `concord/retrieval/knowledge/`.

### Verification false positives (legitimate actions denied)

- Symptom: `verification_outcomes_total{approved="false"}` rising for cases
  the team agrees should pass.
- Diagnosis: inspect `audit_log.verification_rationale` for the affected
  cases; look for specific phrases the verifier is misreading.
- Mitigation: tune the verifier system prompt in
  `concord/actions/verification.py`. Add eval cases for the false positives
  before changing prompts.

### Specialists returning low confidence on documented topics

- Symptom: high escalation rate from `low_confidence`, traces show retrieval
  hits but specialist still escalates.
- Diagnosis: open a few traces, check whether retrieved passages actually
  cover the question. Likely chunks too small or scope filter wrong.
- Mitigation: adjust `CONCORD_RETRIEVAL_CHUNK_CHARS` / `..._OVERLAP`, or
  expand the knowledge base. Re-run `concord index`.

### PII leaking into traces

- Symptom: `traces.payload` contains email/phone strings.
- Mitigation: this should never happen — intake redacts before anything else.
  If it does, add the failing pattern to `concord/intake/pii.py::_PATTERNS`
  with a regression test in `tests/test_pii.py`.

## Recovery — partial action failures

`ActionService` uses idempotency keys. If a customer reports an action did or
did not happen and the audit log disagrees, the truth is the audit log. To
manually replay a denied action after a policy change:

```sql
-- find the entry
SELECT id, tool_name, arguments, verification_rationale
FROM audit_log
WHERE customer_id = '...' AND occurred_at > date('now', '-1 day');

-- manually re-execute with the same idempotency_key from the API
```

A future enhancement: a `concord replay --audit-id N` CLI. Not implemented yet
because manual replay is rare; document it here so it does not become a
forgotten gap.

## Deploys

- The container is stateless except for the mounted `chroma` volume and
  `data/` (SQLite). Rolling restart is safe — in-flight requests will time
  out cleanly because every external call has a deadline.
- Knowledge updates: bump the file, deploy, `concord index` runs on startup
  (idempotent upsert). Old chunks remain queryable until they are upserted.
- For zero-downtime knowledge swaps, run `concord index --path .new/` to a
  staging path, then update `CONCORD_CHROMA_PATH` and restart.
