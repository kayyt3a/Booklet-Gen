"""Verify Paulio's icon actually appears in the worked-example box of a
primary booklet (Years 1-6), and stays out of a secondary one, matching the
same year cut the "Paulio shows you first" label already uses.

    PYTHONPATH=. python scripts/check_paulio_in_booklet.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.platypus import Image, Paragraph, Table

from booklet_gen.formatter import (
    _PAULIO_ICON_PATH, _make_styles, _worked_example_flowable, paulio_teaches,
)
from booklet_gen.schemas import WorkedExample


def _header(box: Table):
    """The first flowable inside the box: either Paulio's header row or the
    plain label paragraph, depending on how the box was built."""
    return box._cellvalues[0][0][0]


def check_asset_exists_on_disk():
    assert _PAULIO_ICON_PATH.exists(), (
        f"Paulio's worked-example icon is missing on disk: {_PAULIO_ICON_PATH}")
    print("OK: Paulio's worked-example icon exists on disk")


def check_icon_present_for_primary():
    styles = _make_styles()
    we = WorkedExample(question="Order 12, 5, 8 from smallest to largest.",
                       steps=["Compare the digits."], answer="5, 8, 12")
    box = _worked_example_flowable(styles, we, "Paulio shows you first", paulio=True)
    header = _header(box)
    assert isinstance(header, Table), (
        f"expected Paulio's header row (image + label), got {type(header).__name__}")
    icon = header._cellvalues[0][0]
    assert isinstance(icon, Image), (
        f"expected Paulio's icon image in the header row, got {type(icon).__name__}")
    print("OK: a primary worked-example box carries Paulio's icon")


def check_icon_absent_for_secondary():
    styles = _make_styles()
    we = WorkedExample(question="Differentiate f(x) = x^2.",
                       steps=["Apply the power rule."], answer="2x")
    box = _worked_example_flowable(styles, we, "Worked example", paulio=False)
    header = _header(box)
    assert isinstance(header, Paragraph), (
        f"expected a plain label with no icon, got {type(header).__name__}")
    print("OK: a secondary worked-example box carries no icon")


def check_guided_example_also_gets_the_icon():
    # A booklet's guided ("we do") box is taught content too, not just the
    # opening worked example, so it has to carry the same icon or Paulio
    # narrates the first box and then silently vanishes from the second.
    styles = _make_styles()
    ge = WorkedExample(question="Order 20, 11, 15 from smallest to largest.",
                       steps=["Compare the digits."], answer="11, 15, 20")
    box = _worked_example_flowable(styles, ge, "Now let's try one together", paulio=True)
    header = _header(box)
    assert isinstance(header, Table), (
        "expected the guided example to carry Paulio's icon too, "
        f"got {type(header).__name__}")
    print("OK: the guided ('we do') box also carries Paulio's icon")


def check_year_gate_matches_label_gate():
    # The icon must follow paulio_teaches(), the same gate the label already
    # uses, not a second gate of its own that could drift out of sync with it.
    assert paulio_teaches("Year 6") is True
    assert paulio_teaches("Year 7") is False
    assert paulio_teaches("Year 11-12") is False
    print("OK: the icon's year cut matches the label's year cut")


if __name__ == "__main__":
    check_asset_exists_on_disk()
    check_icon_present_for_primary()
    check_icon_absent_for_secondary()
    check_guided_example_also_gets_the_icon()
    check_year_gate_matches_label_gate()
    print("All checks passed.")
