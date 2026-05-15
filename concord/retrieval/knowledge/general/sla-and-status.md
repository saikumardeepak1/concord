# SLA, Status, and Incident Response

## Service-Level Agreement

The Acme SLA applies to Enterprise customers. Key terms:

- **Uptime target**: 99.9% monthly.
- **Measurement window**: rolling calendar month.
- **Exclusions**: scheduled maintenance announced 7 days in advance,
  force-majeure events, customer-caused outages.

If uptime falls below 99.9% in a given month, the customer is entitled to a
service credit:

- 99.0% to 99.9%: 10% of monthly subscription fee.
- 95.0% to 99.0%: 25% of monthly subscription fee.
- Below 95.0%: 50% of monthly subscription fee.

Credits must be claimed within 30 days of the incident via the support portal
and are issued as account credits, not cash refunds.

## Status page

Real-time and historical incident status is published at
**status.acme.example**. Customers can subscribe to notifications by email,
SMS, RSS, or webhook.

## During an active incident

Support agents should:

1. Acknowledge the customer immediately.
2. Link to the status page incident.
3. Avoid speculating about ETA; use the engineering ETA from the incident
   record if and only if one has been published.
4. Tag the conversation with the incident ID so all affected customers can be
   batched for follow-up communication.
5. **Do not promise SLA credits during the incident.** Credits are calculated
   after the incident's impact and duration are confirmed.
