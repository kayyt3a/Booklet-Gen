"""Checks Paulio ships at the size the site actually displays him at, not
source resolution.

MASCOT_GUIDE.md sets the display sizes: standard poses 160-280 CSS px wide,
desk scenes 260-420 CSS px wide. The supplied artwork arrives around 1300px
and 1.5-2.2MB per file. Serving that directly would make one illustration
the heaviest thing on whatever page it sits on, often by a wide margin over
everything else the page loads. scripts/build_paulio_assets.py resizes to 2x
the display width and palettises to 256 colours; this checks that pipeline
ran and stayed honest, not just that files with the right names exist.

    PYTHONPATH=. python scripts/check_paulio_assets.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from scripts.build_paulio_assets import PAULIO, SIZES, SRC, STANDARD_PX, DESK_PX

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nEVERY POSE THE GUIDE NAMES EXISTS, BOTH SOURCE AND WEB-SIZED")

for name in SIZES:
    src = SRC / name
    web = PAULIO / name
    assert src.is_file(), f"{name} is missing from source/"
    assert web.is_file(), (
        f"{name} exists in source/ but not at the top level: the build "
        "script has not been run against it, or its output was deleted")
ok(f"all {len(SIZES)} poses from MASCOT_GUIDE.md's asset table are present "
   "in both source/ and the web-served directory")

print("\nTHE WEB COPY IS ACTUALLY SMALLER, NOT A DUPLICATE OF THE SOURCE")

for name in SIZES:
    src_kb = (SRC / name).stat().st_size / 1024
    web_kb = (PAULIO / name).stat().st_size / 1024
    assert web_kb < src_kb * 0.5, (
        f"{name}: source is {src_kb:.0f}KB, web copy is {web_kb:.0f}KB. "
        "That is not a real size reduction, so this looks like the raw "
        "source was copied to the web path rather than built")
ok("every web-served file is under half its source size")

print("\nNOTHING SLIPPED BACK TO AN UNOPTIMISED COPY")

# A generous ceiling, not a target: this catches someone dropping an
# unoptimised file back in over a built one, not a few KB of drift between
# runs of the quantizer.
CEILING_KB = {STANDARD_PX: 200, DESK_PX: 260}
for name, target_w in SIZES.items():
    web = PAULIO / name
    kb = web.stat().st_size / 1024
    ceiling = CEILING_KB[target_w]
    assert kb < ceiling, (
        f"{name} is {kb:.0f}KB, over the {ceiling}KB ceiling for its size "
        "band. Either the quantizer regressed or an unoptimised file was "
        "placed here directly")
ok(f"every web file stays under its ceiling "
   f"({CEILING_KB[STANDARD_PX]}KB standard / {CEILING_KB[DESK_PX]}KB desk)")

print("\nEACH WEB FILE IS SIZED FOR ITS OWN CATEGORY, NOT ONE SIZE FOR ALL")

for name, target_w in SIZES.items():
    im = Image.open(PAULIO / name)
    assert im.width == target_w, (
        f"{name} is {im.width}px wide, expected {target_w}. Standard poses "
        "and desk scenes display at different sizes on the site "
        "(MASCOT_GUIDE.md), and serving them at one shared width either "
        "blurs the smaller category or wastes bytes on the larger one")
ok(f"standard poses are {STANDARD_PX}px wide, desk scenes {DESK_PX}px, "
   "matching their own display band")

print("\nQUANTISING TO A SMALL PALETTE DID NOT FLATTEN THE SOFT EDGES")

# A hand-inked outline on a transparent background is anti-aliased: the
# pixels right at the line should carry a PARTIAL alpha, not just fully
# opaque ink and fully transparent background. Losing that reads as a
# jagged cutout instead of the drawn line the guide specifies.
sample = PAULIO / "paulio-welcome.png"
rgba = Image.open(sample).convert("RGBA")
alpha = np.asarray(rgba)[..., 3]
soft_edge_pixels = int(((alpha > 5) & (alpha < 250)).sum())
assert soft_edge_pixels > 500, (
    f"only {soft_edge_pixels} pixels of partial alpha in {sample.name}. "
    "The palette quantizer may have collapsed transparency to a single "
    "on/off index instead of keeping each palette entry's own alpha, which "
    "turns a smooth inked edge into a jagged cutout")
ok(f"soft anti-aliased edges survive quantisation "
   f"({soft_edge_pixels} partial-alpha pixels in the sample)")

print(f"\nALL {_passed} PAULIO ASSET CHECKS PASSED")
sys.exit(0)
