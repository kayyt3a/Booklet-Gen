# RAG source library

Drop source PDFs in here, run the ingester, and they become the reference
material the generator calibrates against.

## Where to save downloads

```
rag_sources/
  <Subject>/          must match the names below exactly
    <Year>/           "Year 3" ... "Year 12", or "All Years"
      <Tag>/          free-form: NAPLAN, SCSA, WACE, PastPapers, Textbook
        some-paper.pdf
```

### Subject folder names matter

The retriever filters on subject, so a folder name that does not match what the
app asks for is invisible. Use exactly these:

| Folder name             | Used by                                  |
| ----------------------- | ---------------------------------------- |
| `Mathematics`           | Academic Accelerate, NAPLAN Practice     |
| `English`               | Academic Accelerate, NAPLAN Practice     |
| `Reasoning`             | Scholarships                             |
| `Mathematics Methods`   | Methods Exam (Year 11-12)                |

`Maths`, `maths`, or `Math` will **not** match. Neither will `Science`, which
is not currently offered.

`All Years` is a wildcard for cross-year documents such as a P-10 scope and
sequence: those chunks are retrieved for every year level.

### Example

```
rag_sources/
  Mathematics/
    All Years/
      SCSA/
        scsa-maths-scope-and-sequence-P10.pdf
    Year 5/
      NAPLAN/
        naplan-2016-numeracy-year-5.pdf
  Mathematics Methods/
    Year 12/
      WACE/
        2024-MAM-Examination-Calculator-Assumed.pdf
        2024-MAM-Ratified-Calc-Assumed-Marking-Key.pdf
  English/
    Year 5/
      NAPLAN/
        naplan-2016-reading-year-5.pdf
```

## Ingesting

**Set `DATABASE_URL` first if you want the material to reach the deployed app.**
Without it the ingester writes to the local `rag_store/` only, which never
leaves your machine.

```powershell
$env:DATABASE_URL="postgresql://..."          # the live database
.venv\Scripts\python scripts\ingest_folder.py --dry-run
.venv\Scripts\python scripts\ingest_folder.py
.venv\Scripts\python scripts\rag_status.py    # confirm what landed
```

Re-running is safe: a file that has been ingested before is replaced, not
duplicated. Only `.pdf` is read. Word documents are skipped silently, so
convert them first.

If ingestion stops partway with a quota error, everything queued after that
point never made it in. Re-run once quota resets and check `rag_status.py`.

## Where to get sources

Ordered by what actually moves booklet quality, given what is already ingested.

### 1. Curriculum scope and sequence (fills the even-year gap)

NAPLAN only runs in Years 3, 5, 7 and 9, so past papers can never ground
Academic Accelerate in the even years. Scope-and-sequence documents can: file
them under `All Years` and every year level retrieves them.

- **SCSA (WA)**, free, official, already PDF, no conversion needed. Best fit.
  https://k10outline.scsa.wa.edu.au/home/wa-curriculum/learning-areas/mathematics/p-10-mat-curriculum
  https://k10outline.scsa.wa.edu.au/home/wa-curriculum/learning-areas/english/p-10-english-curriculum
  Look for the Mathematics P-10, Years 7-10 Mathematics, Mathematics P-6, and
  English P-10 scope-and-sequence PDFs.
- **Australian Curriculum v9 (national)**, free and official, but the downloads
  are `.docx` and the ingester reads `.pdf` only, so convert first.
  https://www.australiancurriculum.edu.au/downloads/learning-areas/
  QCAA mirrors the same content as browsable pages:
  https://www.qcaa.qld.edu.au/p-10/aciq/version-9/learning-areas/p-10-mathematics

### 2. Reasoning material (Scholarships has none)

The Scholarships program currently generates ungrounded. The NSW Department of
Education publishes free official Selective High School Placement Test sample
papers with answer keys, covering Reading, Mathematical Reasoning, Thinking
Skills and Writing. That maps straight onto `Reasoning/`. Prefer the
department's own copies over the third-party sites that mirror them, and favour
recent material: the test became computer-based in 2025.

### 3. NAPLAN past papers

Free and public, the biggest lift for NAPLAN quality:
https://www.acara.edu.au/assessment/naplan
Numeracy papers go under `Mathematics/`, reading/language/writing under
`English/`. Note that public archives thin out after 2016, when the test moved
to online adaptive delivery. The 2008-2011 and 2012-2016 sets may be the
complete official offering.

### 4. Other

- **WACE ATAR past papers and marking keys** for Methods Exam, from SCSA.
  Methods only. Specialist is not a product line.
- **Textbooks**, copyrighted. Fine privately to guide *style*, since generated
  questions are new content rather than copies. Do not redistribute.

## Bulk downloading

```powershell
python scripts\download_pdfs.py <URL> --into "rag_sources\Mathematics\Year 5\NAPLAN"
```

`--contains <text>` filters by URL path, `--dry-run` shows the plan. Existing
files are skipped.

ACARA listing pages mix every year and subject together, and one `--contains`
filter cannot capture "year AND subject" safely. Dump everything into a staging
folder and let the sorter route each file by filename:

```powershell
python scripts\download_pdfs.py <ACARA-page-URL> --into rag_sources\_staging
python scripts\sort_naplan_staging.py --dry-run
python scripts\sort_naplan_staging.py
python scripts\ingest_folder.py
```

`sort_naplan_staging.py` reads the year (`y3`/`yr5`/`year7`) and subject
(`numeracy` to Mathematics; `reading`/`language`/`writing`/`conventions` to
English) from each filename and files it under
`rag_sources/<Subject>/<Year>/NAPLAN/`. Anything it cannot classify is left in
`_staging` and listed at the end for you to move by hand.

## Notes

- Files loose at the top level of `rag_sources/` are skipped with a warning.
- Everything here is gitignored: large, and some of it is copyrighted for
  personal use only.
- The local store lives in `rag_store/` and is also gitignored. Losing it just
  means re-running the ingester, or re-migrating from Postgres.
