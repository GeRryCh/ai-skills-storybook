---
name: storybook-render
description: >
  Stage 3 of 4 in the storybook pipeline — render the illustrated pages.
  Use when an approved story.json AND a style-sheet.png already exist (from
  storybook-story + storybook-stylesheet) and the user wants to generate, re-render,
  or fix page illustrations — e.g. "render the book", "render the pages", "re-render
  page 3", "regenerate the pages", "redo the cover". Generates one illustration per
  page (using the style sheet as the consistency anchor) and overlays the story text.
  Costs one image API call per page. Output is page images only — book file assembly
  (PDF/EPUB) and packaging happen in Stage 4 (storybook-consolidate), free, after
  the user reviews the rendered pages.
  If the style sheet is missing, run storybook-stylesheet first; if story.json is
  missing, run storybook-story first.
metadata:
  requires:
    bins:
      - uv
    env:
      - GEMINI_API_KEY
---

# Storybook — Stage 3: Render Pages

## Preconditions

- `{out_dir}/story.json` exists (from **storybook-story**).
- `{out_dir}/style-sheet.png` exists and `story.json` has `style_sheet_path` set (from **storybook-stylesheet**). The script warns and produces weaker consistency if it is missing.
- `GEMINI_API_KEY` is set; `uv` is installed. The script calls the Gemini image API directly (no sibling skill needed).

If the style sheet is missing, run **storybook-stylesheet** first.

---

## Render

```bash
uv run {skillDir}/scripts/render_book.py \
  --story {out_dir}/story.json
```

**Useful flags:**
- `--from N` — resume from page N (skips earlier pages, also skips any already-existing files)
- `--only N` — render a single page (good for testing one page before a full run, or re-doing one page).
- `--resolution 1K|2K|4K` — override the resolution from `story.json` for this run. Resolution is normally configured in `story.json` via the top-level `resolution` field (default `2K` when not set); pass this flag to override it ad-hoc. `1K` is faster/cheaper for drafts; `4K` for large-format print.
- `--aspect-ratio RATIO` — override the aspect ratio from `story.json` for this run (choices: `1:1` `2:3` `3:2` `3:4` `4:3` `4:5` `5:4` `9:16` `16:9` `21:9`). Aspect ratio is normally configured via the top-level `aspect_ratio` field in `story.json`; when neither is set the model chooses framing per call.
- `--model gemini-3.1-flash-image|gemini-3-pro-image` — override the image model for every page this run. Normally set per-page or book-wide in `story.json` (precedence: `--model` flag > `pages[].model` > top-level `model` > flash default). Flash (default): faster/cheaper, character-lane cap 4. Pro: higher quality, character-lane cap 5. Style sheets always use pro regardless. References ride two lanes (PER-83): a character lane and an object lane (objects + locations, cap 10 on both models, 14 total). **Auto-upgrade:** when a page's **character** lane has ≥5 images and the effective model is flash (including an explicit `--model gemini-3.1-flash-image` or per-page override), that page is automatically upgraded to `gemini-3-pro-image` for that call only; logged as `auto-upgraded page N to gemini-3-pro-image (5 characters > flash character-lane cap 4)`; `story.json` is never modified. Manually pinning a page's model to pro solely to avoid the character-lane cap is therefore no longer needed. Object-lane overflow never triggers an upgrade. **Retry workflow:** set a page's `model` to `gemini-3-pro-image` in `story.json`, then `rm pages/page-NN*.png` and re-run `--only N`.
- `--text-mode overlay|native|long` — override the text mode for every page this run. Normally set per-page or book-wide in `story.json` (precedence: `--text-mode` flag > `pages[].text_mode` > top-level `text_mode` > native default). A page-level `text_mode` field in `story.json` lets individual pages differ from the book default without this flag — e.g. one long-mode page in an otherwise native book. This flag overrides all page-level and book-level fields for the entire run. Mixed-mode books produce mixed filename suffixes in `pages/` (e.g. some `page-NN-native.png`, some `page-NN-long.png` pairs).
- `--saved-formats pdf epub|none` — override `story.json`'s `saved_formats` for this run: which book file(s) to assemble after a full render. `epub` is a fixed-layout EPUB3 (pre-paginated, full-bleed pages). `none` skips assembly entirely (useful for partial `--from` runs where more pages are coming). `saved_formats` is normally configured in `story.json` (default: all formats when omitted).

All pages are fired concurrently via `asyncio` — one async Gemini request per page, no thread pool and no concurrency cap. Pages are independent (each call only uses the shared style sheet + character refs), so wall-clock ≈ the slowest single page. Transient `429`/`5xx` responses are retried automatically with exponential backoff + jitter (honoring `Retry-After`), so a momentary rate-limit no longer drops a page.

Output:

| Mode | Art file | Text file |
|------|----------|-----------|
| overlay | `pages/page-NN.png` | (same file) |
| native | `pages/page-NN-native.png` | (same file) |
| long | `pages/page-NN-long.png` | `pages/page-NN-long-text.png` |

In long mode the cover (page 1) is a single combined page — `pages/page-01-long.png`. Body pages with empty `text` emit an art-only page (no text page generated). Text pages sit on ONE shared model-generated background per book — `pages/text-bg-long.png` (+1 paid call total, generated once before the pages fire; it reserves a low-detail central area for the text panel). A page with `text_background_prompt` gets its own dedicated background instead (`pages/page-NN-long-bg.png`, +1 call for that page). Cost: N art calls + 1 shared-bg call.

Each final file is printed as `MEDIA: <path>` so the IDE can display it inline.

**Re-rendering individual artifacts (long mode):**
- To force-regen the art image: `rm pages/page-NN-long.png` (also `rm pages/raw-page-NN-long.png` for the cover raw), then re-run.
- To force-regen only the text page (Pillow-only, free, no key needed as long as the background PNG exists): `rm pages/page-NN-long-text.png` then re-run.
- To force-regen the shared text-page background (+1 paid call): `rm pages/text-bg-long.png pages/page-NN-long-text.png` (all text pages that should pick it up), then re-run.
- To force-regen a per-page dedicated bg (only if `text_background_prompt` set): `rm pages/page-NN-long-bg.png pages/page-NN-long-text.png` then re-run.

For overlay/native: `rm pages/page-NN{-native}.png` (and `pages/raw-page-NN.png` for overlay), then `--only N`.

---

## Location references

If `story.json` has `cast` entries with `kind: "location"` and a page lists one of those
ids in its `pages[].cast` array, that place's Stage-2 reference sheet (`style_sheet`) is
sent as an additional reference image — the same mechanism as characters and objects. When
the entry has no sheet (e.g. a book rendered before Stage 2 was re-run with PER-50), the
first `ref_image` photo is sent as a fallback instead (logged). Location references ride
the **object lane** (PER-83) — the same lane as objects, cap 10 on both models — and
**outrank** objects within it: a wrong-style background poisons the whole frame, a
slightly-off prop does not.

> **character lane** (cap 4 flash / 5 pro): hero sheet → remaining character sheets
> **object lane** (cap 10, both models): **location ref (sheet, or photo fallback)** → object refs → **book-wide style frame** (lowest priority)

Flash pages whose **character** lane exceeds 4 are **auto-upgraded to pro** before any
character is dropped (see `--model` above). Anything past the pro character-lane cap (5),
or past the object-lane cap (10), is logged (never silently dropped). Object-lane overflow
never triggers an upgrade. On scenery-only pages
(`"cast": []`) the location photo is the sole reference image (plus the style frame, if set).

Each reference is sent with a short identifying note in the Gemini call so the model knows
a location sheet or photo is the setting, not a character. Unknown ids and missing
files degrade to a logged warning and skip — they never fail the render.

**Book-wide style frame (PER-82, "Lever B").** When `story.json`'s top-level `style_frame`
is set (written by `make_style_sheet.py`, Stage 2) and the file exists on disk, it's sent as
the lowest-priority object-lane reference on **every** page call for the book — including
scenery-only pages and the long-mode text-background calls, since it's sourced from the book
level, not from that page's `cast`. It anchors the look of everything that isn't cast
(backgrounds, crowds, lighting, props), which otherwise drifts from text alone toward the
model's world-knowledge default. It's an abstract style board (palette/texture/lighting
samples), never a scene — a missing or absent frame is just today's behavior (soft, not a
hard requirement like a location's sheet).

## `<id>` placeholder substitution (PER-56)

`pages[].cast` holds cast **ids** (e.g. `["pip", "major-oak"]`). `image_prompt` uses `<id>` placeholders. Before every Gemini call, `render_book.py`'s `resolve_cast_placeholders()` substitutes each `<id>` with the cast entry's display `name` — the model always sees real names, never id tokens. Unknown or malformed `<id>` tokens (e.g. `<Pip>` with wrong case) are stripped of angle brackets and logged; the validator (`validate_story.py`) is the hard gate that prevents them.

---

## Cost & failure notes

- Each page = one Gemini image call. 8 pages = 8 calls. Long mode adds 1 call for the shared text-page background (8 pages = 9 calls), plus 1 per page that sets `text_background_prompt`. The book-wide style frame (PER-82) is generated once in **Stage 2**, not here — it costs no extra Stage 3 calls, only 1 extra ref slot/page (free under the object-lane cap unless that lane is already maxed).
- **Strongly suggest** a 2-page proof run first: `--only 2` then `--only 3`.
- On any error, re-run with `--from N` — already-rendered pages are skipped.
- API errors: check `GEMINI_API_KEY` is set, `uv` installed, and Gemini account has credits.
- Resolution defaults to 2K (from `story.json`'s `resolution` field, or the 2K built-in fallback). Override ad-hoc with `--resolution 1K|2K|4K`.
- Aspect ratio defaults to unset (model chooses per call) unless `story.json`'s `aspect_ratio` field is set. Override ad-hoc with `--aspect-ratio`.

---

## Fonts (bundled, OFL licensed)

| Key | File | Use |
|-----|------|-----|
| `reader` | `Andika-Regular.ttf` | Body text — literacy-designed, open letterforms |
| `display` | `PatrickHand-Regular.ttf` | Cover/title overlays |

These are **roles**. Each page's `font` field in `story.json` selects the role (default `reader`); set the cover to `"font": "display"` for a title look.

**Custom fonts (config-level).** A top-level `fonts` map in `story.json` redefines what each role's font is:
```json
"fonts": { "reader": "Arial", "display": "Patrick Hand" }
```
Family names resolve at render time, in order: (1) bundled asset in `assets/fonts/`, (2) system-installed font (Arial, Georgia, Helvetica… — no manual install needed), (3) the bundled role default + a warning if the name can't be found. Rendering rasterizes to pixels, so using a system font does not redistribute the font file. Omit `fonts` to keep bundled Andika/PatrickHand.

Ad-hoc test: `overlay_text.py --font display --font-name "Arial"` (the `--font` role is the fallback if `--font-name` can't be resolved).

---

## Quick overlay / text-page test (no API cost)

```bash
# Band mode (overlay/native covers):
uv run {skillDir}/scripts/overlay_text.py \
  --image /path/to/any.jpg \
  --text "Once upon a time there was a brave little hedgehog." \
  --placement bottom \
  --out /tmp/test-overlay.png

# Text-page mode (long mode body pages — --image is the text-page background):
uv run {skillDir}/scripts/overlay_text.py \
  --image /path/to/text-bg.png \
  --text "Long story text that belongs on its own page." \
  --text-page \
  --out /tmp/test-textpage.png
```

**Band mode (overlay/native):** text sits on a soft, feathered white panel anchored to the top or bottom. Tune with:
- `--box-alpha N` — panel opacity 0–255 (default `205`)
- `--feather N` — edge blur radius in px (default `14`; `0` = hard edge)
- `--align left|center` — horizontal text alignment (default `left`)

**Text-page mode (`--text-page`, long mode):** `--image` is a purpose-made background (the render stage generates it with a reserved central text section); it is used as-is — no blur, no wash. Story text sits on a vertically centered, feathered panel. Tune with:
- `--canvas-from PATH` — scale-to-cover + center-crop `--image` to the dims of this art image, so the text page matches its art page

**Sizing (band mode):** font shrinks from 72px toward a 22px floor; panel capped at ⅓ of page height.

**Sizing (text-page mode):** font shrinks from 96px toward a 22px floor; panel capped at ~80% of page height — large enough for ~80–200 words per logical page.

Per-page `story.json` fields: `text_placement` (top/bottom/floating, default floating), `text_color_hint` (dark/light, default dark — affects all text modes: dark = near-black ink on a light panel in overlay/long, warm dark ink baked in for native; light = near-white ink on a dark panel in overlay/long, cream-white ink with a forced dark backdrop area for native; default dark is unchanged from prior behaviour), `text_align` (left/center), `font` (reader/display), `model` (gemini-3.1-flash-image/gemini-3-pro-image — retry knob; unset inherits book-level `model` or flash default), `text_mode` (overlay/native/long — per-page override; unset inherits book-level `text_mode` or native default; lets individual pages differ from the book default), `text_background_prompt` (long mode only — per-page dedicated text-page background overriding the shared one, +1 paid call). Top-level `text_background_prompt` customizes the shared book-wide text-page background (relevant when any page's effective mode is long).

---

## Handoff

After a full render, show all `MEDIA:` page images to the user, plus the cost summary
(PER-35) the script prints when it made at least one paid call: `Cost this run: $X.XX
(N calls)` and `Book total: $Y.YY (M calls) [out_dir/costs.jsonl]`. **Stop for review.** This
is the point of the Stage 3/4 split — the user reviews the rendered pages before committing
to assembly. Fix any pages with `--only N` and re-render as needed.

Once the user approves:

```
All pages approved → ready for Stage 4.
Next: storybook-consolidate (Stage 4) assembles the finished book files (PDF /
      fixed-layout EPUB3) and can zip the book for sharing — free, no API calls.
```

Do not proceed to Stage 4 until the user has reviewed the pages and approved.
