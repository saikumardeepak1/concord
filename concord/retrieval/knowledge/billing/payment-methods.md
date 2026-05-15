# Payment Methods

## Accepted methods

Acme accepts the following payment methods on all paid plans:

- **Credit and debit cards**: Visa, Mastercard, American Express, Discover, JCB.
  Cards are processed by our PCI-DSS Level 1 payment processor. We do not
  store full card numbers on our systems.
- **ACH bank transfer (US only)**: available on annual plans.
- **SEPA direct debit (EU only)**: available on annual plans.
- **Wire transfer**: available for Enterprise customers paying $10,000+ annually.
- **Purchase order (PO) billing**: available for Enterprise customers; requires
  signed order form and a credit check.

We do not currently accept PayPal, cryptocurrency, Apple Pay, or Google Pay
for subscription billing.

## Switching payment methods

Customers can switch payment methods from **Settings -> Billing -> Payment
Method** at any time. The new method is charged on the next billing cycle.

## Switching from credit card to PO / wire / ACH

This requires a manual setup by the billing team:

1. Customer contacts billing with their preferred method.
2. Billing issues a new order form referencing the new method.
3. Once signed, future invoices route to that method.
4. Pre-paid period on the original method is honored; no refund/no double-charge.

## Failed payments

If a payment fails, we retry on a fixed schedule: day 1, day 3, day 7. The
billing contact receives an email after each failure. After three failed
attempts the account moves to `past_due` and feature access is restricted on
day 14 if still unpaid. The customer can update their payment method at any
point during this window to avoid restriction.
