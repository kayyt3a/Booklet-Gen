# Deploying the Folio web app

The app is a standard Flask app served by gunicorn, packaged in a Dockerfile.
Any host that runs a container works (Render, Railway, Fly.io, a VPS).

## What you need first
- A Google Gemini API key **with billing enabled** (the free tier caps at 500
  requests/day, which one or two booklets can exhaust).
- A Stripe account (test mode is fine to start) for taking payments.
- A host account and, ideally, a domain.

## Local run (no Docker)
```
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.webapp.example .env        # fill in FLASK_SECRET_KEY and GEMINI_API_KEY
# load the env vars, then:
python -m booklet_gen.webapp
```
Open http://127.0.0.1:5000

## Local run (Docker)
```
docker build -t folio .
docker run -p 8080:8080 --env-file .env -v $PWD/data:/data folio
```
Open http://localhost:8080

## Deploying to a host (e.g. Render / Railway / Fly.io)
1. Push this repo to GitHub (already done).
2. Create a new "Web Service" from the repo. The host will detect the
   Dockerfile and build it.
3. Add environment variables in the host dashboard (see `.env.webapp.example`):
   `FLASK_SECRET_KEY`, `GEMINI_API_KEY`, and the Stripe keys.
4. Add a persistent disk/volume mounted at `/data` so accounts and generated
   booklets survive restarts.
5. Deploy. The host gives you a public URL. Put that in `PUBLIC_BASE_URL`.

## Stripe setup
1. In the Stripe dashboard, get your **Secret key** into `STRIPE_SECRET_KEY`.
2. Add a webhook endpoint pointing at `https://your-domain.com/webhook`, listening
   for `checkout.session.completed`. Put its signing secret into
   `STRIPE_WEBHOOK_SECRET`.
3. Credit packs and prices live in `booklet_gen/webapp/pricing.py`; edit freely.

## Notes
- SQLite is used for accounts and credits, which is fine for launch. Move to
  Postgres when you have real traffic (the data layer is isolated in
  `booklet_gen/webapp/db.py`).
- Generation runs in a background thread per request. For higher volume, move
  to a proper task queue (RQ or Celery); the job code is already isolated.
