# FolioAI founder-only launch checklist

This list contains only work that requires your identity, your accounts, your
money, or a business decision from you. Codex can prepare and test the product,
but it cannot truthfully complete these items for you. Do not paste passwords,
secret keys, banking details, identity documents, or full database URLs into a
chat or commit them to Git.

## Decide the offer

- [ ] Read and approve or amend `LAUNCH_OFFER.md`. It now contains a concrete
  recommendation for prices, the welcome credit, refund handling, launch
  products, and beta limits.

## Establish the seller

- [ ] Choose the legal business or sole-trader name that will sell FolioAI.
- [ ] Obtain or confirm the ABN and decide whether GST registration is required.
- [ ] Choose a public business contact address, support email, and optional
  support phone number.
- [ ] Open or nominate the bank account that will receive Stripe payouts.
- [ ] Ask an Australian accountant which records, GST wording, and tax settings
  apply to your actual circumstances.

## Approve the legal position

- [ ] Have the Privacy Policy, Terms, refund wording, and business disclosures
  reviewed for your real operator details and intended customers.
- [ ] Decide the minimum age and whether a parent or guardian must own every
  account. The current product is designed for adults purchasing for children.
- [ ] Approve the clean-room policy: no third-party assessment or textbook RAG,
  no production migration of the old vector store, and no externally sourced
  worksheet images.

## Configure accounts only you control

- [ ] Complete Stripe business and identity verification, add payout details,
  and configure customer-facing business information.
- [ ] Create Stripe test-mode prices and the FolioAI webhook. After the full test
  passes, repeat this in live mode.
- [ ] Choose a transactional email provider, verify the sender domain, and add
  the provider's SPF and DKIM records to DNS.
- [ ] In Supabase, create a private `booklets` Storage bucket and turn on an
  appropriate backup or point-in-time recovery plan.
- [ ] In Render, create or confirm the paid web service and background worker,
  then enter the production environment variables from `DEPLOY.md`.
- [ ] Register or confirm the public domain and connect its DNS records to
  Render after the launch environment is stable.
- [ ] Turn on billing and failure alerts in Render, Supabase, Stripe, Gemini,
  and the email provider.

## Personally accept the launch

- [ ] Use a fresh disposable account to complete signup, email verification,
  one normal booklet, one term plan, download, password reset, data export, and
  account deletion.
- [ ] Complete a Stripe test purchase and confirm exactly the expected credits
  appear, including after refreshing the success page and replaying the webhook.
- [ ] Make one small live purchase with your own payment method after Stripe is
  switched to live mode, then confirm the payout and refund flow.
- [ ] Read several generated PDFs as a parent and print at least one on an
  ordinary home printer.
- [ ] Invite a small group of real parents or tutors using `PAID_BETA.md` and
  personally decide whether the recorded results are good enough for launch.
- [ ] Commit to checking the support inbox and provider alerts on a schedule you
  can realistically maintain.

The operational walkthrough for these items is in `DEPLOY.md`. Keep this file
as the short decision list and use the deployment guide while working inside
each provider dashboard.
