# Cover background

Drop your booklet cover design here as:

    cover_background.png

Requirements:
- Portrait A4 aspect ratio (e.g. 1240 x 1754 px or larger).
- Leave the vertical centre clear. Folio prints the text (product line,
  subject, year, student name, date, estimated time) into that space.
- PNG or JPG.

When this file is present, every booklet cover uses it automatically and
the running header/footer is hidden on the cover page. If it is absent,
Folio falls back to a plain text cover.

You can also point at an image anywhere on disk instead of putting it here,
by setting an environment variable:

    FOLIO_COVER_BACKGROUND=/path/to/your/design.png
