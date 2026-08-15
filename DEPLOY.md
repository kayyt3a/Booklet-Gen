# Deploying FolioAI for paying customers

FolioAI runs as two Render services that share one Supabase Postgres database:

1. The web service handles accounts, Stripe Checkout, and customer pages.
2. The background worker claims queued jobs and generates the files.

Generated files can be stored in a private Supabase Storage bucket. Stripe
handles card entry, receipts, and payment authentication. An SMTP provider
delivers account verification and password-reset email.

## 1. Verify locally

In PowerShell:

```powershell
cd "C:\Users\User\OneDrive\Documents\Booklet-Gen"
Copy-Item .env.webapp.example .env
notepad .env
.venv\Scripts\pip.exe install -r requirements.txt
.venv\Scripts\python.exe -m booklet_gen.webapp
```

Open `http://127.0.0.1:5000`. Local development uses inline generation when
`FOLIO_JOB_MODE=auto`, which generates in-process when no worker is running. Stop the server with Ctrl+C.

Before changing a beta or live deployment, audit a prepared environment file
without contacting any provider. The report prints setting names but never
their secret values:

```powershell
.venv\Scripts\python.exe scripts\launch_readiness.py --stage beta --env-file .env
.venv\Scripts\python.exe scripts\launch_readiness.py --stage live --env-file .env
```

## 2. Prepare Supabase

Use the existing Supabase project that holds FolioAI accounts and RAG vectors.

1. In Supabase, open Storage and create a bucket named `booklets`.
2. Keep the bucket private. Do not add a public read policy.
3. Copy the Project URL and the service-role key from project API settings.
4. Keep the service-role key only in Render environment variables. Never put
   it in browser code, Git, or a public screenshot.
5. Keep the session-pooler Postgres URL as `DATABASE_URL` on both Render
   services.

The web app creates short-lived signed download URLs after confirming the
logged-in account owns the booklet. If Storage is temporarily unavailable,
new files fall back to the database.

Do not copy the existing local RAG library to Postgres. It contains past
assessment material that has not been approved for commercial app use. Build a
fresh store from sources recorded as commercially approved under
`rag_sources/README.md`. Only after that review, migrate the clean store from
the repo with `DATABASE_URL` set in the current PowerShell session:

```powershell
.venv\Scripts\python.exe scripts\migrate_rag_to_postgres.py --dry-run
.venv\Scripts\python.exe scripts\migrate_rag_to_postgres.py
```

Never run this migration against the old local paper archive. Only upload
material you have the documented right to use for this public service.
The migration now enforces this boundary by rejecting every vector source that
lacks approval provenance from `rag_sources/source_rights.csv`.

## 3. Configure transactional email

Create an account with an SMTP provider such as Postmark, Brevo, Mailgun, or
Amazon SES. Verify a sender address or, preferably, your domain. Add the SPF
and DKIM DNS records the provider supplies.

Collect these values:

- `FOLIO_EMAIL_FROM`, for example `FolioAI <hello@yourdomain.com>`
- `SMTP_HOST`
- `SMTP_PORT`, normally `587`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_STARTTLS=1`

Leave `FOLIO_REQUIRE_EMAIL_VERIFICATION=0` until the credentials are tested.
Set it to `1` before opening public signups.

## 4. Configure Stripe in test mode

Complete Stripe account and business verification first. Then in Stripe test
mode:

1. Create a one-time product for one booklet, for example A$5.00.
2. Create a one-time product for a 10-week term plan, for example A$35.00.
3. Copy each `price_...` identifier into `STRIPE_PRICE_SINGLE` and
   `STRIPE_PRICE_TERM`.
4. Copy the test secret key into `STRIPE_SECRET_KEY`.
5. Add a webhook destination using
   `https://YOUR-HOST/stripe/webhook`.
6. Subscribe it to `checkout.session.completed` and
   `checkout.session.async_payment_succeeded`.
7. Copy its `whsec_...` signing secret into `STRIPE_WEBHOOK_SECRET`.

Keep `FOLIO_REQUIRE_PAYMENTS=0` until a test Checkout completes and the credit
appears once in Account. Then set it to `1`.

Before launch, repeat the product, price, secret-key, and webhook steps in
Stripe live mode. Test and live IDs cannot be mixed. Configure Stripe's public
business details, support contact, receipt branding, statement descriptor,
refund process, and tax settings that apply to the business. For Australian
consumer pricing, configure the displayed one-time prices as the total charge,
including GST when the business is required to charge it.

## 5. Create the Render worker

`render.yaml` already defines the worker and a shared secret group, so this is
a Blueprint sync rather than a service you build by hand.

1. In Render, open the Blueprint for this repository and choose Sync, or
   create a new Blueprint pointed at `render.yaml`. Render will show the new
   `folio-generator` worker and the `folio-secrets` environment group.
2. Open Environment Groups, then `folio-secrets`, and paste each value ONCE:
   `GEMINI_API_KEY`, `DATABASE_URL`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`. Both the web service and the worker read them
   from here, so they cannot drift apart.
3. Apply. The worker deploys and starts polling.

There is no job mode to switch. The web service ships `FOLIO_JOB_MODE=auto`
and decides per request: while the worker's heartbeat is fresh, jobs are left
for it; when it is not, the web service generates them itself. So the worker
can be added, restarted or removed at any time without an outage and without
anything to flip in the right order.

Confirm it worked by generating one booklet and watching the worker's logs
claim it. `/healthz` also reports worker status.

One worker processes one job at a time. Add another instance later if the
queue regularly grows. Claiming is an atomic conditional update, so two
workers, or a worker and the web service, can never run the same job.

The included `render.yaml` can create both services as a Blueprint for a new
deployment. For the existing service, manual configuration avoids creating a
second web service by accident.

## 6. Configure the Render web service

Add these values to the existing `folio` web service:

```text
FLASK_SECRET_KEY=<at least 32 random characters>
GEMINI_API_KEY=<secret>
DATABASE_URL=<Supabase session-pooler URL>
FOLIO_REQUIRE_POSTGRES=1
FOLIO_PUBLIC_URL=https://folio-45rh.onrender.com
FOLIO_BUSINESS_NAME=<registered business or trading name>
FOLIO_BUSINESS_COUNTRY=Australia
FOLIO_BUSINESS_NUMBER=<ABN or other applicable business number>
FOLIO_BUSINESS_ADDRESS=<public business contact address>
FOLIO_SUPPORT_EMAIL=<monitored support address>
FOLIO_SUPPORT_PHONE=<optional monitored phone number>
FOLIO_ADMIN_EMAILS=<your FolioAI login email>
FOLIO_JOB_MODE=auto
FOLIO_COOKIE_SECURE=1
FOLIO_REQUIRE_PAYMENTS=1
STRIPE_SECRET_KEY=<live or test secret for the current stage>
STRIPE_WEBHOOK_SECRET=<matching webhook signing secret>
STRIPE_PRICE_SINGLE=<matching price ID>
STRIPE_PRICE_TERM=<matching price ID>
FOLIO_REQUIRE_EMAIL_VERIFICATION=1
FOLIO_EMAIL_FROM=<verified sender>
SMTP_HOST=<provider host>
SMTP_PORT=587
SMTP_USERNAME=<secret>
SMTP_PASSWORD=<secret>
SMTP_STARTTLS=1
SUPABASE_URL=<project URL>
SUPABASE_SERVICE_ROLE_KEY=<secret>
FOLIO_STORAGE_BUCKET=booklets
```

Generate `FLASK_SECRET_KEY` locally:

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Redeploy the web service. Its `/healthz` check confirms the process can reach
the database.

## 7. Run the launch test

Use Stripe test mode for this entire test:

1. Sign up with an email address you can receive.
2. Open the verification link and confirm the account starts with one credit.
3. Generate one booklet and wait for the worker to finish it.
4. Download it, restart the Render web service, and download it again.
5. Buy one credit through Stripe Checkout and confirm Account shows exactly
   one purchase and one new credit.
6. Refresh the success page and resend the webhook from Stripe. Confirm no
   duplicate credit appears.
7. Trigger a controlled failed job and confirm the reserved credit returns.
8. Use Forgot password and complete a reset.
9. Export account data, then test account deletion with a disposable account.
10. Inspect the site on a phone and check Support, Privacy, Terms, and Pricing.

## 8. Connect the public domain

1. Add the custom domain in Render and copy the DNS records Render provides.
2. Add those records at the domain registrar and wait for Render's TLS
   certificate to become active.
3. Change `FOLIO_PUBLIC_URL` to the final `https://` domain.
4. Create the matching Stripe live webhook and update its signing secret.
5. Update the email sender domain and links if needed.
6. Update Stripe business-profile and customer-facing URLs to the final domain.

## 9. Launch and operate

Before inviting customers:

- Replace the placeholder business name and support email.
- Have the Privacy policy, Terms, refund wording, business registrations, and
  tax treatment reviewed for the actual operator and customers.
- Review all RAG sources for public commercial-use rights.
- Turn on Render, Supabase, Stripe, Gemini, and email-provider billing alerts.
- Make a Supabase backup and document how to restore it.
- Monitor Render worker errors, Stripe webhook failures, email bounces, Gemini
  spend, job failure rate, and customer support messages.
- Keep all secret keys in provider dashboards and rotate any key that appears
  in a file, message, log, or screenshot.

Do not push the payment-required configuration live until Stripe, email, and
the worker are all ready. The app deliberately refuses to start if production
requirements are enabled but their secrets are missing.
