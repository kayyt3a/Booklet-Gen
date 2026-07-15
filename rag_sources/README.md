# RAG source library

Everything under this folder is chunked and embedded into the local vector
store when you run:

    .venv/bin/python scripts/ingest_folder.py

The ingester derives metadata from the folder tree — no need to edit any
script when you add new PDFs. Just drop them in the right subfolder and
re-run.

## Folder layout

    rag_sources/
      <Subject>/                Mathematics, English, Science
        <Year>/                 "Year 3", "Year 5", "Year 7", "Year 9", ...
          <TopicTag>/           free-form; conventional: NAPLAN, SCSA,
                                Cambridge, Textbook, PastPapers
            some-source.pdf

The `<TopicTag>` becomes the `topics` value on every chunk from files inside
it, so the retriever can prefer NAPLAN-tagged chunks when generating a
NAPLAN-style booklet, and SCSA-tagged chunks when generating school work.

Example populated tree:

    rag_sources/
      Mathematics/
        Year 3/
          NAPLAN/
            naplan-2019-y3-numeracy.pdf
            naplan-2021-y3-numeracy.pdf
          SCSA/
            wa-curriculum-y3-maths.pdf
        Year 5/
          NAPLAN/
            naplan-2022-y5-numeracy.pdf
          Cambridge/
            cambridge-primary-maths-y5.pdf
      English/
        Year 5/
          NAPLAN/
            naplan-2022-y5-reading.pdf
            naplan-2022-y5-language.pdf

Re-run the ingester any time you add or remove PDFs. It's idempotent — a
file that has been ingested before is deleted and re-added, not duplicated.

## Where to get sources

- **SCSA (WA curriculum)** — https://k10outline.scsa.wa.edu.au/ — free,
  official, exactly the calibration you want for school-focused booklets.
- **NAPLAN past papers** — https://nap.edu.au/naplan/results-and-reports/example-tests
  — free and public. Single biggest lift for NAPLAN quality.
- **Cambridge / other textbooks** — copyrighted. Fine to use privately in
  your own RAG library to guide *style* (the generated questions are your
  own new content, not copies). Don't distribute the PDFs. If you ever
  commercialise this tool, drop them and use only open/licensed sources.

## Notes

- Files sitting loose at the top level of `rag_sources/` are skipped with a
  warning — move them under `<Subject>/<Year>/<TopicTag>/` to ingest.
- Add `--dry-run` to `ingest_folder.py` to preview what will be ingested
  without touching the store.
- The store lives in `rag_store/` and is git-ignored. Losing it just means
  running the ingester again.
