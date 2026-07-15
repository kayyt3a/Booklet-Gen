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
                                — OR the wildcard "All Years" for
                                  cross-year curriculum docs.
          <TopicTag>/           free-form; conventional: NAPLAN, SCSA,
                                Cambridge, Textbook, PastPapers
            some-source.pdf

The `<TopicTag>` becomes the `topics` value on every chunk from files inside
it, so the retriever can prefer NAPLAN-tagged chunks when generating a
NAPLAN-style booklet, and SCSA-tagged chunks when generating school work.

Example populated tree:

    rag_sources/
      Mathematics/
        All Years/
          SCSA/
            scsa-maths-scope-and-sequence-P10.pdf   ← retrieved for any year
            scsa-maths-year-descriptions-P10.pdf
        Year 3/
          NAPLAN/
            naplan-2019-y3-numeracy.pdf
        Year 5/
          NAPLAN/
            naplan-2022-y5-numeracy.pdf
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
- **NAPLAN past papers** — https://www.acara.edu.au/assessment/naplan
  (start at "NAPLAN 2012–2016 test papers" for the full archive). Free
  and public. Single biggest lift for NAPLAN quality.
- **Cambridge / other textbooks** — copyrighted. Fine to use privately in
  your own RAG library to guide *style* (the generated questions are your
  own new content, not copies). Don't distribute the PDFs. If you ever
  commercialise this tool, drop them and use only open/licensed sources.

## Bulk downloading

To grab every PDF linked on a page in one shot, use the included downloader:

    python scripts/download_pdfs.py <URL> --into <folder>

Examples:

    # Every PDF on the ACARA NAPLAN 2012-2016 page, sorted into Y5 Numeracy
    python scripts/download_pdfs.py \
      https://www.acara.edu.au/assessment/naplan/naplan-2012-2016-test-papers \
      --into "rag_sources/Mathematics/Year 5/NAPLAN" \
      --contains numeracy

    # Same, but Reading into the English folder
    python scripts/download_pdfs.py \
      https://www.acara.edu.au/assessment/naplan/naplan-2012-2016-test-papers \
      --into "rag_sources/English/Year 5/NAPLAN" \
      --contains reading

`--contains <substring>` filters URLs by path — helpful when a listing page
has all year levels and subjects mixed together. `--dry-run` prints what
would be downloaded without touching disk. Files that already exist are
skipped, so re-running is safe.

## Fastest path: download an ACARA archive page end-to-end

ACARA listing pages mix every year level and subject together, and a
single `--contains` filter can't safely capture "year AND subject" at
once (e.g. `--contains numeracy` on its own would pull Year 3, 5, 7 and 9
numeracy into whichever folder you point it at). Instead, dump everything
into a staging folder and let the sorter route each file by filename:

    python scripts/download_pdfs.py <ACARA-page-URL> --into rag_sources/_staging
    python scripts/sort_naplan_staging.py --dry-run   # check the plan
    python scripts/sort_naplan_staging.py             # move for real
    python scripts/ingest_folder.py

`sort_naplan_staging.py` detects year level (`y3`/`yr5`/`year7`/`Y09`...)
and subject (`numeracy` -> Mathematics, `reading`/`language`/`writing`/
`convention`/`persuasive`/`narrative` -> English) from each filename and
moves it into `rag_sources/<Subject>/<Year>/NAPLAN/`. Files it can't
confidently classify are left in `_staging` and printed at the end —
move those into the right folder by hand.

Run it again for other ACARA archive pages (2008–2011, 2017+, example
tests, ...) any time — the staging folder starts fresh each run since
sorted files are moved out of it.

## Notes

- Files sitting loose at the top level of `rag_sources/` are skipped with a
  warning — move them under `<Subject>/<Year>/<TopicTag>/` to ingest.
- Add `--dry-run` to `ingest_folder.py` to preview what will be ingested
  without touching the store.
- The store lives in `rag_store/` and is git-ignored. Losing it just means
  running the ingester again.
