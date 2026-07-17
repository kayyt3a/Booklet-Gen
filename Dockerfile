# Folio web app container.
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

# Persistent data (SQLite db + generated output) lives here; mount a volume
# at /data in production so it survives restarts.
ENV FOLIO_DB=/data/folio.db \
    FOLIO_OUTPUT=/data/output \
    PORT=8080
RUN mkdir -p /data/output

EXPOSE 8080

# 2 workers, long timeout because booklet generation is slow.
CMD gunicorn "booklet_gen.webapp:create_app()" \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 --threads 4 --timeout 600
