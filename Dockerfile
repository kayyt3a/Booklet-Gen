# FolioAI web app container.
# Build:  docker build -t folio .
# Run:    docker run -p 8080:8080 --env-file .env folio
FROM python:3.11-slim

# System deps: poppler for PDF rasterisation, tesseract for OCR of scanned
# source PDFs, and fonts so generated PDFs render text correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Local fallbacks live in /data. Production uses Postgres and private object
# storage, so this directory is only working space and needs no persistent disk.
ENV FOLIO_DB=/data/folio.db \
    FOLIO_OUTPUT=/data/output \
    PORT=8080
RUN mkdir -p /data/output

EXPOSE 8080

# Generation is handled by the separate worker in production. The timeout is
# still generous enough for migrations and slower provider responses at boot.
CMD gunicorn "booklet_gen.webapp:create_app()" \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 --threads 4 --timeout 120 --access-logfile -
