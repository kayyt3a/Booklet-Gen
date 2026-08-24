"""The booklet cover has to be visible to someone who has not paid yet.

The cover is the product's strongest visual asset and, until covers.py and
scripts/build_cover_samples.py existed, it appeared nowhere on the website:
static/img/ shipped eleven mascot poses and zero covers, so the only way to
see one was to buy a booklet. The share card had the same problem in a worse
place, because it is what unfurls when someone drops a FolioAI link into a
parents' group: a wordmark on navy, technically correct at 1200x630 and
silent about what is being sold.

This checks the parts of that a machine can see:

  * every cover named in booklet_gen/webapp/covers.py is actually built, at
    the right size and A4 shape, and small enough to serve;
  * a library row's stand-in cover resolves to a file that exists, and two
    different subjects do not get the same one;
  * the landing page, the pricing page and My booklets each show at least one
    cover, and every image they reference is served;
  * the share card is 1200x630 and has a cover in it. That last one is
    measured rather than asserted by filename: the old card was navy across
    its whole width, and a card with a light booklet cover standing on the
    right cannot be.

Run:  PYTHONPATH=. python scripts/check_web_covers.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                            # noqa: E402
from PIL import Image                                         # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="folio-covers-"))
os.environ["FOLIO_DB"] = str(_tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(_tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "c" * 40
os.environ.pop("DATABASE_URL", None)

from booklet_gen.webapp import create_app                     # noqa: E402
from booklet_gen.webapp import db                             # noqa: E402
from booklet_gen.webapp.covers import SAMPLES, cover_for      # noqa: E402

STATIC = Path("booklet_gen/webapp/static")
failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
print("\nThe covers the site advertises are actually built")
print("-" * 62)
check(len(SAMPLES) >= 3, "there is a set of covers, not one token image",
      f"{len(SAMPLES)} defined")

A4 = 1075 / 760  # the aspect the cover renderer draws at
for s in SAMPLES:
    hero, thumb = STATIC / s.file, STATIC / s.thumb
    if not hero.is_file() or not thumb.is_file():
        check(False, f"{s.slug} is built at both sizes",
              "run: PYTHONPATH=. python scripts/build_cover_samples.py")
        continue
    hw, hh = Image.open(hero).size
    tw, th = Image.open(thumb).size
    kb = hero.stat().st_size / 1024
    # Wide enough to read the title and topic line on a landing page, which is
    # the whole reason it is there. 600 is 2x a 300 CSS px display.
    check(hw >= 600 and abs(hh / hw - A4) < 0.02,
          f"{s.slug} is a readable A4-shaped cover", f"{hw}x{hh}")
    check(90 <= tw <= 200 and abs(th / tw - A4) < 0.03,
          f"{s.slug} has a row-sized thumbnail", f"{tw}x{th}")
    # A cover is flat vector fill. If one of these arrives as a several
    # hundred KB truecolour render, the build step was skipped.
    check(kb < 90, f"{s.slug} is small enough to serve", f"{kb:.0f} KB")


# ---------------------------------------------------------------------------
print("\nA library row's cover resolves, and subjects do not all look alike")
print("-" * 62)
labels = {
    "Academic Accelerate - Year 5 - Mathematics - Ella": "maths",
    "Academic Accelerate - Year 3 - English - Noah": "english",
    "NAPLAN Practice - Year 7 - Mia": "naplan",
    "": "no label at all",
}
chosen = {}
for label, what in labels.items():
    path = cover_for(label)
    chosen[what] = path
    check((STATIC / path).is_file(), f"a {what} booklet gets a cover that exists",
          path)
check(len({chosen["maths"], chosen["english"], chosen["naplan"]}) == 3,
      "maths, English and NAPLAN rows are told apart at a glance",
      str(sorted(set(chosen.values()))))


# ---------------------------------------------------------------------------
print("\nThe pages a prospective customer reads show one")
print("-" * 62)
app = create_app()
client = app.test_client()


def csrf(path: str) -> str:
    m = re.search(rb'name="csrf_token" value="([^"]+)"', client.get(path).data)
    return m.group(1).decode() if m else ""


client.post("/signup", data={"email": "covers@test.com",
                             "password": "correct-horse-battery",
                             "csrf_token": csrf("/signup")})
uid = db.get_user_by_email("covers@test.com")["id"]
db.create_job("covers-job", uid,
              "Academic Accelerate - Year 5 - Mathematics - Ella", units=1)
db.finish_job("covers-job", path=str(_tmp / "output" / "x.pdf"))
db.save_job_file("covers-job", uid, "folio.pdf", "application/pdf", b"%PDF x")

# The landing page is only served to a signed-out visitor, so it is fetched
# with its own client rather than the signed-in one above.
pages = {"/": create_app().test_client().get("/").data,
         "/pricing": client.get("/pricing").data,
         "/library": client.get("/library").data}
for path, body in pages.items():
    refs = re.findall(rb'src="(/static/img/covers/[^"]+)"', body)
    check(bool(refs), f"{path} shows a booklet cover", f"{len(refs)} on the page")
    for ref in set(refs):
        r = client.get(ref.decode())
        check(r.status_code == 200 and len(r.data) > 1000,
              f"{ref.decode().rsplit('/', 1)[-1]} is served",
              f"{r.status_code}, {len(r.data)} bytes")

# The sample booklet page was rendered at about 320 CSS px, half the width of
# the page it photographs, so none of its own text could be read and nothing
# said it opened bigger.
check(b"Click to enlarge" in pages["/"],
      "the sample page says it opens full size rather than leaving a blur")


# ---------------------------------------------------------------------------
print("\nThe share card sells something")
print("-" * 62)
og = STATIC / "img" / "brand" / "og-image.jpg"
check(og.is_file(), "the share card exists")
im = Image.open(og)
check(im.size == (1200, 630), "it is the size the crawlers read", str(im.size))
head = pages["/"].decode()
check('content="1200"' in head and 'content="630"' in head,
      "and the page declares those same dimensions")

grey = np.asarray(im.convert("L")).astype(float)
third = grey.shape[1] // 3
left, right = grey[:, :third].mean(), grey[:, 2 * third:].mean()
# The old card was the wordmark on navy: dark from edge to edge, right third
# mean 23. A card with a booklet cover standing in it cannot be.
check(right > 90 and left < 80,
      "it shows a light booklet cover on a navy field, not a logo on navy",
      f"left third {left:.0f}, right third {right:.0f}")

# build_brand_assets.py used to write this file by cropping the supplied
# banner, so re-running it would have silently restored the logo-on-navy card.
brand_build = Path("scripts/build_brand_assets.py").read_text()
check('BRAND / "og-image.jpg"' not in brand_build,
      "and no other build script can put the bare logo card back")


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
