# Access, Roles, and Account Management

## Roles

Acme accounts have four roles:

- **Owner**: full control, including billing and account deletion. There must
  always be at least one owner per workspace.
- **Admin**: full control except billing and account deletion.
- **Member**: access to product features per the plan, no settings access.
- **Read-only**: view-only access. Useful for auditors and observers.

## Adding and removing users

Owners and admins can invite users from **Settings -> People -> Invite**.
Invited users receive an email with a join link valid for 7 days.

Removing a user revokes their session immediately. Any API keys created by
that user are also revoked. Workspace-level API keys are not affected.

## Password and login issues

### "I forgot my password"

The customer should use the **Forgot password** link on the login page.
Reset emails are sent only to verified addresses. If the customer no longer
has access to the verified email, ownership of the address must be proven
through a separate identity-verification process; support agents must not
bypass this.

### "I'm locked out after too many attempts"

Accounts are locked for 15 minutes after 10 failed login attempts. The lock
clears automatically. Support can override this with manager approval only;
silent overrides are not permitted.

### "MFA device is lost"

If the user has SSO, log in via SSO. If not, account owners can reset MFA for
a member from **Settings -> People -> [user] -> Reset MFA**. The user must
re-enroll on next login.

## Account closure and data deletion

Customers can request data deletion under GDPR / CCPA from
**Settings -> Account -> Privacy**. Deletion is processed within 30 days. Some
data (financial records, audit logs) is retained for the legally required
period and cannot be deleted on request; this is disclosed in the privacy
policy.

GDPR or CCPA deletion requests are **sensitive**: if a support agent receives
one outside the self-serve flow, it must be escalated to the privacy team
with the customer's verified identity, not processed by general support.
