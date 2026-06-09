# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A set of **four Claude Code skills** that together turn a story idea (optionally with
character photos) into a fully illustrated children's picture book. It is not an app — it
is skill definitions (`SKILL.md`) plus the Python scripts they invoke. There is no build
step and no test suite; the scripts are the product.

## The pipeline (read this first)

Four skills run in order and hand off a **single file, `story.json`**, in an output
directory (default: a newly created `{slug(title)}/` folder under the user's cwd;
an explicitly user-named path is used verbatim):

1. **storybook-story** (free, no API) — views any supplied photos (free, in-session), optionally analyzes a **style reference image** in-session to seed `style_guide` (PER-9: free, no API, same Claude-vision seam as cast photos), then writes `story.json`: per-page `text`, `image_prompt`, per-page `cast` list
   (mixed kinds), and a global `cast` array (characters, objects, and locations via `kind`). Validates `story.json` against `story_schema.json` via `scripts/validate_story.py` (free, stdlib-only, reuses the editor's validator — exit 2 on errors). **Has a hard approval gate** — it must stop
   and wait for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call per cast entry) — generates one
   `style-sheet-{slug}.png` per cast entry from the `cast` array (characters, objects, and
   locations all get sheets; location sheets are built from the entry's downloaded real-place
   photos when present, else from `appearance` — PER-50), writes each entry's `style_sheet`
   path back into `story.json`. **Approval gate**: show all sheets, get confirmation before
   rendering — a wrong sheet poisons every page that character appears on.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using only the style sheets for the cast entries listed in that page's `cast` field
   (per-page selection, cap 4 flash default / 5 pro; auto-upgrades flash→pro when refs ≥5 — PER-58; overridable per page or book via the `model` field or `--model` CLI flag), then overlays text. Three text modes:
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

Both paid scripts write an append-only audit log **`out_dir/log.txt`** for every outgoing
image request (Stage 2 → OpenAI, Stage 3 → Gemini; same log format) — full config, system instruction, full prompt, and per-reference-image
metadata (source path, mime, byte count; never raw bytes) in `contents` order. The helper
`append_api_log()` is duplicated in both scripts (keep in sync with `build_style_block()`
and `_ensure_png`). Runs with `--composite-only` and editor recomposites never write
this log (the `--composite-only` guard returns before the hook). Editor regenerates spawn
`render_book.py` as a subprocess, so they are automatically covered. `log.txt` is
gitignored (including inside `tests/fixtures/`).

A top-level `style_guide` object is **required** in `story.json`: both paid scripts
assemble it into one byte-identical style block (`build_style_block()`, duplicated in
both scripts — keep the copies in sync) injected verbatim into every image call (both vendors). This is
the book-wide consistency mechanism (each page is a separate stateless call). Both scripts
**refuse to run** (`require_style_guide()`, exit 2) when it is missing or empty — breaking
change for pre-existing `story.json` files; add the field to render old books. The `style`
string remains as a short human label only.

Both paid scripts reject pre-PER-34 `story.json` files (legacy keys `characters`, `locations`, `pages[].characters`, `pages[].location`) with `exit 2` and a migration message — no shim, clean break.

## Critical external dependency

The two paid scripts call **different image vendors** — this is a cross-vendor pipeline,
not one model end-to-end. Both declare their SDK as a PEP-723 inline dependency.

- **`make_style_sheet.py` (Stage 2) → OpenAI `gpt-image-2`** via the `openai` SDK. Uses
  `client.images.edit` when an entry has reference photos (the documented multi-image
  likeness path), falling back to `client.images.generate` when it has none (edit requires
  ≥1 input image). `gpt-image-2` has no separate system-role channel on `images.edit`, so
  the per-kind system prompt, the reference label, and the sheet prompt are folded into one
  prompt string (wrapped in `STYLE_BOOST_HEAD`/`STYLE_BOOST_TAIL`). `quality="medium"`,
  `moderation="low"` (via `extra_body` so it reaches the API regardless of SDK version).
  Call size comes from `aspect_to_size(aspect_ratio)` — the `--resolution` 1K/2K/4K flag is
  Gemini-era and **ignored** here (logged only). Requires `STORYBOOK_SKILL_OPENAI_API_KEY`
  (preferred) or `OPENAI_API_KEY`. Returned image is `response.data[0].b64_json`.
- **`render_book.py` (Stage 3) → Google Gemini** via the `google-genai` SDK. Builds a
  `genai.Client` (lazily, on first paid call) with `api_key` from the environment; configurable
  model — default `gemini-3.1-flash-image` (4-ref cap) or `gemini-3-pro-image` (5-ref cap),
  set per-page, book-wide, or via `--model` CLI flag (auto-upgrade flash→pro when refs ≥5).
  Sends the prompt plus reference images as `types.Part.from_bytes`, extracts the returned
  image from `part.inline_data.data`. Requires `GEMINI_API_KEY`.

**Consistency implication:** the Stage-2 sheet PNG (OpenAI) is fed as a *reference image*
into the Stage-3 render (Gemini) — a different model interprets it. The verbatim `style`
string assembled by `build_style_block()` is injected into BOTH vendors' prompts and is the
only byte-identical cross-vendor anchor; the sheet PNG is a lossy proxy. Sheets force a flat
neutral background (subject isolation — scenery on a sheet would bleed into every page); the
book style shows up in the character's linework/palette, not a background.

Requires `uv` on PATH. No sibling skill is needed (an earlier version shelled out to
`nano-banana-pro-openrouter`; that logic is now inlined in each script — `run_nano_banana()`
in `render_book.py` and `generate_image()` in `make_style_sheet.py`).

## Running the scripts

All scripts are PEP-723 inline-dependency scripts — always run with `uv run` (it resolves
deps like Pillow automatically), never `python`:

```bash
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
`--text-mode overlay|native|long` (override for entire run; precedence: CLI > per-page `text_mode` field > book `text_mode` field > native default; per-page `text_mode` lets individual pages differ from the book default without this flag),
`--model gemini-3.1-flash-image|gemini-3-pro-image`
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

## Key design decision: explicit cast, never prose-scraped; cast referenced by id (PER-56)

**Cast entries have a stable `id` (PER-56).** Pattern `^[a-z][a-z0-9-]*$` (e.g. `pip`, `major-oak`). The id is the internal key — it appears in `pages[].cast` and as `<id>` placeholders in `image_prompt`. It also drives the style-sheet filename: `style-sheet-{id}.png` (replacing the old `char_slug(name)`-derived filename). **The id NEVER reaches the image model.** `render_book.py`'s `resolve_cast_placeholders()` substitutes `<id>` → cast entry's display `name` before every Gemini call; the model always sees real names. This hybrid design (id internal, name to model) was chosen after research: Google Gemini image models run on "deep language understanding" — real names carry species/gender/age cues that opaque ids lack, and in native text mode a raw `<id>` token would be lettered into the art.

**`image_prompt` uses `<id>` placeholders (PER-56 + PER-42).** Example: `"<pip> runs through rain"` → Gemini sees `"Pip runs through rain"`. Never repeat a cast member's `appearance` prose in the prompt (PER-42) — the style sheet defines appearance; name-only (via `<id>` → name) is the stronger consistency signal. Pose, action, expression, scene description stay in the prompt. `validate_story.py` errors on unknown `<id>` tokens, warns on echo. Non-cast background figures are described in prose as usual.

**Breaking change (PER-56, no shim):** both paid scripts call `require_cast_ids()` (exit 2 with migration message) when any cast entry is missing a valid `id`. Migrate story.json: add `id` to each cast entry, switch `pages[].cast` to ids, convert `image_prompt` name mentions to `<id>` placeholders.

The `cast` array in `story.json` is authored explicitly and is the **only** source for
the style sheets. An earlier regex that scraped characters from prose minted phantom
characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit")
and poisoned every page. Do not reintroduce auto-extraction. See the docstring on
`get_cast()` in `make_style_sheet.py`. Reference images come only from that
entry's own `ref_image` (a single path or an array of paths), capped at 5 (Gemini 3
Pro Image character-lane limit). There is no shared global pool — the cast-to-photo mapping
is fixed in Stage 1, so one character's photo never bleeds into another's sheet.

Each page also carries an explicit `cast` list (`pages[].cast`) of cast **ids** — not names — naming which
cast members appear on it. `render_book.py`'s `collect_input_images(story, page)` uses
this to send only the relevant per-cast-entry style sheets — the model never sees sheets
for cast entries not on the page. The **hero** is the first cast entry of `kind: "character"` (or kind absent, defaulting to character) in `pages[].cast`: it additionally contributes its first `ref_image` (a solo photo or a Stage-1
reference photo), so the render anchors the hero's facial likeness on the real photo, not only on the
(lossy) style sheet. **Convention: author the hero/child first among the character-kind entries in each page's `cast` list.**
Priority into the per-model cap (4 flash default / 5 pro) is: hero sheet → hero photo → remaining character sheets (page order) → object refs (page order) → location refs (page order, lowest, first to drop from cap); anything past the cap is logged, never silently dropped. **Auto-upgrade (PER-58):** `select_refs()` in `render_book.py` runs before the cap is applied — if the effective model is flash and the candidate list has ≥5 images, the page is silently upgraded to `gemini-3-pro-image` for that call only (logged, story.json untouched). The cap/drop logic is in `select_refs`; `collect_input_images` now returns the full uncapped candidate list.

**Outfit lock (single canonical outfit per character).** For kind=character entries, `appearance` must
name exactly one outfit; the style-sheet prompt takes clothing from there, never from
`ref_image` photos (which may show the character in multiple outfits). Stage 3 takes
clothing from the sheet, not the hero photo. This locks one outfit per character across
the whole book. To change a character's outfit, edit `appearance`, delete the existing
style-sheet PNG, and re-run `make_style_sheet.py`.

## Location photo references (PER-38, PER-50)

Real named places contribute downloaded photo references that Stage 2 turns into a location
style sheet. They are part of the unified **`cast`** array as entries with `kind:
"location"` rather than a separate `locations[]` array.

**Schema:** add a cast entry with `kind: "location"`, `name`, `appearance` (place
description), and `ref_image` (ARRAY of downloaded photo paths — target 3 distinct
angles/views, minimum 1; a plain string is also accepted). Optional `source_url` stores
provenance — an array parallel to `ref_image` (one Commons file-page URL per photo, same
order) or a single string. Pages opt in by listing the place name in `pages[].cast`.

**Per-page selection is mandatory** to prevent environment bleed (PER-33 lesson: a location
reference used book-wide bleeds the place's environment into every page, including pages set
elsewhere). Only list the place name on pages physically set there.

**Stage 1 (in-session, free):** the agent detects real named places, calls
`perplexity_search` to find ~3 distinct Wikimedia Commons freely-licensed photos (different
angles/views preferred), builds a deterministic download URL per photo, and runs
`fetch_location.py` once per photo (numbered outputs `loc-{slug}-1.jpg`, `-2.jpg`,
`-3.jpg`), validating each inline (right place, well-framed, no prominent people). Accept
1-2 when Commons lacks suitable photos (min 1) and tell the user. The resulting paths go
into the cast entry's `ref_image` array. If the Perplexity MCP is absent, photo gathering
is skipped with a user-facing message (the place still gets an appearance-only sheet) —
Stage 1 never fails over this.

**`fetch_location.py`** (`skills/storybook-story/scripts/fetch_location.py`): PEP-723,
Pillow + stdlib `urllib`. Validates HTTP status, `content-type: image/*`, decodes with
Pillow, checks min edge (≥512px default), downscales to max edge (≤1536px default),
mode-normalises to RGB, saves as JPEG. Prints `MEDIA: {out}` for inline preview; invoked
once per photo (PER-50).

**Stage 2 policy (changed in PER-50):** `make_style_sheet.py` generates a sheet for
**every** location entry — from the real-place photos in `ref_image` when present
(architecture/landmarks/geography anchored, rendered in the book style), from `appearance`
alone otherwise (fictional recurring place). The sheet is the render reference; the raw
photo is only the render-time fallback. Note for pre-PER-50 books: re-running Stage 2 on a
story whose location carried only a photo makes one extra paid call and writes `style_sheet`;
render remains backward-compatible via the photo fallback for books never re-sheeted.

**Ref priority and cap (`render_book.py`):**

> hero sheet → hero photo → remaining character sheets (page order) → object refs (page order) → **location refs (sheet, or photo fallback — lowest, first to drop)**

`collect_input_images()` builds the full prioritized candidate list (no cap). `select_refs(candidates, model)` then: auto-upgrades flash → pro when `len(candidates) > 4`, applies the cap, and returns `(effective_model, selected, dropped)`. Drops are logged, never silent. Flash pages with ≥5 refs are upgraded to pro before any ref is dropped; only past the pro cap (5) are refs dropped. On scenery-only pages (`cast: []` or only non-character entries) with a location set, the location photo is the sole reference image.

**Labeled-interleaved contents (`run_nano_banana`):** each reference image is preceded by a short text part: `"Next image: {label}."` The `IMAGE_SYSTEM_PROMPT` defines the behaviour rule for each of 6 label kinds. Keep label wording in sync with the system prompt's "kind" vocabulary:

- `"character style sheet for {name}"` → defines design, outfit, art style
- `"real photograph of the character {name} (facial likeness reference)"` → face only, outfit from sheet
- `"object reference sheet for {name}"` → defines object design, colours, proportions
- `"real photograph of the object {name} (appearance reference)"` → shape/materials/details reference
- `"location reference sheet for {name}"` → defines place's look in book style
- `"real photograph of the location {name} (setting reference)"` → setting, rendered in book style

`STYLE_ANCHOR` contains `"of a character"` in the photo-matching sentence to prevent the anchor from instructing the model to extract a face from a landmark photo on scenery-only pages.

## Text overlay (`overlay_text.py`)

Pillow composites text on a feathered, semi-transparent panel that blends into the art (no hard
edge). Panel base colour follows `text_color_hint` (`dark` → white panel `(255,255,255)`,
`light` → near-black panel `(30,30,30)`); text colour is near-black on dark, near-white on light.
`dark` behaviour is byte-identical to pre-PER-52 output. A `bottom` panel anchors flush to the image bottom (full-bleed); a
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
script never bakes story text into the generated image (overlay mode) — every `image_prompt` reserves a
low-detail safe zone for this overlay. In native mode, `NATIVE_TEXT_DIRECTIVE` in `render_book.py`
instructs the model to letter text into the art; `text_color_hint` is spliced into it as `{ink_clause}`,
selecting warm dark ink (with a lightly-toned backdrop) for `dark` or cream-white ink (with an
explicitly forced dark-toned backdrop area) for `light`.

## Local visual editor (`edit_story.py`)

`skills/storybook-story/scripts/edit_story.py` is a stdlib-only PEP-723 script that
launches a tiny local HTTP server (127.0.0.1 only) and opens `assets/editor.html` in
the browser. It provides a visual form for `story.json` — book settings, cast with
photo previews, palette swatches, **per-cast-entry style-sheet generate/regenerate button
with version history and "Use in book" selector** (PER-59), and a page-by-page editor with
**an `@`-mention cast picker in the image-prompt field** (PER-62: type `@` to insert an
`<id>` placeholder, filterable by id/name, all kinds with badges, keyboard nav), a
**read-only "Cast on this page" view derived from the prompt's `<id>` mentions** (PER-62:
the image_prompt is the single source of truth for a page's cast — `_castIdsFromPrompt()`
collects valid `<id>` tokens in first-appearance order, `_syncPageCast()` reconciles
`page.cast` on every prompt edit; hero = first character-kind mention; no manual
add/remove/reorder UI), render-status badges, **per-page image preview, generation
history browser, a regenerate button, a per-page model picker** (retry knob: set a page to `gemini-3-pro-image`
and hit Regenerate to retry that page on the stronger model without touching the rest), **a
per-page ref-count warning badge** (PER-58: amber "5 refs → pro required" when the intent-based
ref count is 5 and the effective model is flash — render auto-upgrades at runtime; red "N refs >
pro cap 5 — refs will drop" when count ≥ 6 regardless of model), and
**a per-page text mode picker** (unset = same as book; override lets individual pages render in a
different mode than the book default). Fields that have no effect given the current effective text mode
are greyed-out (user may still pre-set them); the `floating` placement option is hard-hidden
when not in native mode. No API cost for browsing/selecting; regenerate triggers one
paid Gemini call per page. A **"📦 Consolidate Story" button** sits below the Pages section
and opens a modal (PER-49) for managing `saved_formats` and running Stage 4 directly from
the editor — see "Consolidate Story modal" below.

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

### Style-sheet endpoints (PER-59)

Three server endpoints mirror the page-image flow for cast-entry style sheets:

- **`GET /api/sheet/versions?name=X`** — pure read; lists generated versions as
  `{versions: [{id, path, mtime, in_use}], regen: {status, error}}`. Entry not found → 404.
- **`POST /api/sheet/regenerate`** body `{name}` — archives the current sheet into history
  (`pages/history/{stem}/{stamp}/{stem}.png`), deletes it, then spawns
  `uv run make_style_sheet.py --only NAME` in a background thread. Returns 200 immediately;
  poll `/api/sheet/versions?name=X` to watch progress. Requires `STORYBOOK_SKILL_OPENAI_API_KEY`
  (or `OPENAI_API_KEY`) — Stage 2 is OpenAI `gpt-image-2`, NOT Gemini (page regen uses Gemini).
  **Full-quiescence gate** (409): ANY running job blocks this call (consolidation, regen-all,
  any page render, any other sheet regen). Reason: make_style_sheet.py rewrites story.json
  on completion; concurrent jobs would corrupt each other's story.json write.
  On failure, the previous sheet is restored from history so the book is never left sheet-less.
  Works for first-generation too (no prior sheet → nothing to archive; Generate case).
- **`POST /api/sheet/select`** body `{name, version}` — copies a history entry back into the
  canonical slot (free, no API). Same full-quiescence gate (sheets are render inputs).

**Sheet history layout** (within `pages/history/` next to story.json):
```
pages/history/
  page-NN/            ← page history (existing)
  style-sheet-{slug}/ ← sheet history (stem = Path(style_sheet).stem)
    YYYYMMDD-HHMMSS/
      style-sheet-{slug}.png
```
Sharing one `pages/history/` root means `rm -rf pages/` also wipes sheet history — documented
accepted trade-off. Renaming a cast entry changes the slug → old-stem history is orphaned
(same accepted drift class as page reorder).

**GET /api/status** now includes ALL named cast entries (not only those with a `style_sheet`):
`cast[name] = {style_sheet_exists: bool, regen: {status, error}}`. The `regen` field lets
a reloaded client resume an in-flight sheet poll.

**`make_style_sheet.py --only NAME`** (PER-59): process only the named cast entry; the slug
walk still runs for ALL entries so filenames stay stable. Exit 2 if name not found.
This is NOT equivalent to delete-PNG + full run: the full run rewrites `style_sheet` for
every entry (absolute paths), which would desync the client's single-field mtime patch.

**Known limitations:** pages using a regenerated sheet are stale but show no stale badge
(badge tracks session edits, not sheet changes). The confirm-dialog warns the user.

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

All three page-mutating endpoints also 409 while a consolidation job is running (merging
reads pages/ concurrently with a regen that deletes canonicals would corrupt output).

### Consolidate Story modal (PER-49)

The export configuration (`saved_formats`) that was in the Book settings fieldset has been
moved into a **"📦 Consolidate Story" modal** that also lets the user run Stage 4 directly.

Two new server endpoints (both free, no API key, global consolidate lock):

- **`POST /api/consolidate`** body `{formats: ["pdf","epub","zip"]}` — spawns
  `merge_pdf.py`, `merge_epub.py`, and/or `package_book.py` sequentially in a background
  thread (fixed order: pdf → epub → zip, so `package_book.py` globs books that just merged).
  No `--text-mode` flag: the scripts resolve text mode from story.json themselves. Returns
  200 immediately; poll `/api/consolidate/status` to watch progress.
  409 if consolidation is already running OR any page render is running.
- **`GET /api/consolidate/status`** — returns `{ok, status:"idle"|"running"|"done"|"error",
  results:[{format, ok, path, error}]}`. Each result is appended under lock as it completes
  (live per-format progress). Cheap poll target (mirrors `/api/versions` for pages).

**Lock discipline:** `_consolidate_job` (module-level dict) is always mutated in place
under `_regen_lock` (same lock as `_regen_jobs`). The worker thread never holds the lock
across `subprocess.run` — status polls (also locked) would deadlock otherwise.

**Modal layout:** single format set — the existing `saved_formats` tri-state (unset/`[]`/explicit)
is rendered inside the modal and persists to story.json on change; a separate "also build zip"
checkbox is run-time-only (never written to story.json; `saved_formats` schema stays `["pdf","epub"]`).
Run flushes unsaved edits first (same dirty-check as the page regenerate button) so scripts
read the latest story.json.

### Re-generate All Pages button (PER-53)

A **"↻ Re-generate All Pages"** button sits next to the Consolidate Story button below the
Pages section. It re-renders the entire book in one click — useful after changing book-level
settings (style_guide, model, text_mode, resolution, aspect_ratio) that make every page stale.

Cost: N paid Gemini calls (one per page), same as clicking per-page Regenerate N times.
Current images are archived to history before deletion.

**Note:** style sheets are NOT regenerated — re-run Stage 2 first if style_guide changed and
the sheets need to reflect the new settings.

**Long-mode books:** a second confirm offers to also regenerate the shared text-page background
(`pages/text-bg-long.png`). Accept → the bg is archived to `pages/history/text-bg-long-<stamp>.png`
and the next render regenerates it fresh (+1 paid call). Cancel → bg preserved (per-page parity).
Per-page dedicated backgrounds (`page-NN-long-bg.png`) are also archived+deleted when `fresh_bg=true`.

Two new server endpoints (paid, require `GEMINI_API_KEY`, global regen-all lock):

- **`POST /api/regenerate-all`** body `{fresh_bg: bool}` (optional, default false) — archives and
  deletes every page's canonical artifacts (bg preserved unless `fresh_bg=true`), then spawns a
  single `render_book.py` run with no `--only` (pages render concurrently via asyncio; 30 min
  timeout). Sets `_regen_jobs[num] = "running"` for every page immediately — existing per-page
  regenerate/recomposite/select endpoints 409 via their existing lock checks; consolidate 409s
  via its existing `any(running)` check. Returns 200 immediately with `{ok, total}`.
  409 if regen-all, consolidation, or any per-page regen is already running.
- **`GET /api/regenerate-all/status`** — returns `{ok, status, error, total, done, pages}`.
  `done` is computed from file existence (not `_regen_jobs`, which all flip at subprocess exit).
  `pages` maps each page_num to its `_regen_jobs` entry — used by the client to clear
  per-page edit state after a successful run.

**Worker sweep:** after the subprocess exits (any exit path), each page is assessed by file
existence: preview found → adopt into history + mark done; not found → restore from history
(safe on empty history) + mark error. This keeps paid art that rendered successfully even if
rc≠0 (partial failure); missing pages are never left as holes.

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

## Repo layout notes

- This is a **git worktree** (`w1`); siblings `w2`, etc. share one bare repo. Skills live at
  top-level `skills/` and are served to Claude via a `.claude/skills` symlink at the parent
  level (commit `b73df4a`).
- `.gitignore` excludes generated artifacts: `pages/`, `story.json`, `*.png`. The `*.png` files
  in the tree (e.g. `eva.png`, `style-sheet-*.png`) are local sample data, not tracked.
- `README.md` is the human-facing intro and is **maintained manually by the user**. Do
  **not** update it as part of routine changes — leave it alone unless the user explicitly
  asks. `CLAUDE.md` is the living agent reference; keep that current instead.
