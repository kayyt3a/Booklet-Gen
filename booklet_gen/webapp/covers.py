"""The booklet covers the website shows, and which one a library row wears.

One list, read by two things that would otherwise drift apart:

  * scripts/build_cover_samples.py renders these specs into
    static/img/covers/, using the same booklet_gen.visuals.cover code that
    draws the cover of a real PDF;
  * the templates show them, and library.html asks cover_for() which of them
    stands in for a given booklet row.

Keeping the definitions here rather than in the build script means a template
can never name an image the build does not produce, which is the usual way an
asset directory ends up with a broken img tag nobody notices for a month.

The thumbnail on a library row is a stand-in, not that booklet's actual cover:
the generated PDF is stored as one blob and nothing rasterises page 1 of it.
It is chosen from the row's own subject and product line, so a parent's maths
booklets and their English booklets look different from each other at a glance,
which is what makes the list read as a shelf of objects rather than as a log.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverSample:
    slug: str
    year: str
    subject: str
    program: str
    topic: str
    name: str
    week: str = ""
    pill: str = "Practice Booklet"

    @property
    def file(self) -> str:
        return f"img/covers/{self.slug}.png"

    @property
    def thumb(self) -> str:
        return f"img/covers/{self.slug}-thumb.png"

    @property
    def alt(self) -> str:
        return (f"The cover of a {self.year} {self.subject} practice booklet: "
                f"FolioAI wordmark, the title {self.year} {self.subject}, and "
                "the topic and student name underneath.")


# Each one is a booklet the customer menu will actually sell (programs.py).
# Showing a cover for a product that is held back would be advertising
# something the site then refuses to generate.
SAMPLES: tuple[CoverSample, ...] = (
    CoverSample("year5-mathematics", "Year 5", "Mathematics",
                "Academic Accelerate", "Fractions and Decimals", "Ella"),
    CoverSample("year3-english", "Year 3", "English",
                "Academic Accelerate", "Persuasive Writing", "Noah"),
    CoverSample("year7-naplan", "Year 7", "Numeracy and Literacy",
                "NAPLAN Practice", "Number and Reading", "Mia"),
    CoverSample("year9-english-week4", "Year 9", "English",
                "Academic Accelerate", "Analysing Persuasive Devices", "Sam",
                week="4 of 10  |  Persuasive devices", pill="Weekly Practice"),
)

BY_SLUG = {s.slug: s for s in SAMPLES}


def cover_for(label: str) -> str:
    """The static path of the cover thumbnail that stands in for a booklet.

    `label` is the job label the library prints, e.g.
    "Academic Accelerate - Year 5 - Mathematics - Ella (week 3)". Matching on
    it rather than on a stored program key keeps this working for jobs created
    before covers existed, which is most of them.
    """
    text = (label or "").lower()
    if "naplan" in text:
        return BY_SLUG["year7-naplan"].thumb
    if "english" in text:
        if "week" in text:
            return BY_SLUG["year9-english-week4"].thumb
        return BY_SLUG["year3-english"].thumb
    return BY_SLUG["year5-mathematics"].thumb
