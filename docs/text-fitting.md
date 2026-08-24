# Text Fitting & Panel Sizing

How `overlay_text.py` sizes story text and the semi-transparent panel behind it, in both
surfaces that composite text with Pillow. Source of truth: `LayoutSettings` and
`measure_text_block()` in `skills/storybook-render/scripts/overlay_text.py` — this doc
explains the design; the code defines the numbers.

---

## The one box model (PER-104)

```
panel = text block (at the configured font size) + padding
```

The text pushes the panel from the inside. Panel height is **derived from content** —
more words make a taller panel at the *same* glyph size. There is no separate
"comfortable zone" or per-surface font ceiling to fit against; the font size is simply
`layout.font_size` (book-wide, optionally overridden per page), and the box is whatever
that text at that size needs.

Font size shrinks in exactly **one** circumstance: the panel would otherwise exceed its
boundary (`max_panel_fraction` of the page height). That is a last-resort overflow guard,
not a layout mechanism — most pages never reach it.

### `measure_text_block()` — one function, one measurement

`overlay()` (band mode) and `text_page()` (text-page mode) both call the same
`measure_text_block()` to fit the panel, and both `_compose()`'s boundary check and its
draw loop consume that single measurement. This matters: an earlier version measured the
fit-check and the draw with two slightly different line-height formulas (`bbox[3] + 8` vs
`bbox[3] - bbox[1] + 8`) — harmless while the fit loop was conservative, but exactly the
kind of drift that makes a fixed-font guard misfire (shrink when it needn't, or "fit" and
then clip anyway). One function, called once, removes the possibility.

The algorithm:

1. Start at `layout.font_size`. Wrap the text, measure the block height + padding.
2. If it fits `max_box_h`, done — this is the ordinary case, at the ordinary size.
3. If not, shrink 2px at a time, down to `layout.min_font_size` (the overflow-guard
   floor).
4. If the panel still doesn't fit even at the floor, truncate explicitly and report how
   many lines were dropped — see "Loud failure" below. This never happens silently.

Measurement uses the **actual resolved font and the actual text** (wrapped with the same
`_word_wrap` used for drawing), so the fitted size matches what is rendered. Don't
reintroduce a word-count proxy into the fit — it under-counts wrapped lines (paragraph
spacers, long words) and overflows the panel.

## Growth anchors (placement — the one thing that legitimately differs)

Band mode and text-page mode differ only in **where the panel is anchored**, which
determines which direction it grows. The sizing math above is identical either way.

**PER-105:** every anchor below is relative to the **art rect**, not the canvas —
`(0, 0, w, h)` when no page frame (`border`) is set, so nothing here changes for a book
that doesn't use one. See `docs/page-frame.md` for the art rect itself.

| Surface | Anchored | Grows | Panel corners |
|---|---|---|---|
| band, `bottom` | art rect's bottom edge, drawn `radius` px past it | upward | rounded, lower corners fall off the art (not necessarily the canvas) |
| band, `top` | `EDGE_MARGIN_FRACTION` (4%, a module constant — see below) from the art rect's top | downward | all four rounded |
| text page | center of the art rect — `box_y0 = ay0 + (ah - box_h) // 2` | both directions, symmetrically | all four rounded |

`overlay_text.py:_compose()`'s `anchor` parameter (`"top"` / `"bottom"` / `"center"`) is
the only place this distinction lives. The panel mask is additionally clipped to the art
rect's own (rounded) shape when a border is set, so the band can never spill onto the
margin — the defect PER-105 exists to fix.

## The boundary

One shared setting, `layout.max_panel_fraction` (default `0.9`) — the most of the page
height a panel may cover before the font starts giving way. Same value, both surfaces;
where the free space *lands* differs by anchor (that's the growth-anchors table above),
not the boundary itself:

- **bottom-anchored band**: one edge pinned, free space collects entirely above.
- **top-anchored band**: pinned at the top margin, so it additionally must leave the
  matching gap below — `max_box_h = min(int(h * max_panel_fraction), h - 2 * edge_margin)`.
  At the default 0.9 / 4% margin, the 0.9 term wins and this second cap never actually
  binds.
- **text page (centered)**: runs out of room top and bottom simultaneously.

`EDGE_MARGIN_FRACTION` (top-placement gap, 4% of page height) stays a plain module
constant, **not** part of `layout` — it is placement, not the boundary, and it's already a
fraction (already resolution-independent). Deriving it from `max_panel_fraction` would
couple two unrelated knobs and re-flow every top-placed page for no reason.

**Accepted consequence:** a generous boundary means a wordy overlay band can cover a large
share of the artwork before anything shrinks — a 200-word page at the default settings
reaches roughly 40% of a square page. That's correct under content-determines-panel: the
boundary exists to bound a runaway page, not to keep panels small.

## Reference units

Every length in `layout` (all of it except `max_panel_fraction`, which is already a
fraction) is authored as pixels **at a declared `reference_size`** (default 2048) and
scaled by one uniform factor at composite time:

```
scale = min(page_w, page_h) / reference_size
```

`LayoutSettings.scaled(w, h)` applies this to `font_size`, `min_font_size`, `pad_h`,
`pad_v`, `radius`, and `feather` in one place. One scalar — not width-for-horizontal,
height-for-vertical — so type and padding scale together and changing the book's aspect
ratio never changes type size on its own.

This is what makes a 1K OpenAI-fallback page, a 2K Gemini page, and a future upscaled 4K
page all compose identically: the same `story.json` renders the same *proportions*
regardless of which pixel grid the page actually is. Before this, fonts/padding were
absolute px while panel ceilings were fractions of height — invisible at a uniform 2048px,
and wrong the moment one page wasn't (`aspect_to_size()` already hard-codes 1024 on the
OpenAI paths today).

## Settings — `story.json`'s `layout` object

```json
"layout": {
  "reference_size": 2048,
  "font_size": 48,
  "min_font_size": 22,
  "padding": { "h": 40, "v": 48 },
  "max_panel_fraction": 0.9,
  "radius": 36,
  "feather": 14
}
```

All optional — omit any key to keep `overlay_text.py`'s own default for it, omit the
whole object to keep every default. A per-page `font_size` field overrides
`layout.font_size` for one page (e.g. a larger cover title) without touching the rest of
the book; absent, it inherits the book value. See `story_schema.json`'s `layout` and
`pages[].font_size` for the authoritative field docs.

`box_alpha` (panel opacity, 0–255) stays outside `layout` — it's unitless, nothing to
scale, still a module constant plus the `--box-alpha` CLI flag.

A separate, optional top-level `border` object (PER-105) shares `layout.reference_size` as
its own unit baseline but is otherwise independent — it composites a margin/frame around
the artwork rather than sizing the text panel. See `docs/page-frame.md`.

## Loud failure (never silent)

If even `min_font_size` can't make the panel fit `max_panel_fraction`, `measure_text_block`
truncates the wrapped lines explicitly and reports the count. The composite still happens
— **warn and composite**, never fail the book over one over-long page — but a warning
prints to stderr with a stable, greppable marker:

```
TEXT-OVERFLOW: page-04-long-text.png — configured 48px, used 22px, 3 line(s) dropped
```

`render_book.py`'s `run_overlay`/`run_text_page` forward `overlay_text.py`'s stderr into
the page's own log unconditionally (not only on a non-zero exit — a warn-and-composite page
exits 0), so the warning survives into `render_book.py`'s own stdout. The editor's page
regenerate job scans for this marker and surfaces it next to the page preview.

Before this model, silent clipping was near-unreachable — the old fit loops shrank all the
way to a 12px absolute floor first, so almost any text fit somewhere. Under a fixed font
size, a large book-wide `font_size` plus one wordy page reaches this path by ordinary use,
not by pathology, which is why it had to stop being silent.

## PER-100: `right` alignment

`left`, `center`, and `right` are all defined against the **text column**
`[h_pad, w - h_pad]`, not raw canvas width:

- `left`: `x = h_pad`
- `center`: centered within the column
- `right`: right edge of the line lands at `w - h_pad`

One shared branch in `_draw_text_lines()`, used by both surfaces — previously duplicated
verbatim in `overlay()` and `text_page()`.

## Tuning

Every `layout` key is a CLI flag too (`--font-size`, `--min-font-size`, `--pad-h`,
`--pad-v`, `--radius`, `--feather`, `--max-panel-fraction`, `--reference-size`), alongside
`--box-alpha` and `--align left|center|right`. Tuning is zero-cost: re-run
`overlay_text.py` against a committed fixture page (see "Smoke-testing changes" in
CLAUDE.md) — no API call involved. The page frame (PER-105) has its own flag group
(`--frame`, `--border-width`, `--border-color`, `--border-radius`, `--shadow`,
`--shadow-offset`, `--shadow-blur`, `--shadow-opacity`) — see `docs/page-frame.md`.
