# Integrations and SDKs

## Supported SDKs

Acme maintains official SDKs for:

- Python (3.9+): `pip install acme-sdk`
- Node.js (18+): `npm install @acme/sdk`
- Go (1.21+): `go get github.com/acme/sdk-go`
- Ruby (3.0+): `gem install acme-sdk`

Community SDKs exist for Rust, Java, and PHP but are not officially supported.

## Webhooks

Webhook deliveries retry on failure with exponential backoff for up to 24
hours. After 24 hours of failures, the webhook endpoint is disabled and the
account owner is notified.

Webhook signatures are HMAC-SHA256 over the raw body, using the per-endpoint
signing secret. Always verify signatures before processing.

## Common integration issues

### "Webhooks are not being delivered"

Checklist:

1. Confirm the endpoint URL is reachable from the public internet (no
   localhost, no internal-only DNS).
2. Confirm the endpoint returns HTTP 2xx within 10 seconds. Anything slower
   counts as a failure.
3. Check **Settings -> Developers -> Webhooks -> Delivery Log** for the
   per-attempt response body. Acme stores the last 72 hours.

### "I can't see events in the dashboard"

Events may take up to 60 seconds to appear in the dashboard due to indexing
lag. API queries see events immediately. If events are missing after 5 minutes,
check that the API key has the `read:events` scope.

### "SSO login broke after we changed our IdP cert"

Customer must upload the new certificate at **Settings -> Security -> SSO ->
Certificate**. Until the new certificate is uploaded, SSO logins will fail
with `invalid_signature`. Local-account logins for owners still work as the
break-glass path.
