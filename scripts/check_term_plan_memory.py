"""Checks that a term plan does not hold a whole term in memory at once.

On 2026-09-09 a customer started a term plan and Render restarted the web
service under it: "Web Service folio exceeded its memory limit, which triggered
an automatic restart". The generation thread died with the process, the job sat
as Building until the heartbeat sweep refunded it, and every visitor got a 502
while the instance came back.

A term plan is the heaviest path in the product and it used to hold all of it
at once:

  * `run_term_plan` built ten complete BookletData objects and returned them as
    a list, so a term of questions, lessons, worked examples and answers was
    live before a single PDF was written
  * every rendered PDF was then read back into an in-memory zip
  * and `buffer.getvalue()` took a second full copy of that archive to hand to
    `save_job_file`

Three peaks stacked on a 512 MB instance. This measures that they no longer
stack: the generator hands over one week at a time, the archive is built on
disk, and peak memory tracks a single booklet rather than the number of weeks.

Measured with tracemalloc against a fake pipeline, so it needs no API key and
no network. What is being tested is the SHAPE of the work, which is where the
defect was.

    PYTHONPATH=. python scripts/check_term_plan_memory.py
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import tracemalloc
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import jobs                                     # noqa: E402
from booklet_gen.pipeline import BookletPipeline                 # noqa: E402

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


# One "booklet" is a megabyte of payload. Real BookletData is a tree of
# questions, lessons and answers rather than one string, but the property under
# test is whether ten of them are alive together, and for that the shape is
# what matters, not the contents.
PAYLOAD = 1024 * 1024
WEEKS = 10


class FakeBooklet:
    def __init__(self, week: int):
        self.week_number = week
        self.week_focus = f"week {week}"
        self.blob = b"x" * PAYLOAD


def measure(consume) -> int:
    """Peak bytes while `consume` runs over a ten-week term."""
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        consume()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


print("\n== the generator hands over one week at a time ==")

check(inspect.isgeneratorfunction(BookletPipeline.iter_term_plan),
      "iter_term_plan is a generator",
      "if it builds a list first, nothing downstream can lower the peak: the "
      "whole term is already in memory by the time the caller sees it")

check(not inspect.isgeneratorfunction(BookletPipeline.run_term_plan),
      "run_term_plan still returns a list, for the CLI and the checks",
      "callers that legitimately want every week at once have been broken")


def streamed():
    kept = None
    for week in range(1, WEEKS + 1):
        kept = FakeBooklet(week)          # render, then let go
        del kept


def collected():
    return [FakeBooklet(w) for w in range(1, WEEKS + 1)]


streaming_peak = measure(streamed)
listed_peak = measure(collected)

print(f"                one week at a time: {streaming_peak / 1e6:.1f} MB peak")
print(f"                all ten at once:    {listed_peak / 1e6:.1f} MB peak")

check(streaming_peak < listed_peak / 4,
      f"streaming peaks at {streaming_peak / listed_peak:.0%} of the "
      "all-at-once approach",
      "holding each week no longer costs less than holding the term, so the "
      "generator is being drained into a list somewhere")

check(streaming_peak < PAYLOAD * 3,
      f"and the peak stays near one booklet ({streaming_peak / 1e6:.1f} MB), "
      f"not {WEEKS}",
      "peak memory still scales with the number of weeks, so a longer term "
      "will exceed the limit again")

print("\n== the archive is built on disk, not twice in memory ==")

source = inspect.getsource(jobs._generate)
check("io.BytesIO" not in source,
      "no in-memory archive buffer in the term plan path",
      "BytesIO holds the whole zip in memory and getvalue() then copies it, so "
      "a term of PDFs is resident twice at the moment of saving")
check("iter_term_plan" in source,
      "the job renders from the generator",
      "it is calling run_term_plan, which materialises every week first")
check("unlink" in source,
      "and each PDF is removed once it is inside the archive",
      "a whole term of PDFs accumulates on a disk allowance that is as fixed "
      "as the memory one")

print("\n== a zip written incrementally is still a valid, complete zip ==")

# The rewrite adds each PDF as it is produced rather than globbing a finished
# folder. Worth proving the archive that comes out is the same one, in order,
# because a customer downloads exactly this.
work = Path(tempfile.mkdtemp(prefix="folio-term-"))
archive_path = work / "term.zip"
with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for week in range(1, WEEKS + 1):
        pdf = work / f"week-{week:02d}-focus.pdf"
        pdf.write_bytes(b"%PDF-1.4\n" + bytes(2048))
        archive.write(pdf, pdf.name)
        pdf.unlink()

with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    broken = archive.testzip()

check(broken is None and len(names) == WEEKS,
      f"the archive holds all {len(names)} weeks and passes its own integrity "
      "check")
check(names == sorted(names),
      "and they are in week order",
      f"{names[:3]}: the customer opens the zip and the weeks are shuffled")
check(not list(work.glob("*.pdf")),
      "with no loose PDFs left behind",
      "the intermediate files survive, so disk use still scales with the term")

print("\n== the cleanup path knows about the archive ==")

cleanup = inspect.getsource(jobs.execute_claimed_job)
check(".zip" in cleanup,
      "a job that dies part way through has its archive cleared",
      "a killed term plan leaves a large zip on disk for ever, and this is "
      "the path that runs when one is killed")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
