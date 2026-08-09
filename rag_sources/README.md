# Copyright-safe RAG source library

FolioAI is a commercial product. A document being free to download, publicly
available or educational does not mean it can be uploaded to an app, embedded,
adapted or used to produce a paid resource.

Do not ingest a source into the deployed database until its commercial use has
been reviewed and recorded. When rights are unclear, treat the source as not
approved.

## Current safety state

The existing local library contains past NAPLAN and WACE assessment material.
Do not migrate that library into the paid product. NAPLAN external RAG is
disabled in `booklet_gen/programs.py` while a reviewed corpus is built. The
internal `booklet_gen/guidance/naplan_practice.txt` file guides original item
writing without exposing agents to past-paper content.

This code boundary does not delete local files. It prevents the NAPLAN product
from retrieving them. Other programs can still use the shared Mathematics
store, so a production store must be rebuilt from approved sources rather than
copied from the current local store.

## Source categories

### Approved in principle, subject to item-level review

- Material written by the FolioAI operator specifically for commercial use.
- Material supplied under a licence that expressly permits commercial reuse
  and adaptation, with every licence condition followed.
- Selected Australian Curriculum website text covered by CC BY 4.0, with the
  required attribution and without excluded or third-party material.
- Material for which the rights holder has given FolioAI written permission
  covering storage, embedding, AI-assisted generation and sale of outputs.

### Not approved without written permission

- Past NAPLAN tests, answers, reading passages and writing prompts.
- NAPLAN demonstration or trial items used as a question bank.
- WACE examinations and marking keys used commercially without SCSA
  permission.
- ACER scholarship papers or other proprietary assessment papers.
- Commercial textbooks, workbooks, tutoring resources and teacher guides.
- National Literacy and Numeracy Learning Progressions or other content marked
  non-commercial.
- Photographs, logos, illustrations or third-party material whose licence is
  different from the surrounding page or document.

Do not use an education, research, study or classroom exception as the basis
for a paid consumer product without advice specific to FolioAI.

## Required rights record

Before ingestion, record all of the following in the project's source-rights
register:

| Field | What to record |
| --- | --- |
| Internal source id | Stable identifier used in filenames and logs |
| Title | Exact title of the source |
| Rights holder | Person or organisation that owns the material |
| Source URL | The official page where the material and terms were obtained |
| Access date | Date the source and terms were checked |
| Licence or permission | Exact licence name or written permission reference |
| Commercial use | Yes or no |
| Adaptation allowed | Yes or no |
| AI and embedding use | Whether this use is expressly permitted or reviewed |
| Attribution | Exact wording that must appear in FolioAI or its documentation |
| Exclusions | Pages, images, logos or third-party material that must be removed |
| Reviewer | Person who completed the rights review |
| Review date | Date of the decision |
| Decision | Approved, quarantined or rejected |

Keep a copy of the applicable terms or permission with the record. Terms can
change after a source is downloaded.

The tracked template is `rag_sources/source_rights.csv`. Its `source_path`
must be the PDF path relative to `rag_sources`, using forward slashes. The
folder ingester fails closed: unregistered, quarantined, rejected, incomplete,
or ambiguously licensed sources are listed as blocked and are not embedded.
Every approved chunk receives the source id and review date as provenance
metadata. The Postgres migration refuses old or hand-built stores without that
metadata.

## Safe corpus design

Prefer short, operator-written skill briefs over whole assessment papers. A
useful RAG chunk identifies:

- the curriculum skill
- prerequisite knowledge
- common misconceptions
- age-appropriate vocabulary and notation
- one abstract example written by FolioAI
- accessibility considerations
- the source and licence for any curriculum statement used

It should not contain a released question, distinctive passage, official
stimulus, answer set or marking key.

Suggested folder layout:

```text
rag_sources/
  Mathematics/
    Year 5/
      Folio-Original/
        fractions-skill-brief.pdf
  English/
    Year 5/
      Folio-Original/
        inference-skill-brief.pdf
  Curriculum/
    All Years/
      ACARA-CC-BY/
        reviewed-content-descriptions.pdf
```

The subject folder must still match the names used by the application:
`Mathematics`, `English`, `Reasoning`, or `Mathematics Methods`.

## Australian Curriculum material

The Australian Curriculum website states that much of its content is licensed
under CC BY 4.0, which permits commercial adaptation with attribution. It also
lists exclusions, including logos, photographs, some third-party material and
more restrictive resources. Review the specific page or download before use:

https://www.australiancurriculum.edu.au/copyright-and-terms-of-use/

Do not assume that all ACARA material has the same licence. Past NAPLAN test
materials have separate and much more restrictive terms:

https://www.acara.edu.au/assessment/naplan

## Ingestion workflow

1. Put the candidate source in a quarantine folder, not the approved library.
2. Read the source's own copyright notice and the official website terms.
3. Remove excluded pages, images, logos and third-party content.
4. Complete the rights record.
5. Have a second person review uncertain or high-value sources.
6. Move only approved material into the structured source library.
7. Run the ingester against a fresh test store first.
8. Inspect retrieved chunks for restricted wording and bad metadata.
9. Migrate only the reviewed store to production.
10. Retain the rights register and attribution text for the life of the source.

Never point `scripts/migrate_rag_to_postgres.py` at the current local store for
a paid deployment. Build a clean store from approved sources.

## Ingesting approved material

Set `DATABASE_URL` only after the test store has been reviewed. Without it the
ingester writes to local `rag_store/`.

```powershell
.venv\Scripts\python.exe scripts\ingest_folder.py --dry-run
.venv\Scripts\python.exe scripts\ingest_folder.py
.venv\Scripts\python.exe scripts\rag_status.py
```

Re-running replaces a source rather than duplicating it. The current folder
ingester reads PDF files. Everything under `rag_sources/` except this README is
gitignored because raw source files may be large or licensed. The CSV rights
register is the deliberate exception and remains tracked for audit history.
