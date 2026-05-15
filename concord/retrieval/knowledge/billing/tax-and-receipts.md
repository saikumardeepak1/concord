# Tax, Receipts, and Custom Invoice Fields

## Customizing the invoice

Customers can add the following fields to all future invoices from
**Settings -> Billing -> Invoice Details**:

- **Company legal name**: appears as the bill-to.
- **Tax ID / VAT number**: required for B2B invoicing in many jurisdictions
  (EU VAT, UK VAT, AU ABN, IN GSTIN, BR CNPJ). Once entered and validated,
  it appears on every invoice and may exempt the customer from sales tax
  where the reverse-charge mechanism applies.
- **Billing address**: separate from the workspace's primary address.
- **Purchase order (PO) number**: appears as a reference field on the invoice.

Changes apply to the next invoice; past invoices are not retroactively updated.
Customers needing a corrected past invoice should contact support and the
billing team will reissue.

## Tax exemption

US customers with a valid tax exemption certificate (501(c)(3), educational
institutions, government entities, resellers) can submit it via the support
portal. Approval takes 2-3 business days. Once approved, future invoices in
qualifying jurisdictions are not taxed.

EU and UK customers with a valid VAT number entered at **Settings -> Billing**
will have the reverse-charge mechanism applied automatically; their invoices
show 0% VAT with a reverse-charge notation.

## Resending receipts

Past invoices and receipts are always available at **Settings -> Billing ->
Invoices**. The billing contact can also request an email resend for a
specific invoice from the same page.

If the customer doesn't have access to the billing portal (e.g. they were the
billing contact but their account was removed), support can email a copy of
any invoice to a verified email on file. Verification means: confirming the
requester is the current billing contact OR an account owner.

## Common questions

### "Why is there tax on my invoice this month but not last month?"

Three usual causes:

1. Your billing jurisdiction changed (new billing address).
2. The tax rate in your jurisdiction changed.
3. Your tax exemption certificate expired (they typically have a renewal
   date) or the exempt status was removed.

The invoice PDF includes a tax line item with the jurisdiction code; this
makes it auditable.

### "Can I get a receipt that doesn't show my coworker's name?"

Yes. The invoice bill-to is set at **Settings -> Billing -> Invoice Details**.
By default it shows the workspace's billing contact. Customers who want a
company name only can set the company legal name there.
