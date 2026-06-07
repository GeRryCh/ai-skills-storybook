# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A set of **three Claude Code skills** that together turn a story idea (optionally with
character photos) into a fully illustrated children's picture book. It is not an app — it
is skill definitions (`SKILL.md`) plus the Python scripts they invoke. There is no build
step and no test suite; the scripts are the product.

## The pipeline (read this first)

Three skills run in order and hand off a **single file, `story.json`**, in an output
directory (default: the user's cwd, e.g. this worktree root):

1. **storybook-story** (free, no API) — views any supplied photos (free, in-session), crops
   multi-person photos to one file per person via `scripts/crop_character.py` (Pillow only,
   no API), then writes `story.json`: per-page `text`, `image_prompt`, per-page `characters`
   cast list, and a global `characters` array. **Has a hard approval gate** — it must stop
   and wait for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call per character) — generates one
   `style-sheet-{name}.png` per character from the `characters` array, writes each
   character's `style_sheet` path back into `story.json`. **Approval gate**: show all
   sheets, get confirmation before rendering — a wrong sheet poisons every page that
   character appears on.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using only the style sheets for the characters listed in that page's `characters` field
   (per-page selection, cap 4 for the flash model), then overlays text. Three text modes:
   - `overlay`: `pages/page-NN.png` (art + Pillow text panel)
   - `native`: `pages/page-NN-native.png` (model bakes text into art)
   - `long`: `pages/page-NN-long.png` (full-bleed art, no text) + `pages/page-NN-long-text.png`
     (separate text page: feathered panel over a background image, Pillow-composited).
     Cover (page 1) stays combined (`pages/page-01-long.png`). Text pages sit on **one
     shared model-generated background per book** (`pages/text-bg-long.png`, generated
     once in `render_all` *before* pages fire — generating it inside the concurrent
     `render_page` would race), prompted with a reserved low-detail central text area
     and no characters; top-level `text_background_prompt` customizes it. A page-level
     `text_background_prompt` gives that page its own dedicated bg instead
     (`pages/page-NN-long-bg.png`, +1 paid call). **Cost: N art calls + 1 shared-bg
     call. Text-page composition itself is free Pillow work (rebuildable without an
     API key while the bg PNG exists).**
   After a full render, assembles book file(s) via `merge_pdf.py` / `merge_epub.py` at no
   extra API cost. Long mode outputs `{title}-long.pdf` / `{title}-long.epub`.

`story.json` is the contract between stages; its schema is `skills/storybook-story/assets/story_schema.json`.

A top-level `style_guide` object is **required** in `story.json`: both paid scripts
assemble it into one byte-identical style block (`build_style_block()`, duplicated in
both scripts — keep the copies in sync) injected verbatim into every Gemini call. This is
the book-wide consistency mechanism (each page is a separate stateless call). Both scripts
**refuse to run** (`require_style_guide()`, exit 2) when it is missing or empty — breaking
change for pre-existing `story.json` files; add the field to render old books. The `style`
string remains as a short human label only.

## Critical external dependency

The two paid scripts call the **Gemini image API directly** (via the `google-genai`
Python SDK, declared as a PEP-723 inline dependency). They build a `genai.Client` with
`api_key` from the environment, model `gemini-3-pro-image` (style sheets, up to 5 character
reference images per call) or `gemini-3.1-flash-image` (page renders, up to 4 reference
images per call), send the prompt plus reference images as `types.Part.from_bytes`, and
extract the returned image from `part.inline_data.data`.
Requires `uv` on PATH and `GEMINI_API_KEY` in the environment. No sibling skill is
needed (an earlier version shelled out to `nano-banana-pro-openrouter`; that logic is now
inlined in each script — `run_nano_banana()` in `render_book.py` and `generate_image()` in
`make_style_sheet.py`).

## Running the scripts

All scripts are PEP-723 inline-dependency scripts — always run with `uv run` (it resolves
deps like Pillow automatically), never `python`:

```bash
# Stage 1 — crop one person from a multi-person source photo (free, Pillow only, no API)
# Run once per character extracted from a group photo; overwrites --out on each run.
uv run skills/storybook-story/scripts/crop_character.py \
  --image /path/to/family.jpg \
  --box 0.05,0.10,0.48,0.95 \
  --out {out_dir}/ref-mia.png

# Stage 2
uv run skills/storybook-stylesheet/scripts/make_style_sheet.py --story story.json

# Stage 3 — render all pages (1K default; all pages fired concurrently via asyncio)
uv run skills/storybook-render/scripts/render_book.py --story story.json

# Render / re-render a single page (proof before a full run)
uv run skills/storybook-render/scripts/render_book.py --story story.json --only 3

# Text overlay only, no image API cost (verify Pillow + fonts)
uv run skills/storybook-render/scripts/overlay_text.py \
  --image any.png --text "Once upon a time..." --placement bottom --out /tmp/t.png

# Merge already-rendered pages into a PDF (no API cost)
uv run skills/storybook-render/scripts/merge_pdf.py --story story.json
# native mode PDF:
uv run skills/storybook-render/scripts/merge_pdf.py --story story.json --text-mode native

# Merge already-rendered pages into a fixed-layout EPUB3 (no API cost)
uv run skills/storybook-render/scripts/merge_epub.py --story story.json
# native mode EPUB:
uv run skills/storybook-render/scripts/merge_epub.py --story story.json --text-mode native
```

`render_book.py` flags: `--from N` (resume), `--only N`, `--resolution 1K|2K|4K`,
`--aspect-ratio RATIO` (override from story.json; unset → model chooses),
`--text-mode overlay|native`, `--saved-formats pdf epub|none` (override story.json
`saved_formats`; default when neither set: all formats).
Pages are independent and all fired concurrently via `asyncio` (one async Gemini
request per page, no thread pool, no concurrency cap). Transient 429/5xx are retried
with exponential backoff + jitter, so wall-clock ≈ the slowest single page.

After a full render (`--only` not set), `render_book.py` automatically assembles book
file(s) per `saved_formats` (story.json field → CLI override → default all). Writes
`{title}.pdf` / `{title}.epub` (overlay) or `{title}-native.*` (native) next to
`story.json`. Pass `--saved-formats none` to suppress (e.g. for `--from` partial runs).

## Idempotency / re-run semantics (important when editing scripts)

Both paid scripts **skip work whose output already exists**: `make_style_sheet.py` skips any
`style-sheet-{name}.png` that already exists (per-character, so re-running only generates
the missing ones); `render_book.py` skips any `page-NN.png` that exists. To force a
regenerate you must `rm` the target file (and the matching `raw-page-NN.png` for a page)
first. Preserve this behaviour — it makes partial-failure re-runs cheap.

## Key design decision: explicit cast, never prose-scraped

The `characters` array in `story.json` is authored explicitly and is the **only** source for
the style sheets. An earlier regex that scraped characters from prose minted phantom
characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit")
and poisoned every page. Do not reintroduce auto-extraction. See the docstring on
`get_characters()` in `make_style_sheet.py`. Reference images come only from that
character's own `ref_image` (a single path or an array of paths), capped at 5 (Gemini 3
Pro Image character-lane limit). There is no shared global pool — the cast-to-photo mapping
is fixed in Stage 1, so one character's photo never bleeds into another's sheet.

**Single-person images only.** Every path in `ref_image` must show only one person — a solo
photo or a per-person crop. If a source photo contains multiple people, Stage 1 crops it
to one file per character via `skills/storybook-story/scripts/crop_character.py` (Pillow
only, free, no API). The original multi-person photo is never listed in any `ref_image`. This
preserves the existing cap math (5 Stage-2 / 4 flash cap) unchanged — refs stay
per-character and per-person, so nothing interacts differently with the caps.

Re-run recipe for a bad crop: re-run `crop_character.py` with an adjusted `--box` (it
overwrites silently — free to iterate) → `rm style-sheet-{slug}.png` → re-run Stage 2.
Cross-session note: crop provenance is not stored in `story.json` (intentional — same rule
as `style_sheet`). To redo a crop in a new session you need the original source photo again.

Each page also carries an explicit `characters` list (`pages[].characters`) naming which
cast members appear on it. `render_book.py`'s `collect_input_images(story, page)` uses
this to send only the relevant per-character style sheets — the model never sees sheets
for characters not on the page. The **first** name in `pages[].characters` is the page
**hero**: it additionally contributes its first `ref_image` (a solo photo or a Stage-1
crop), so the render anchors the hero's facial likeness on the real photo, not only on the
(lossy) style sheet. **Convention: author the hero/child first in each page's cast list.**
Priority into the 4-image cap (flash) is hero sheet → hero photo → remaining characters'
sheets in order; anything past the cap is logged, never silently dropped.

**Outfit lock (single canonical outfit per character).** `characters[].appearance` must
name exactly one outfit; the style-sheet prompt takes clothing from there, never from
`ref_image` photos (which may show the character in multiple outfits). Stage 3 takes
clothing from the sheet, not the hero photo. This locks one outfit per character across
the whole book. To change a character's outfit, edit `appearance`, delete the existing
style-sheet PNG, and re-run `make_style_sheet.py`.

## Location photo references (PER-38)

Real named places (landmarks, cities, named buildings) can contribute a photo reference
during page rendering — e.g. "the Eiffel Tower" prompts a search for a real photo.

**Stage 1 (in-session, free):** the agent detects real named places in the story, calls
`perplexity_search` to find a Wikimedia Commons freely-licensed photo, builds a
deterministic download URL (`Special:FilePath/<File-title>?width=1600`), and runs
`fetch_location.py` to download and validate it. If the Perplexity MCP is absent,
locations are skipped with a user-facing message — Stage 1 never fails over this.

**`fetch_location.py`** (`skills/storybook-story/scripts/fetch_location.py`): PEP-723,
Pillow + stdlib `urllib`. Validates HTTP status, `content-type: image/*`, decodes with
Pillow, checks min edge (≥512px default), downscales to max edge (≤1536px default), mode-
normalises to RGB, saves as JPEG. Prints `MEDIA: {out}` for inline preview. Zero-cost
smoke test:

```bash
uv run skills/storybook-story/scripts/fetch_location.py \
  --url "https://commons.wikimedia.org/wiki/Special:FilePath/Tour_Eiffel_Wikimedia_Commons.jpg?width=1600" \
  --out /tmp/smoke-loc.jpg
```

**Schema fields:** optional top-level `locations` array (`[{name, ref_image, description?,
source_url?}]`) + optional `pages[].location` string. `ref_image` is a plain string (one
path; no array). Declared in `story_schema.json` — `additionalProperties: false` at both
levels means declarations are required for the editor not to warn. `pages[].location` must
match a `locations[].name` exactly. Per-page selection is mandatory to prevent environment
bleed (PER-33 lesson: a location photo used book-wide bleeds the place's environment into
every page, including pages set elsewhere).

**Cap priority in `collect_input_images` (render_book.py):**

> hero sheet → hero photo → remaining cast sheets → **location photo (lowest)**

The location photo is appended last in the candidates list and is the first to drop from the
4-image cap. Drops are logged, never silent. On scenery-only pages (`characters: []`) with a
`location` set, the location photo is the sole reference image. Unknown location names and
missing files degrade to warnings + skip, never a render failure.

**Labeled-interleaved contents (`run_nano_banana`):** `collect_input_images` now returns
`(label, path)` pairs (it already built labels, then discarded them). Each reference image
is preceded in the Gemini `contents` list by a short text part: `"Next image: {label}."`.
This tells the model whether each image is a character style sheet, a character photograph,
or a location photograph. The `IMAGE_SYSTEM_PROMPT` defines the behaviour rule for each
kind. Keep label wording in sync with the system prompt's "kind" vocabulary:

- `"character style sheet for {name}"` → "defines design, outfit, art style"
- `"real photograph of the character {name} (facial likeness reference)"` → "face only, outfit from sheet"
- `"real photograph of the location {loc} (setting reference)"` → "setting, not a character, render in book style"

`STYLE_ANCHOR` contains `"of a character"` in the photo-matching sentence (added in PER-38)
to prevent the anchor from instructing the model to extract a face from a landmark photo on
scenery-only pages.

## Text overlay (`overlay_text.py`)

Pillow composites text on a feathered, semi-transparent rounded white panel that blends into
the art (no hard edge). A `bottom` panel anchors flush to the image bottom (full-bleed); a
`top` panel keeps a 4%-height margin. Font size auto-shrinks (72px → 22px floor) to fit the
comfortable 25% zone; the panel may grow past it but is hard-capped at 1/3 of page height. If
text won't fit 1/3 even at the 22px floor, the font shrinks below it (down to a 12px hard min)
so it fits rather than clipping. See `MAX_BOX_FRACTION` / `ABS_MIN_FONT_PX` and the two-phase
`_pick_font_size`. Per-page `text_align` (`left`/`center`, default `left`,
passed as `--align`) centers cover titles. Two
bundled OFL fonts back two **roles**: `reader` (Andika, body) and `display` (PatrickHand,
titles), selected per page via the `font` field in `story.json` (default `reader`);
`render_book.py`'s `run_overlay` passes the role through as `--font`. An optional top-level
`fonts` map (`{"reader": "Arial", "display": "Patrick Hand"}`) redefines each role's font;
`render_book.py` looks up the page's role in it and passes the family name as `--font-name`.
`overlay_text.py`'s `_resolve_font_ref()` resolves a name in order: bundled asset
(`_bundled_font_path`) → system font (PIL searches OS font dirs) → bundled role default +
one-time warning. So system fonts (Arial, etc.) need no manual install, and unknown names
never crash the render. Tunables are module constants near the top (`BOX_ALPHA`,
`FEATHER_PX`, padding, font px range) exposed as `--box-alpha` / `--feather` flags. The render
script never bakes story text into the generated image — every `image_prompt` reserves a
low-detail safe zone for this overlay.

## Local visual editor (`edit_story.py`)

`skills/storybook-story/scripts/edit_story.py` is a stdlib-only PEP-723 script that
launches a tiny local HTTP server (127.0.0.1 only) and opens `assets/editor.html` in
the browser. It provides a visual form for `story.json` — book settings, cast with
photo previews, palette swatches, and a page-by-page editor with hero-ordered cast
selection and render-status badges. No API cost, no dependencies beyond Python ≥ 3.10.

```bash
uv run skills/storybook-story/scripts/edit_story.py --story /path/to/story.json
# headless smoke test:
uv run skills/storybook-story/scripts/edit_story.py --story /path/to/story.json \
  --no-browser --port 8766
```

Round-trip contract: the editor preserves unknown keys at all levels and uses the same
formatter as `make_style_sheet.py` (`json.dump(indent=2, ensure_ascii=False)`). Optional
fields never get materialised when absent — the no-edit save is semantically stable.
Validates against `story_schema.json` before writing, with separate error (blocking) vs.
warning (non-blocking) tiers. Includes a 409 conflict guard: if `story.json` changes on
disk while the editor is open (e.g. Stage 2 writes `style_sheet` paths), the save
returns an error and a Reload button rather than silently clobbering the new content.

## Smoke-testing changes (do this on task completion)

When a task is complete, smoke-test the change against the **pip-storm fixture** in
`tests/` before declaring done. Two fixtures ship committed artifacts so the no-API paths
can be exercised for free: `tests/fixtures/pip-storm/` (overlay + native pages, reference
image, style sheet) and `tests/fixtures/pip-storm-long/` (the long-mode fixture: full-bleed
art pages, shared text-page background `pages/text-bg-long.png`, text pages, long books):

```bash
# No API cost — crop the committed fixture ref image (happy path + overwrite loop)
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.2,0.1,0.8,0.9 --out /tmp/smoke-crop.png
# Adjust box and re-run (must overwrite silently)
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.1,0.05,0.9,0.95 --out /tmp/smoke-crop.png
# Pixel coords → exit 2 with "fractions, not pixels" message
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 120,40,800,900 --out /tmp/smoke-bad.png
# Degenerate box (left ≥ right) → exit 2
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.8,0.1,0.2,0.9 --out /tmp/smoke-bad.png

# No API cost — exercise text overlay against a committed fixture page
uv run skills/storybook-render/scripts/overlay_text.py \
  --image tests/fixtures/pip-storm/pages/page-01.png \
  --text "Once upon a time..." --placement bottom --out /tmp/smoke.png

# No API cost — exercise text-page mode (long mode) against the committed shared bg
uv run skills/storybook-render/scripts/overlay_text.py \
  --image tests/fixtures/pip-storm-long/pages/text-bg-long.png \
  --text "Pip loved sunny days in the meadow." \
  --text-page --out /tmp/smoke-textpage.png

# No API cost — re-merge the committed fixture pages into a PDF (all three modes)
uv run skills/storybook-render/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-render/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-render/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long

# No API cost — re-merge into a fixed-layout EPUB3 (all three modes)
uv run skills/storybook-render/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-render/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-render/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long
```

Prefer these zero-cost checks; they cover overlay, text-page composition, PDF merge, EPUB
assembly, font resolution, and per-page selection logic without an image API call. Only fall
back to the paid
`tests/regen.sh` (~10 image calls, needs `GEMINI_API_KEY`) when a change actually
touches the Gemini API call paths and must be verified end-to-end. When editing a paid
script, re-run a single proof first (`render_book.py --only N` /
`make_style_sheet.py` on one character) before any full regen.

## Repo layout notes

- This is a **git worktree** (`w1`); siblings `w2`, etc. share one bare repo. Skills live at
  top-level `skills/` and are served to Claude via a `.claude/skills` symlink at the parent
  level (commit `b73df4a`).
- `.gitignore` excludes generated artifacts: `pages/`, `story.json`, `*.png`. The `*.png` files
  in the tree (e.g. `eva.png`, `style-sheet-*.png`) are local sample data, not tracked.
- `README.md` is the human-facing intro and is **maintained manually by the user**. Do
  **not** update it as part of routine changes — leave it alone unless the user explicitly
  asks. `CLAUDE.md` is the living agent reference; keep that current instead.
