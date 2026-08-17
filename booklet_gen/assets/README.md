# Cover assets

The booklet cover is **drawn in code**, not dropped in as a picture. There is
nothing to add to this folder to change it.

- `COVER_DESIGN_SYSTEM.md` is the founder's design brief and the binding spec.
- `design_reference/` holds the two mockups the brief was written against
  (`english_cover_reference.png`, `math_cover_reference.png`). They are the
  reference for spacing, proportion and tone. Look at them before changing the
  cover renderer.
- The renderer itself is `booklet_gen/visuals/cover.py`. The formatter builds a
  `CoverSpec` (`formatter.cover_spec`) and the page-1 canvas callback
  (`formatter._draw_page_chrome`) hands it to `render_cover`.
- The Folio mark on the cover is the real brand asset,
  `booklet_gen/webapp/static/img/brand/mark-512.png`, used at two sizes. It is
  not duplicated here, so there is only ever one file to update.

## Changing the look

| What | Where |
| --- | --- |
| Background family (light blue, dark navy, white, warm off-white) | `VARIANTS` in `visuals/cover.py` |
| Which booklet gets which family | `variant_for()` in `visuals/cover.py` |
| The wave shapes across the lower third | `_SWEEP` / `_SWEEP_DARK` knots |
| Subject decoration (grids, quote marks, orbitals, matrices) | `_detail_*` functions and `_DETAILS` |
| Wording of the pill, topic line, date and time lines | `cover_pill()`, `cover_topic()`, `cover_spec()` in `formatter.py` |
| The sentence about the answer key | `cover_footer_note()` in `formatter.py`, and read its docstring first |

Run `python scripts/check_cover_design.py` after any change. It renders real
booklets and reads the PDFs back.

## Static image override

There is still an escape hatch for a one-off printed run or a partner-branded
cover. Set an environment variable to a full-bleed portrait A4 image and it
replaces the drawn cover completely, text and all:

    FOLIO_COVER_BACKGROUND=/path/to/design.png

Nothing is printed over it, so the image has to carry its own title, name and
any other wording. Use it knowing that the year level, subject, topic, week and
the answer-key sentence all disappear with the drawn cover.

Dropping a file called `cover_background.png` in this folder no longer does
anything. It used to be picked up automatically, which would have meant the old
static cover silently overriding the new design on every existing install.

## Leftovers

`cover_background.jpg` is the retired static cover. It is kept only because it
is the documented source of the website's line-art motifs (see the comment at
the top of `booklet_gen/webapp/templates/_motifs.html` and `handoff.md`). It is
no longer read by the renderer. The 1.4MB PNG beside it, which nothing
referenced, has been removed.
