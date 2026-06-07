# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A set of **four Claude Code skills** that together turn a story idea (optionally with
character photos) into a fully illustrated children's picture book. It is not an app — it
is skill definitions (`SKILL.md`) plus the Python scripts they invoke. There is no build
step and no test suite; the scripts are the product.

## The pipeline (read this first)

Four skills run in order and hand off a **single file, `story.json`**, in an output
directory (default: the user's cwd, e.g. this worktree root):

1. **storybook-story** (free, no API) — views any supplied photos (free, in-session), crops
   multi-person photos to one file per person via `scripts/crop_character.py` (Pillow only,
   no API), then writes `story.json`: per-page `text`, `image_prompt`, per-page `cast` list
   (mixed kinds), and a global `cast` array (characters, objects, and locations via `kind`). Validates `story.json` against `story_schema.json` via `scripts/validate_story.py` (free, stdlib-only, reuses the editor's validator — exit 2 on errors). **Has a hard approval gate** — it must stop
   and wait for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call per character) — generates one
   `style-sheet-{slug}.png` per eligible cast entry from the `cast` array (characters and objects always; kind=location entries with ref_image are skipped — the real-place photo is used directly at render time), writes each
   entry's `style_sheet` path back into `story.json`. **Approval gate**: show all
   sheets, get confirmation before rendering — a wrong sheet poisons every page that
   character appears on.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using only the style sheets for the cast entries listed in that page's `cast` field
   (per-page selection, cap 4 flash default / 5 pro; overridable per page or book via the `model` field or `--model` CLI flag), then overlays text. Three text modes:
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
   Output is page images only. **Approval gate**: show all rendered pages, stop for
   review before proceeding to assembly.
4. **storybook-consolidate** (free, no API) — after the user reviews and approves the
   rendered pages, chooses formats interactively (`saved_formats` is the default answer —
   omitted = both PDF and EPUB; `[]` = "no book files" preference; **no script reads this
   field**; interactive choice wins), assembles `{title}{suffix}.pdf` and/or
   `{title}{suffix}.epub` via `merge_pdf.py` / `merge_epub.py`, and optionally packages
   story.json + pages + style sheets + book files into `{slug}-book.zip` via
   `package_book.py`. All free.

`story.json` is the contract between stages; its schema is `skills/storybook-story/assets/story_schema.json`.

A top-level `style_guide` object is **required** in `story.json`: both paid scripts
assemble it into one byte-identical style block (`build_style_block()`, duplicated in
both scripts — keep the copies in sync) injected verbatim into every Gemini call. This is
the book-wide consistency mechanism (each page is a separate stateless call). Both scripts
**refuse to run** (`require_style_guide()`, exit 2) when it is missing or empty — breaking
change for pre-existing `story.json` files; add the field to render old books. The `style`
string remains as a short human label only.

Both paid scripts reject pre-PER-34 `story.json` files (legacy keys `characters`, `locations`, `pages[].characters`, `pages[].location`) with `exit 2` and a migration message — no shim, clean break.

## Critical external dependency

The two paid scripts call the **Gemini image API directly** (via the `google-genai`
Python SDK, declared as a PEP-723 inline dependency). They build a `genai.Client` with
`api_key` from the environment, model `gemini-3-pro-image` (style sheets, always, up to 5 character
reference images per call) or for page renders the configurable model — default `gemini-3.1-flash-image`
(4-ref cap) or `gemini-3-pro-image` (5-ref cap) set per-page, book-wide, or via `--model` CLI flag;
style sheets always stay on pro regardless. Scripts send the prompt plus reference images as `types.Part.from_bytes`, and
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

# Stage 3 — render all pages (2K default; all pages fired concurrently via asyncio)
uv run skills/storybook-render/scripts/render_book.py --story story.json

# Render / re-render a single page (proof before a full run)
uv run skills/storybook-render/scripts/render_book.py --story story.json --only 3

# Text overlay only, no image API cost (verify Pillow + fonts)
uv run skills/storybook-render/scripts/overlay_text.py \
  --image any.png --text "Once upon a time..." --placement bottom --out /tmp/t.png

# Stage 4 — assemble into PDF (no API cost); scripts now in storybook-consolidate
uv run skills/storybook-consolidate/scripts/merge_pdf.py --story story.json
# native mode PDF:
uv run skills/storybook-consolidate/scripts/merge_pdf.py --story story.json --text-mode native

# Merge already-rendered pages into a fixed-layout EPUB3 (no API cost)
uv run skills/storybook-consolidate/scripts/merge_epub.py --story story.json
# native mode EPUB:
uv run skills/storybook-consolidate/scripts/merge_epub.py --story story.json --text-mode native

# Package book assets into a zip (no API cost)
uv run skills/storybook-consolidate/scripts/package_book.py --story story.json
```

`render_book.py` flags: `--from N` (resume), `--only N`, `--resolution 1K|2K|4K`,
`--aspect-ratio RATIO` (override from story.json; unset → model chooses),
`--text-mode overlay|native|long`, `--model gemini-3.1-flash-image|gemini-3-pro-image`
(override per-page/book model for one run; precedence: CLI > page field > story field > flash default),
`--saved-formats pdf epub|none` (override story.json
`saved_formats`; default when neither set: all formats),
`--composite-only` (abort instead of making any paid Gemini call; only rebuild free Pillow
composites from existing raw/art/bg files — needs no `GEMINI_API_KEY`; pages that would
require a new image fail with a clear message naming the missing prerequisite, exit 1).
Pages are independent and all fired concurrently via `asyncio` (one async Gemini
request per page, no thread pool, no concurrency cap). Transient 429/5xx are retried
with exponential backoff + jitter, so wall-clock ≈ the slowest single page.

After a full render, `render_book.py` prints a pointer to Stage 4 (storybook-consolidate).
Assembly is Stage 4's job — `render_book.py` produces page images only.

## Idempotency / re-run semantics (important when editing scripts)

Both paid scripts **skip work whose output already exists**: `make_style_sheet.py` skips any
`style-sheet-{name}.png` that already exists (per-character, so re-running only generates
the missing ones); `render_book.py` skips any `page-NN.png` that exists. To force a
regenerate you must `rm` the target file (and the matching `raw-page-NN.png` for a page)
first. Preserve this behaviour — it makes partial-failure re-runs cheap.

**Overlay mode free re-composite (PER-47):** `render_book.py` now guards the overlay
Gemini call with `if not raw_path.exists()`. This means:
- `rm page-NN.png` alone + re-run = free re-composite from `raw-page-NN.png` (no API call,
  no `GEMINI_API_KEY` needed).
- `rm page-NN.png` + `rm raw-page-NN.png` + re-run = paid re-render (new image).
  The same two-tier semantics apply to the long-mode cover (`page-01-long.png` /
  `raw-page-01-long.png`); long-mode body text pages have always been free to rebuild
  when `page-NN-long.png` and the shared `text-bg-long.png` already exist.
  Use `--composite-only` to guarantee no paid call is ever made in a run.

## Key design decision: explicit cast, never prose-scraped

**`image_prompt` references cast by name only (PER-42).** Every entry in `pages[].cast`
is reference-backed at render time (character/object → style sheet; location → photo or
sheet). Repeating a cast member's `appearance` prose in the `image_prompt` makes the
render model deviate from the reference; name-only is the stronger, more consistent
signal. Pose, action, expression, and scene description stay in the prompt — only inherent
appearance (species, colours, outfit, physical traits) is omitted. Non-cast background
figures are described in prose as usual (no reference to anchor on).
`validate_story.py` warns on detected echoes (character/object kinds; locations excluded).

The `cast` array in `story.json` is authored explicitly and is the **only** source for
the style sheets. An earlier regex that scraped characters from prose minted phantom
characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit")
and poisoned every page. Do not reintroduce auto-extraction. See the docstring on
`get_cast()` in `make_style_sheet.py`. Reference images come only from that
entry's own `ref_image` (a single path or an array of paths), capped at 5 (Gemini 3
Pro Image character-lane limit). There is no shared global pool — the cast-to-photo mapping
is fixed in Stage 1, so one character's photo never bleeds into another's sheet.

**Single-person images only.** Every path in `ref_image` must show only one person — a solo
photo or a per-person crop. If a source photo contains multiple people, Stage 1 crops it
to one file per character via `skills/storybook-story/scripts/crop_character.py` (Pillow
only, free, no API). The original multi-person photo is never listed in any `ref_image`. This
preserves the existing cap math (5 Stage-2 / 4-or-5 render cap by model) unchanged — refs stay
per-character and per-person, so nothing interacts differently with the caps.

Re-run recipe for a bad crop: re-run `crop_character.py` with an adjusted `--box` (it
overwrites silently — free to iterate) → `rm style-sheet-{slug}.png` → re-run Stage 2.
Cross-session note: crop provenance is not stored in `story.json` (intentional — same rule
as `style_sheet`). To redo a crop in a new session you need the original source photo again.

Each page also carries an explicit `cast` list (`pages[].cast`) naming which
cast members appear on it. `render_book.py`'s `collect_input_images(story, page)` uses
this to send only the relevant per-cast-entry style sheets — the model never sees sheets
for cast entries not on the page. The **hero** is the first cast entry of `kind: "character"` (or kind absent, defaulting to character) in `pages[].cast`: it additionally contributes its first `ref_image` (a solo photo or a Stage-1
crop), so the render anchors the hero's facial likeness on the real photo, not only on the
(lossy) style sheet. **Convention: author the hero/child first among the character-kind entries in each page's `cast` list.**
Priority into the per-model cap (4 flash default / 5 pro) is: hero sheet → hero photo → remaining character sheets (page order) → object refs (page order) → location refs (page order, lowest, first to drop from cap); anything past the cap is logged, never silently dropped.

**Outfit lock (single canonical outfit per character).** For kind=character entries, `appearance` must
name exactly one outfit; the style-sheet prompt takes clothing from there, never from
`ref_image` photos (which may show the character in multiple outfits). Stage 3 takes
clothing from the sheet, not the hero photo. This locks one outfit per character across
the whole book. To change a character's outfit, edit `appearance`, delete the existing
style-sheet PNG, and re-run `make_style_sheet.py`.

## Location photo references (PER-38)

Real named places can contribute a photo reference during page rendering. They are now part of the unified **`cast`** array as entries with `kind: "location"` rather than a separate `locations[]` array.

**Schema:** add a cast entry with `kind: "location"`, `name`, `appearance` (place description), and `ref_image` (path to downloaded photo — produces a `kind: "location"` sheet-less entry). Optional `source_url` stores provenance. Pages opt in by listing the place name in `pages[].cast`.

**Per-page selection is mandatory** to prevent environment bleed (PER-33 lesson: a location photo used book-wide bleeds the place's environment into every page, including pages set elsewhere). Only list the place name on pages physically set there.

**Stage 1 (in-session, free):** the agent detects real named places in the story, calls `perplexity_search` to find a Wikimedia Commons freely-licensed photo, builds a deterministic download URL, and runs `fetch_location.py` to download and validate it. The resulting path goes into the cast entry's `ref_image` field. If the Perplexity MCP is absent, locations are skipped with a user-facing message — Stage 1 never fails over this.

**`fetch_location.py`** (`skills/storybook-story/scripts/fetch_location.py`): PEP-723, Pillow + stdlib `urllib`. Validates HTTP status, `content-type: image/*`, decodes with Pillow, checks min edge (≥512px default), downscales to max edge (≤1536px default), mode-normalises to RGB, saves as JPEG. Prints `MEDIA: {out}` for inline preview.

**Stage 2 policy:** `make_style_sheet.py` **skips** sheet generation for `kind=location` entries that carry a `ref_image` (the real-place photo is the render reference; no style sheet generated). Generates a location reference sheet only when no `ref_image` is set (fictional recurring place, from `appearance`).

**Cap priority in `collect_input_images` (render_book.py):**

> hero sheet → hero photo → remaining character sheets (page order) → object refs (page order) → **location refs (lowest, first to drop)**

The location reference is appended last and is the first to be dropped when the per-model cap is reached (4 flash default / 5 pro). Drops are logged, never silent. On scenery-only pages (`cast: []` or only non-character entries) with a location set, the location photo is the sole reference image.

**Labeled-interleaved contents (`run_nano_banana`):** each reference image is preceded by a short text part: `"Next image: {label}."` The `IMAGE_SYSTEM_PROMPT` defines the behaviour rule for each of 6 label kinds. Keep label wording in sync with the system prompt's "kind" vocabulary:

- `"character style sheet for {name}"` → defines design, outfit, art style
- `"real photograph of the character {name} (facial likeness reference)"` → face only, outfit from sheet
- `"object reference sheet for {name}"` → defines object design, colours, proportions
- `"real photograph of the object {name} (appearance reference)"` → shape/materials/details reference
- `"location reference sheet for {name}"` → defines place's look in book style
- `"real photograph of the location {name} (setting reference)"` → setting, rendered in book style

`STYLE_ANCHOR` contains `"of a character"` in the photo-matching sentence to prevent the anchor from instructing the model to extract a face from a landmark photo on scenery-only pages.

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
selection, render-status badges, **per-page image preview, generation history browser,
a regenerate button, and a per-page model picker** (retry knob: set a page to `gemini-3-pro-image`
and hit Regenerate to retry that page on the stronger model without touching the rest). No API cost for browsing/selecting; regenerate triggers one
paid Gemini call per page.

```bash
uv run skills/storybook-story/scripts/edit_story.py --story /path/to/story.json
# headless smoke test:
uv run skills/storybook-story/scripts/edit_story.py --story /path/to/story.json \
  --no-browser --port 8766
# regenerate requires GEMINI_API_KEY in the editor's env:
GEMINI_API_KEY=your_key uv run skills/storybook-story/scripts/edit_story.py \
  --story /path/to/story.json
```

Round-trip contract: the editor preserves unknown keys at all levels and uses the same
formatter as `make_style_sheet.py` (`json.dump(indent=2, ensure_ascii=False)`). Optional
fields never get materialised when absent — the no-edit save is semantically stable.
Validates against `story_schema.json` before writing, with separate error (blocking) vs.
warning (non-blocking) tiers. Includes a 409 conflict guard: if `story.json` changes on
disk while the editor is open (e.g. Stage 2 writes `style_sheet` paths), the save
returns an error and a Reload button rather than silently clobbering the new content.

### Image manipulation endpoints (PER-41, PER-47)

Four server endpoints power per-page image controls:

- **`GET /api/versions?page=N`** — pure read; lists generated versions for page N as
  `{versions: [{id, path, mtime, in_use}], regen: {status, error}, fast: {eligible, reason, mode}}`.
  Newest first. `path` values are story-dir-relative and fed straight to `/img?path=…`.
  The `fast` field (PER-47) signals whether a free Pillow re-composite is currently possible
  (eligibility = mode is overlay/long AND the prerequisite raw/art/bg exists on disk).
- **`POST /api/page/regenerate`** body `{page_num}` — archives the current image into
  history, deletes the canonical file(s), then spawns `uv run render_book.py --only N`
  in a background thread. Returns 200 immediately; poll `/api/versions` to watch
  progress. Requires `GEMINI_API_KEY` in the editor's env (checked on start). On
  render failure, the previous canonical is restored from history so the book is never
  left with a hole.
- **`POST /api/page/recomposite`** body `{page_num}` (PER-47) — free Pillow re-composite;
  no API key, no paid call. Deletes only the active-mode composite output (never raws/art/bg)
  and spawns `render_book.py --only N --composite-only --text-mode <mode>`. Returns 400 for
  native mode (no Pillow split), 422 with `fallback:true` when the prerequisite raw/art/bg is
  missing (client offers paid re-render fallback), 409 when a render is already running.
  Same adopt/restore history mechanics as regenerate.
- **`POST /api/page/select`** body `{page_num, version}` — copies a history entry's
  artifact set into the canonical slot (the "used in book" image). Free, synchronous.
  All mutating endpoints gate on a per-page lock (409 if one is running).

### Smart regenerate buttons (PER-47)

The editor tracks which story.json fields changed per page since the last completed render
or recomposite. When ALL changed fields are Pillow-only (`text`, `text_placement`,
`text_color_hint`, `text_align`, `font`) AND the server confirms `fast.eligible`, the UI
shows two buttons: **"✎ Re-composite (free)"** (calls the recomposite endpoint) as primary
and **"↻ Re-render (paid)"** as a small secondary override. Otherwise the single paid
"↻ Regenerate" / "↻ Render" button is shown as before.

**Accepted limitations:**
- Tracking is per-browser-session only (stale `image_prompt` from a previous session won't
  force paid — semantics: "apply text fields to the image you currently see").
- Any render-affecting book-level edit (style_guide, model, resolution, text_mode, …) sets a
  sticky `bookPaidEdit` flag for the session, forcing paid on all pages; reload resets it.
- Long-body text-page archival gap (pre-existing): history identity = preview hash = the art
  page; a text-page recomposite whose art hash is already in history archives nothing before
  overwriting. The prior text page is not reliably recoverable — never say "reversible" for
  long body in UI copy.

### History layout

```
{story_dir}/pages/
  page-03.png                 ← canonical (consolidation input, "used in book")
  raw-page-03.png
  history/
    page-03/
      20260607-143012/        ← one stamped dir per generation
        page-03.png           ← whatever artifact set existed is archived here
        raw-page-03.png
      20260607-150244/
        …
```

"Used in book" identity = SHA-256 of the preview file matched against history entries —
no manifest, no `story.json` field; survives CLI renders and pre-feature books. The
canonical file IS the consolidation input; `select` = copy into the canonical slot.
Merge scripts and render skip-logic are unchanged.

**Adopt-on-mutate invariant:** before any mutating op (regenerate, select) touches the
canonical slot, the current canonical is copied into history if its hash is not already
present there. GETs (`/api/versions`) are pure — no disk mutation on read.

**Per-page bg preserved across regens:** `page-NN-long-bg.png` is copied to history for
completeness but never deleted by regenerate, so `render_book.py`'s
`if not bg_path.exists()` guard reuses it — no surprise extra paid bg call.

**Known limitations:** history is keyed by `page_num`; reordering/deleting pages in the
editor does not remap `pages/history/page-NN/` (same drift already exists for the
canonical files). Editor always assumes `pages/` is beside `story.json` (unchanged
pre-existing assumption).

## Interactive interview (x-interview annotations, PER-27)

The storybook-story skill guides users through a short schema-driven interview before writing `story.json`. The mechanism is annotation-based — the skill reads `story_schema.json` at runtime and never needs editing when a knob is added.

### Contract

Add `"x-interview": {"priority": "core"|"advanced", "ask": "<one-line hint>"}` inside top-level property definitions in `skills/storybook-story/assets/story_schema.json`:

- `"priority": "core"` — asked up front in the interview batch; enum values become options, default first (schema `"default"` if present, else first enum value).
- `"priority": "advanced"` — never asked; shown once in the pre-write configuration summary (Gate 1) as an overridable default.
- No annotation — never asked, never in the summary (by design).

### The rule

**A new book-level knob = schema property + `x-interview` annotation (or a deliberate decision to omit one) — never a hard-coded question in `SKILL.md`.** `SKILL.md` holds only the generic algorithm, which is stable across schema changes.

### Required-field warning

Required fields authored by prose logic (`title`, `style_guide`, `cast`, `pages`) are intentionally un-annotated — the agent writes them from the story content, not a question. Any **new required scalar knob** MUST get a `"core"` annotation or explicit authoring logic; otherwise the interview never produces it and `story.json` fails validation. `SKILL.md`'s required-field guard cross-checks `schema.required` at runtime and surfaces gaps — it does not silently skip them.

### Script safety

`edit_story.py` and `editor.html` read only `enum` / `default` / `required` / property key names from the schema — custom `x-*` keys inside property definitions are invisible to both (verified: `_schema_enums` lines 76-91, `_known_keys` lines 94-102, `editor.html` `enumFor`/`defaultFor`). Never make scripts depend on `x-interview`.

### Omission rule

Accepting a default at Gate 1 means the optional key is **omitted** from `story.json` (preserves the editor's round-trip contract where "optional fields never get materialised when absent"). `saved_formats: []` (records "no book files" preference — hint for Stage 4) is NOT the same as omitted (hint = all formats). No script reads `saved_formats`; storybook-consolidate uses it as the default answer when asking which formats to export, and the interactive choice there always wins. `aspect_ratio` omitted = model picks framing per page call.

### `ask` field guidance

Keep `ask` to one line. The property `"description"` remains the authoritative detail and is the source of option glosses shown during the interview.

---

## Smoke-testing changes (do this on task completion)

When a task is complete, smoke-test the change against the **pip-storm fixture** in
`tests/` before declaring done. Two fixtures ship committed artifacts so the no-API paths
can be exercised for free: `tests/fixtures/pip-storm/` (overlay + native pages, reference
image, style sheet) and `tests/fixtures/pip-storm-long/` (the long-mode fixture: full-bleed
art pages, shared text-page background `pages/text-bg-long.png`, text pages, long books):

```bash
# No API cost — validate all committed fixtures (must all exit 0)
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/pip-storm-long/story.json
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/gazelle-valley/story.json

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
# Scripts now live in storybook-consolidate (moved from storybook-render in PER-44)
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long

# No API cost — re-merge into a fixed-layout EPUB3 (all three modes)
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long

# No API cost — package smoke (--out /tmp to avoid polluting fixture dirs)
uv run skills/storybook-consolidate/scripts/package_book.py \
  --story tests/fixtures/pip-storm/story.json --out /tmp/smoke-package.zip
# Note: rebuilt EPUBs always differ (timestamp+uuid in content.opf) — run
# `git restore tests/fixtures` after smoking to discard churned fixture binaries.
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
