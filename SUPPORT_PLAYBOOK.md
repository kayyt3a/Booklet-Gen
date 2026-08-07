# FolioAI customer support playbook

This is the operating procedure for a solo paid beta. It keeps support actions
consistent without exposing secrets or making promises the product cannot keep.

## Service standard

- Monitor the support inbox every business day.
- Aim to acknowledge messages within two business days.
- Never ask a customer to send a password, full card number, Stripe secret,
  identity document, database URL, or API key.
- Verify the account using the email address on the FolioAI account.
- Record the date, account email, job id if available, category, action, and
  outcome in the private support log.
- Do not include a child's full name in the support log. Use the job id and
  booklet label only when necessary.

## Booklet generation failed

1. Confirm the job status in the admin console.
2. Confirm the reserved credit was returned automatically.
3. Read the internal error without copying it into the customer reply.
4. If the cause appears transient, use the no-charge retry once.
5. If it fails again, grant one support credit and escalate the defect.
6. If a paid service cannot be supplied, offer the appropriate refund.

Reply template:

> Thanks for letting us know. The failed generation has not cost you a booklet
> credit. I have checked the job and [queued a no-charge retry / added a
> replacement credit]. I will update you if anything else is needed.

## Booklet quality problem

Quality problems include a materially wrong answer, unsafe content, unreadable
layout, missing pages, or content substantially unrelated to the requested
year, subject, or topic.

1. Ask for the job id or exact booklet title and approximate generation time.
2. Do not ask the customer to email a booklet containing a child's full name if
   the stored job can be inspected instead.
3. Confirm the issue against the stored file.
4. Grant one replacement credit or supervised retry.
5. Mark the issue for a regression test before changing generator code.
6. If the replacement also fails, or the original failure is major, offer a
   refund for the affected paid credit.

Reply template:

> I am sorry this booklet was not usable. I have added a replacement credit to
> your account and recorded the defect for correction. If the replacement has
> the same problem, reply to this message and I will arrange the appropriate
> refund.

## Payment completed but credits are missing

1. Ask only for the FolioAI account email and the Stripe receipt email or
   checkout receipt identifier. Never request card details.
2. Search the payments list and Stripe dashboard.
3. If Stripe shows the payment succeeded, replay the signed webhook.
4. Confirm exactly one payment row and one ledger grant exist.
5. If fulfilment still fails, grant the purchased credits using a support
   reference, then investigate before another sale.

Reply template:

> I found the completed payment and have restored the missing credits. No card
> details are needed. Please refresh your Account page and reply if the balance
> is still not correct.

## Duplicate charge or refund request

1. Confirm the Stripe payment and FolioAI payment row.
2. Check whether any purchased credits were used.
3. Never describe a refund as unavailable where Australian Consumer Law may
   require a remedy.
4. Issue approved refunds to the original payment method through Stripe.
5. Record the Stripe refund id in the private support log.
6. Confirm the credits came back on their own. A Stripe refund or chargeback
   reverses the credits it granted automatically, in proportion to the amount
   returned, and the payment row moves to refunded, partially_refunded or
   disputed. Check the balance rather than assume it.

A balance can be negative. That is correct when a customer generated booklets
and the money then went back, and it stops the account generating again until
it is settled. Do not zero it out to make it look tidy.

Use the admin credit adjustment, positive or negative, for the cases the
webhook cannot judge:

- a dispute closed in our favour, where the credits should be restored
- a refund issued outside Stripe
- a correction to an earlier adjustment

A removal must state a specific reason, such as the Stripe refund or dispute
id. The reason and the admin account are written into the credit ledger and
the server log, so the adjustment stays auditable afterwards. Do not edit the
database by hand: the ledger is the balance, and a direct edit leaves no
record of who made it or why.

## Account access and privacy

- Direct password problems to the reset flow.
- Direct data requests to Account export.
- Account deletion is self-service, but warn the customer to resolve payment
  issues first.
- Escalate suspected account compromise, exposed secrets, unexpected data
  access, or a lost private file as a security incident.
- Preserve relevant logs and rotate affected secrets. Do not delete evidence
  while investigating.

## Incident levels

### Critical

- another customer can access an account or booklet
- a secret key is exposed
- payments are granted to the wrong account
- widespread harmful or restricted content is generated

Stop new generation or payments if needed, preserve evidence, rotate affected
secrets, and obtain professional incident advice.

### High

- the worker is unavailable for more than 15 minutes
- repeated payment fulfilment failures
- generated files cannot be downloaded after successful jobs
- failure rate exceeds 10 percent over the latest 20 jobs

Pause invitations, fix the service, and complete the beta acceptance run again.

### Normal

- one failed generation with an automatic credit return
- one quality complaint
- one delayed email or expired reset link

Follow the relevant procedure and record the outcome.
