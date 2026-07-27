# Deploying the Folio web app

The app is a standard Flask app served by gunicorn, packaged in a Dockerfile.
Any host that runs a container works (Render, Railway, Fly.io, a VPS).

## What you need first
- A Google Gemini API key **with billing enabled**. The free tier caps at a
  low per-minute request rate on every model (not just a daily count), and
  the pipeline fires several calls in parallel per booklet, so it gets
  rate-limited fast even on a cheap model. Billing removes that cap and puts
  you on standard pay-as-you-go rates. Folio is free to users, so this is the
  one cost you carry, but at demo-scale traffic it is pennies to a few
  dollars a month (usage-based, no fixed/monthly fee). See
  `.env.webapp.example` for the fast/strong model split and which one is
  worth spending on.
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
   `FLASK_SECRET_KEY`, `GEMINI_API_KEY`, and `DATABASE_URL`.
4. Deploy. The host gives you a public URL.

## Database (important)

Set `DATABASE_URL` to a Postgres connection string. One database backs both
the user accounts and the RAG library:

- **Without it**, accounts fall back to a local SQLite file and RAG falls back
  to the on-disk Chroma store. Neither is in the Docker image and neither
  survives a restart, so on a free host accounts silently disappear and the
  app generates with no curriculum grounding at all.
- **With it**, accounts and job history persist, and the RAG library is
  available to the deployed app.

Free Postgres with pgvector: Neon or Supabase. Render's own Postgres works too.
The `vector` extension and all tables are created automatically on first run.

To move an existing local RAG library up without re-embedding (so it costs no
Gemini quota):

```
DATABASE_URL=...  python scripts/migrate_rag_to_postgres.py --dry-run
DATABASE_URL=...  python scripts/migrate_rag_to_postgres.py
```

Generated PDFs still land on the instance filesystem and are lost on restart,
which only affects re-downloading an old booklet. Mount a disk at `/data` (a
paid plan on Render) if you want those kept too.

## Notes
- `gemini-embedding-001` returns 3072-dimension vectors. pgvector's ANN
  indexes cap at 2000 dimensions, so the store uses an exact scan, which is
  correct and fast for a library of a few thousand chunks. If the library
  grows large, reduce the embedding dimension to 1536 and an HNSW index is
  created automatically (set `FOLIO_EMBED_DIM` to match, and re-ingest).
- Generation runs in a background thread per request. For higher volume, move
  to a proper task queue (RQ or Celery); the job code is already isolated.
