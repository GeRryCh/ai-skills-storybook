# Future Explorations

Ideas researched but not yet implemented. Left here for reference when revisiting
consistency or quality improvements.

---

## Global style-frame image (B) — opt-in experiment

**Status: IMPLEMENTED — PER-82 ("Lever B").** See `story.json`'s top-level `style_frame`
field, `make_style_sheet.py`'s book-wide style-frame generation step, and
`render_book.py`'s `collect_input_images()`. The sketch below is left for historical
context; the shipped design corrects three things it got wrong (see the note at the
bottom) and is stricter on one point the ticket's own summary was ambiguous about — see
CLAUDE.md's `style_frame` paragraph and the PER-82 plan for the reasoning. Originally
researched under Linear issue PER-33.

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

### What actually shipped differently (PER-82)

The sketch above predates PER-65, PER-83, and PER-84, and is stale in three ways the
PER-82 implementation corrects:

- **Vendor:** step 4 says "one-time Gemini call" — Stage 2 moved to OpenAI `gpt-image-2`
  before this shipped. The frame is generated via `make_style_sheet.py`'s existing
  `generate_image()` (OpenAI), not Gemini.
- **Priority chain:** step 3's `hero sheet → hero photo → style frame → remaining cast
  sheets` includes a hero *photo* — PER-65 removed hero photos from render-time refs
  entirely (characters contribute sheets only). The shipped priority is: character lane
  (unaffected) → location refs → object refs → **style frame last**, within the object
  lane, not interleaved into the character lane.
- **Cap model:** step 3's "4-slot cap" is the old flat cap. PER-83 replaced it with two
  independent lanes (character 4 flash/5 pro, object+location — PER-96: 10 flash/6 pro,
  not 10 on both — total 14 flash/11 pro). The style frame rides the **object lane**,
  tagged lowest priority — it never competes with or upgrades the character lane, but on
  pro it now drops noticeably more often (a smaller lane fills up sooner).
- **`--only` interaction (not in the sketch at all):** the shipped frame-generation step
  is skipped when `make_style_sheet.py` is invoked with `--only ID`. `--only`'s contract
  is exactly one top-level diff (that entry's `style_sheet`) — the visual editor's
  per-cast regenerate flow (`edit_story.py`'s `_run_sheet_regen` + `editor.html`'s
  `fetchSheetVersions`) resyncs only that one field into the client's in-memory
  `story` object after an `--only` run, refreshing `storyMtime` to match disk without
  refetching everything. A second, unsynced top-level field write (the frame, on its
  first-ever generation) would silently vanish on the next editor save — the client's
  stale in-memory copy would overwrite the on-disk field, and the mtime guard wouldn't
  catch it because `storyMtime` was already refreshed. The frame is a full-run-only
  asset for this reason; this is the constraint most likely to get silently re-broken
  by a future edit, since it isn't visible from reading `collect_input_images` or the
  schema alone.

One more thing worth flagging for the next reader: the ticket's own one-line summary
("a sample environment + palette swatches + a patch of linework") is in tension with its
own next clause ("no scenery that could bleed"). The shipped prompt follows the stricter
reading — **no environment at all**, only palette/texture/lighting samples — consistent
with the "Critical design rule" above and with `.claude/rules/script-authoring.md`'s
per-page-location-only rule. If a future revision wants literal "sample environment"
content, that's a deliberate reopening of the PER-33 bleed question, not a bug to fix.

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
