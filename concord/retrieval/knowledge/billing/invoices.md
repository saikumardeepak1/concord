# Invoices and Receipts

## Where to find invoices

All invoices are available under **Settings -> Billing -> Invoices**. Each
invoice can be downloaded as PDF or CSV. The billing contact on the account
receives a copy by email within 24 hours of the charge.

## Invoice line items explained

A typical Acme invoice contains:

- **Subscription**: base plan charge for the billing period.
- **Seats**: per-seat charges for users above the base allocation.
- **Usage**: metered usage (API calls, storage, processed events).
- **Add-ons**: optional features (SSO, audit log retention, premium support).
- **Credits applied**: prior credits, service-credit refunds, promotional codes.
- **Taxes**: sales tax or VAT, computed by jurisdiction.

## Common questions

### "Why is my invoice higher this month?"

The three most common causes:

1. **Seat increase**: new users were invited and accepted between billing
   periods. Prorated charges appear for partial-month additions.
2. **Usage overage**: metered usage crossed an included tier threshold.
3. **Annual-to-monthly conversion**: switching plans mid-cycle prorates.

### "Why are taxes on the invoice?"

Acme charges sales tax in jurisdictions where required by law. Customers with
a valid tax exemption certificate can submit it via the support portal; once
approved, future invoices will not include tax for that jurisdiction.

### "Can I change the billing contact?"

Yes. The current billing contact or any account owner can change the billing
contact under **Settings -> Billing -> Contacts**. There is no charge for
this change.

## Disputing an invoice

If a customer believes an invoice is incorrect, the support agent should:

1. Pull the invoice and confirm the disputed line item.
2. Cross-check the customer's account events log (seat changes, usage records).
3. If there is a clear error, issue a corrective credit or refund per the
   refund policy.
4. If there is no clear error, explain the calculation and offer the customer
   the option to escalate to a billing specialist.
