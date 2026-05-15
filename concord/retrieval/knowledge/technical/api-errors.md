# Common API Errors

## 401 Unauthorized

The API key is missing, malformed, or revoked. Resolutions:

1. Confirm the `Authorization: Bearer <key>` header is present and not empty.
2. Confirm the key has not been rotated. New keys can be created at
   **Settings -> Developers -> API Keys**.
3. Confirm the key matches the workspace the customer is targeting. Keys are
   workspace-scoped.

## 403 Forbidden

The key is valid but lacks permission for the requested resource. Common
causes: the key belongs to a member with restricted role, the workspace does
not have the feature enabled on its plan, or the resource belongs to a
different workspace.

## 429 Rate Limited

Burst limit: **100 requests/second per key**. Sustained limit: **10k requests/
minute per workspace**. Enterprise plans can request higher limits via sales.

When rate-limited, the response includes `Retry-After` (seconds) and
`X-RateLimit-Reset` (epoch). Clients should back off exponentially.

## 500 Internal Server Error

Transient failure on our side. Resolutions:

1. Retry once after 1 second. If a single retry succeeds, no action needed.
2. If repeated 500s on a specific endpoint, the on-call team has likely been
   paged automatically. Customer can check **status.acme.example** for
   ongoing incidents.

## 503 Service Unavailable

Indicates ongoing maintenance or a saturation event. Treat like a 500 with
longer backoff (5-30s) and a check of the status page.

## Idempotency

State-changing API calls accept an `Idempotency-Key` header. If a request is
retried with the same key within 24 hours, the original response is returned.
Always set idempotency keys on POST/PATCH/DELETE in production integrations.
