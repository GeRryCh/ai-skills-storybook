# Future Explorations

Ideas researched but not yet implemented. Left here for reference when revisiting
consistency or quality improvements.

---

## Global style-frame image (B) — opt-in experiment

**Status:** researched, not implemented. See Linear issue PER-33 for the full analysis.

### What it is

Feed one fixed abstract "style frame" image as an additional reference input into every
page render call. The frame would be generated once at the stylesheet stage (alongside
character sheets) from a style-board prompt and stored as `style-frame.png`.

### Why it might help

Google's consumer Gemini image product documents the ability to "apply style, texture, and
color from reference images." Feeding the same reference into every page call could lock
palette, material feel, and lighting more concretely than the text-only `style_guide` block.

### Why it's uncertain

Google's own `Book_illustration` cookbook achieves cross-page consistency with **text only**
— it passes no image references into scene calls. There is no official documentation
confirming that this technique works for the `gemini-*-image` / `generate_content` path.
The concern is that it's an extrapolation from the consumer product, not an API-tested claim.

### Critical design rule if attempted

The frame **must be an abstract style board, never a scene.** Character sheets work because
`build_char_prompt()` forces a flat neutral background (no scenery → no content bleed). A
frame depicting a forest would leak forest environment into every page, including kitchen
pages. The frame must contain: palette swatches, texture/material samples, a lighting study,
at most one representative prop — flat plain background, no scenery, no characters, no text.

### Suggested implementation sketch

1. `make_style_sheet.py` — add `--style-frame` flag: generate `style-frame.png` from a
   style-board prompt built from `build_style_block()` via the existing `generate_image()`
   function; write path to a new top-level `style_frame` field in `story.json`.
   Idempotent (skip if file exists).
2. `story_schema.json` — declare optional top-level `style_frame` (string, path).
   Required because the schema sets `additionalProperties: false`.
3. `render_book.py` `collect_input_images()` — if `story["style_frame"]` is set and
   exists on disk, insert it as a candidate with priority:
   hero sheet → hero photo → **style frame** → remaining cast sheets, into the 4-slot cap.
   On 3-character pages the third supporting sheet drops (logged, not silently).
4. Cost: +1 one-time Gemini call at the (already gated) stylesheet stage; zero recurring
   cost (input images don't add output-token cost).

### How to validate

A/B: render the same page twice — once without `style_frame`, once with — and compare.
Look for tighter palette/lighting lock without unexpected environment leakage.

---

## Seed parameter (A/B validation rider)

**Status:** researched, concluded not effective, but not empirically tested.

The `seed` field is accepted by `types.GenerateContentConfig` and won't error, but
Google's image-generation documentation never mentions it for the `gemini-*-image` models
and makes no cross-call reproducibility or consistency guarantee. The documented image seed
belongs to the older Imagen family (`generate_images`, requires `add_watermark=False`) —
a different API path incompatible with the multi-reference-image pipeline.

If you want empirical confirmation rather than a docs-negative verdict: render one page
twice with an added `types.GenerateContentConfig(seed=12345, ...)` and byte/visual compare.
This converts "undocumented" into "tested, no effect" (or surfaces a surprise). Remove the
`seed` parameter from committed code either way — it is not the lever.
