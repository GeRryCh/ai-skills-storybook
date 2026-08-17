# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A set of **four Claude Code skills** that together turn a story idea (optionally with
character photos) into a fully illustrated book (any genre or age). It is not an app — it
is skill definitions (`SKILL.md`) plus the Python scripts they invoke. There is no build
step; the scripts are the product. An automated test suite lives in `tests/` (see
**Tests** section below).

## The pipeline (read this first)

Four skills run in order and hand off a **single file, `story.json`**, in an output
directory (default: a newly created `{slug(title)}/` folder under the user's cwd;
an explicitly user-named path is used verbatim):

1. **storybook-story** (free, no API) — views any supplied photos (free, in-session), optionally analyzes a **style reference image** in-session to seed `style_guide` (PER-9: free, no API, same Claude-vision seam as cast photos), then writes `story.json`: per-page `text`, `image_prompt`, per-page `cast` list
   (mixed kinds), and a global `cast` array (characters, objects, and locations via `kind`). Validates `story.json` against `story_schema.json` via `scripts/validate_story.py` (free, stdlib-only, reuses the editor's validator — exit 2 on errors, including PER-97's hard-required ≤5-character-per-page cap). **Has a hard approval gate** — it must stop
   and wait for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call per cast entry + 1 book-wide) — generates
   one `style-sheet-{slug}.png` per cast entry from the `cast` array (characters, objects, and
   locations all get sheets; location sheets are built from the entry's downloaded real-place
   photos when present, else from `appearance` — PER-50), writes each entry's `style_sheet`
   path back into `story.json`. Also generates one book-wide `style-frame.png` (PER-82,
   "Lever B" — an abstract style board: palette swatches, a line/texture sample, a lighting
   study; no characters, no places, no scenery), written to the top-level `style_frame`
   field; a full run only (skipped under `--only`, whose contract is exactly one top-level
   diff — see the editor's per-cast regenerate flow below), idempotent (skipped if already
   present). **Approval
   gate**: show all sheets and the style frame, get confirmation before rendering — a wrong
   sheet poisons every page that character appears on, and a wrong style frame poisons
   every page in the book.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using only the style sheets for the cast entries listed in that page's `cast` field
   (per-page selection, two ref lanes — character lane 4 flash / 5 pro, object lane
   (objects + locations + the book-wide style frame, lowest priority) up to 10 flash / 6
   pro, 14 total — PER-83, PER-82, PER-96; auto-upgrades flash→pro when the
   character lane exceeds 4 — PER-58; overridable per page or book via the `model`
   field or `--model` CLI flag), then overlays text. When `story.json`'s top-level
   `style_frame` is set (Stage 2 output — PER-82, "Lever B"), it's sent as the
   lowest-priority object-lane reference on **every** page call (including scenery-only
   pages and the long-mode text-background calls) to anchor the look of everything that
   isn't cast — backgrounds, crowds, lighting, props — which text alone (`style_guide` +
   `premise`) doesn't fully lock down. Soft: an absent or missing-on-disk frame just warns
   and is skipped, unlike PER-84's hard-required location sheet. Three text modes:
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

Both paid scripts also write an append-only **`out_dir/costs.jsonl`** cost ledger (PER-35) —
one JSON record per API response actually received (not per page/sheet; an empty/blocked
Gemini retry still spent tokens and gets its own record). Both vendors return exact token
usage on the image response (OpenAI's `response.usage`, Gemini's `response.usage_metadata`),
so cost is computed from real usage, not estimated from a per-image flat rate. `usd` is
`null` when the billed model has no entry in the `PRICING` table (never guessed) and
`estimated: true` marks a record whose Gemini `candidates_tokens_details` modality
breakdown was absent (only the aggregate `candidates_token_count` was available, folded into
the image-output bucket). Both scripts print a `Cost this run:` / `Book total:` summary at
the end of any run that made at least one paid call. The helpers (`PRICING`,
`PRICING_AS_OF`, `gemini_call_cost()` — `render_book.py` only, `openai_call_cost()`,
`append_cost_record()`, `read_cost_ledger()`, `summarize_cost_records()`,
`format_cost_line()`) are on the same duplicated-and-kept-in-sync list as `append_api_log()`
— see `.claude/rules/script-authoring.md`. The visual editor (`edit_story.py`) reads the
same file (read-only — it never writes cost records) to fold a `costs` summary block into
`GET /api/status`, which the top-bar readout displays next to Save as the book-lifetime
total. `costs.jsonl` follows the exact same gitignore treatment as `log.txt`.

A top-level `style_guide` object is **required** in `story.json`: both paid scripts
assemble it into one byte-identical style block (`build_style_block()`, duplicated in
both scripts — keep the copies in sync) injected verbatim into every image call (both vendors). This is
the book-wide consistency mechanism (each page is a separate stateless call). Both scripts
**refuse to run** (`require_style_guide()`, exit 2) when it is missing or empty — breaking
change for pre-existing `story.json` files; add the field to render old books. The `style`
string remains as a short human label only.

An optional top-level `premise` string (PER-66) is the **textual analogue of the verbatim style block** — the narrative consistency anchor. It is injected verbatim into every page render prompt immediately after `STYLE_ANCHOR`, for all three text modes (overlay / native / long), via `book_premise(story)` in `render_book.py`'s `build_image_prompt`. **Abstract atmosphere/intent only:** genre, audience age, tone, season, time-of-day arc, narrative register. **NEVER** plot, scenes, named places, or per-page objects — those bleed into every page (the PER-33 location-bleed bug class). Empty/absent premise → zero behavioural change. `premise` is deliberately **NOT** part of `build_style_block()` (which also feeds Stage 2 OpenAI stylesheet calls — narrative premise is noise there) and is not injected into the long-mode text background prompt.

An optional top-level `style_frame` string (PER-82, "Lever B") is `premise`'s **visual counterpart** — the image analogue of the verbatim style block, instead of the textual one. It's a path to one book-wide `style-frame.png` (an abstract style board: palette swatches, a line/texture sample, a lighting study), generated once by `make_style_sheet.py` (Stage 2) and written back into `story.json`. `render_book.py` sends it as the lowest-priority object-lane reference image on every page render call (see "Ref priority and lanes" below) to anchor the look of everything that isn't cast — backgrounds, crowds, lighting, props — which text alone doesn't fully lock down. **Abstract style samples only, same PER-33 discipline as `premise`:** NEVER a scene, a story location, or any narrative content — a book-wide reference depicting an environment bleeds into every page, including pages set elsewhere. Absent/missing-on-disk → zero behavioural change (soft, unlike PER-84's hard-required location sheet).

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
- **`render_book.py` (Stage 3) → Google Gemini (default) + OpenAI `gpt-image-2` (fallback)**
  via the `google-genai` + `openai` SDKs. Builds a `genai.Client` (lazily, on first paid call)
  with `api_key` from the environment; configurable model — default `gemini-3.1-flash-image`
  (character-lane cap 4) or `gemini-3-pro-image` (character-lane cap 5), set per-page,
  book-wide, or via `--model` CLI flag (auto-upgrade flash→pro when the character lane
  exceeds 4; the object lane — objects + locations, cap 10 flash / 6 pro (PER-96) — never
  triggers an upgrade itself — PER-83). Sends the prompt plus reference images as
  `types.Part.from_bytes`, extracts the returned image from `part.inline_data.data`. Requires
  `GEMINI_API_KEY`. **OpenAI fallback (PER-67):** when Gemini returns
  `finish_reason=PROHIBITED_CONTENT` (a deterministic content-policy block, not a transient
  error), the page is automatically retried on OpenAI `gpt-image-2` using the exact same
  resolved prompt and selected style-sheet references (no `STYLE_BOOST`, no per-kind system
  prompt — those are Stage-2 apparatus for redrawing from photos; style-sheet refs must be
  reproduced faithfully). Uses `images.edit` (multiple refs) or `images.generate` (no refs),
  `quality="medium"`, `moderation="low"` via `extra_body`. Size from `aspect_to_size(aspect_ratio)`
  (kept in sync with `make_style_sheet.py`). Because `images.edit` has **no labeled-interleaved
  channel** like the Gemini path's `"Next image: {label}."` parts, an **ordered reference manifest**
  (`_build_ref_manifest`) is prepended to the prompt — it numbers each sheet by its label in
  `image=[...]` order so the model binds each sheet to its named subject. Without it a
  multi-character page collapses two distinct sheets into one design (renders the same character
  twice). Fallback only fires when `fallback_vendor="openai"`
  (the default) AND `STORYBOOK_SKILL_OPENAI_API_KEY` or `OPENAI_API_KEY` is present; otherwise
  the page fails as today. Vendor recorded in `log.txt` via `model=gpt-image-2` (distinguishable
  from `gemini-*` entries). Transient 5xx/429 errors are NOT affected — they keep the Gemini
  retry path. Controlled by `--fallback-vendor openai|none` (CLI) or `fallback_vendor` story.json
  field; precedence: CLI > story field > `openai` default.

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
`--aspect-ratio RATIO` (override from story.json; unset → built-in default `3:2`; pass/set `auto` to opt out and let the model choose framing per call),
`--text-mode overlay|native|long` (override for entire run; precedence: CLI > per-page `text_mode` field > book `text_mode` field > native default; per-page `text_mode` lets individual pages differ from the book default without this flag),
`--model gemini-3.1-flash-image|gemini-3-pro-image`
(override per-page/book model for one run; precedence: CLI > page field > story field > flash default),
`--out-dir DIR` (output directory for `pages/`, `log.txt`, `costs.jsonl`; default: same dir as `--story`),
`--composite-only` (abort instead of making any paid Gemini call; only rebuild free Pillow
composites from existing raw/art/bg files — needs no `GEMINI_API_KEY`; pages that would
require a new image fail with a clear message naming the missing prerequisite, exit 1),
`--fallback-vendor openai|none` (vendor to try when Gemini returns `PROHIBITED_CONTENT`;
default `openai` — auto-retry on `gpt-image-2` when an OpenAI key is present, else fail
as today; `none` disables the fallback; precedence: CLI > story `fallback_vendor` field > `openai`).
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
for cast entries not on the page. The **hero** is the first cast entry of `kind: "character"` (or kind absent, defaulting to character) in `pages[].cast`: it leads reference ordering into the character lane so its style sheet is positioned first and never dropped. Characters contribute only their style sheet (no extra photo). **Convention: author the hero/child first among the character-kind entries in each page's `cast` list.**

**Two ref lanes, not one flat cap (PER-83).** The real Gemini reference-image envelope is
lane-based: a **character lane** (4 flash / 5 pro, high-resemblance) and an **object lane**
(objects + locations). Both lane caps are per-model (PER-96) — Google's documented envelope
sums to 14 for either model but splits it differently: the object lane is **cap 10 flash /
6 pro** (Pro spends more of its 14 slots on the character lane and a 3-slot style-reference
lane we don't use yet). Priority within each lane: character lane = hero sheet → remaining
character sheets (page order); object lane = **location refs first** (page order), then
object refs (page order) — location outranks object because a wrong-style background
poisons the whole frame while a slightly-off prop does not (flip of the pre-PER-83
object-before-location order). Anything past its lane's cap is logged, never silently
dropped. **Auto-upgrade (PER-58, narrowed by PER-83):** `select_refs()` in `render_book.py`
runs the upgrade check before either cap is applied — if the effective model is flash and
the **character lane** has >4 entries, the page is silently upgraded to `gemini-3-pro-image`
for that call only (logged, story.json untouched); the object cap is then resolved from the
now-effective model. Object-lane overflow never triggers an upgrade on its own — but because
the object cap is itself per-model, a character-triggered upgrade can shrink the object lane
from 10 to 6 as a side effect (accepted trade-off: character consistency outranks a prop).
The lane/cap/drop logic is in `select_refs`; `collect_input_images` returns the full uncapped
candidate list tagged with `(label, path, lane)`.

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

**Stage 1 (in-session, free):** the agent detects real named places and searches the
**Wikimedia Commons API directly via `curl`** — category listing
(`list=categorymembers&cmtitle=Category:<name>`) first for hit quality, file text search
(`list=search&srnamespace=6`) second, `perplexity_search` demoted to a fallback (unreachable
API, or to help identify the category name) — to find ~3 distinct freely-licensed photos
(different angles/views preferred, **daylight and people-free** — landmark searches skew
heavily toward night shots, which bias the location style sheet dark and bleed into every
page using it), builds a deterministic download URL per photo, and runs `fetch_location.py`
once per photo (numbered outputs `loc-{slug}-1.jpg`, `-2.jpg`, `-3.jpg`; the URL is
percent-encoded internally, so non-ASCII Commons titles — Cyrillic, Azerbaijani, CJK — pass
through human-readable), validating each inline (right place, well-framed, daylight, no
prominent people). Accept 1-2 when Commons lacks suitable photos (min 1) and tell the user.
The resulting paths go into the cast entry's `ref_image` array. If neither the Commons API
nor the Perplexity MCP is reachable, photo gathering is skipped with a user-facing message
(the place still gets an appearance-only sheet) — Stage 1 never fails over this.

**`fetch_location.py`** (`skills/storybook-story/scripts/fetch_location.py`): PEP-723,
Pillow + stdlib `urllib`. Validates HTTP status, `content-type: image/*`, decodes with
Pillow, checks min edge (≥512px default), downscales to max edge (≤1536px default),
mode-normalises to RGB, saves as JPEG. Prints `MEDIA: {out}` for inline preview; invoked
once per photo (PER-50).

**Stage 2 policy (changed in PER-50):** `make_style_sheet.py` generates a sheet for
**every** location entry — from the real-place photos in `ref_image` when present
(architecture/landmarks/geography anchored, rendered in the book style, style-transfer
framing — PER-84), from `appearance` alone otherwise (fictional recurring place). The
location-sheet prompt ranks the book's art style above photographic fidelity while still
preserving the place's recognisable architecture/landmarks/geography (the documented
exception to the no-scenery rule: geography IS the subject on a location sheet).

**Hard-require a location style sheet before render (PER-84, extends PER-69's character
gate).** A raw location photo is a photoreal-bleed vector — one "redraw in book style"
sentence has to fight a full photographic reference, and it can fail either way (dropped
at the ref cap → unanchored hallucination; sent → photoreal pull surviving into the
render). There is **no render-time photo fallback for locations**: a page whose location
cast entry has no usable `style_sheet` fails that page (`render_book.py`'s
`missing_required_sheets`, before any paid call; error names the place and points at
`make_style_sheet.py`) — other pages still render, run exits 1. Pre-PER-50/PER-84 books
whose locations carry only a photo must re-run Stage 2 (builds the missing sheets) before
those pages will render. Objects are unaffected — they keep the photo fallback (a
slightly-off prop does not poison a frame the way a wrong-style background does).

**Ref priority and lanes (`render_book.py`, PER-83):**

> **character lane** (cap 4 flash / 5 pro): hero sheet → remaining character sheets (page order)
> **object lane** (cap 10 flash / 6 pro — PER-96): location refs (sheet only, hard-required — PER-84 — page order) → object refs (sheet, or photo fallback — page order) → **book-wide style frame** (PER-82, lowest priority, first to drop)

`collect_input_images()` builds the full prioritized candidate list (no cap), tagged
`(label, path, lane)`. `select_refs(candidates, model)` then: auto-upgrades flash → pro when
the **character lane** has `> 4` entries, resolves each lane's cap from the now-effective
model and applies it independently, and returns `(effective_model, selected, dropped)`. A
`TOTAL_REF_CAP` of 14 is a defensive backstop for the case both lanes are simultaneously
maxed; with the current per-model caps no combination actually reaches it (flash 4+10=14,
pro 5+6=11), so it never fires today. Drops are logged, never silent. Flash pages with a 5th
character are upgraded to pro before any character is dropped; only past the pro
character-lane cap (5) are characters dropped. Object-lane overflow never triggers an
upgrade on its own — but because the object cap is itself per-model (10 flash / 6 pro), a
character-triggered upgrade can shrink the object lane as a side effect: a page with 5
characters and 8 props upgrades to pro to keep all 5 characters, and loses 2 props it would
have kept on flash. This trade-off is accepted deliberately (character consistency is the
hero mechanism; the object lane's tail is the cheap end to drop). On scenery-only pages
(`cast: []` or only non-character entries) with a location set, the location style sheet is
the sole per-page-cast reference image (PER-84 — no photo fallback for locations); the
book-wide style frame, if set, is still attached on top of it (it's sourced from `story`,
not `page.cast`).

**Authoring-time hard block (PER-97).** The character-lane overflow described above (a page
past its lane cap silently loses sheets at render) is now unreachable through the normal
authoring path: `validate_story()` (`edit_story.py`, shared by the CLI `validate_story.py`
and the editor's `PUT /api/story`) hard-errors any page with more than 5 character-kind
`pages[].cast` entries — model-independent, since 5 auto-upgrades cleanly but 6 always drops
a sheet. The editor's `@`-mention picker also disables further character-kind rows once a
page's character lane is full. This is authoring-time only — render-time keeps the
drop-and-log behaviour above unchanged (a rendering stage must not fail a whole book), and a
`story.json` built or edited outside the editor/validator can still reach `render_book.py`
over-cap. The object lane is unaffected — it stays a warning (editor badge only).

**Book-wide style frame (PER-82, "Lever B").** `story.json`'s top-level `style_frame`
(written by `make_style_sheet.py`, Stage 2 — one abstract style board per book: palette
swatches, a line/texture sample, a lighting study; no characters, no places, no scenery) is
read directly from `story`, not `page['cast']`, so `collect_input_images()` attaches it on
**every** call for that book — including the long-mode text-background calls
(`run_nano_banana(..., story, {"cast": []}, ...)`), since a style board is an apt reference
for a style-matched background too. Soft, unlike PER-84's hard-required location sheet: an
absent or missing-on-disk frame just warns and is skipped — it never fails a render. It
never contributes to the flash→pro auto-upgrade (only the character lane does). It sits at
the bottom of the object lane, so it's the first thing dropped when that lane is over cap —
and since the object cap is per-model (PER-96: 10 flash / 6 pro), it now drops materially
more often on pro, whenever a page carries ≥6 real object/location refs.

**Labeled-interleaved contents (`run_nano_banana`):** each reference image is preceded by a short text part: `"Next image: {label}."` The `IMAGE_SYSTEM_PROMPT` defines the behaviour rule for each of 6 label kinds (locations have no photo-fallback label render-side — PER-84; Stage 2 still labels input photos when *building* the sheet). Keep label wording in sync with the system prompt's "kind" vocabulary:

- `"character style sheet for {name}"` → defines design, outfit, art style
- `"real photograph of the character {name} (facial likeness reference)"` → face only, outfit from sheet
- `"object reference sheet for {name}"` → defines object design, colours, proportions
- `"real photograph of the object {name} (appearance reference)"` → shape/materials/details reference
- `"location reference sheet for {name}"` → defines place's look in book style
- `"book style reference — match its rendering technique, palette, and line treatment exactly; it depicts no specific scene"` (PER-82) → matches rendering technique/palette/line treatment only; never copy its layout, swatches, or any depicted object

`STYLE_ANCHOR` contains `"of a character"` in the photo-matching sentence to prevent the anchor from instructing the model to extract a face from a landmark photo on scenery-only pages.

## Auto-injected prompt guards (PER-87)

`render_book.py`'s `build_page_guards()` appends three defensive clauses to every page
prompt transparently, the same way `text_placement` safe-zone language already is (PER-7
precedent) — folded into `build_image_prompt`'s `anchor` string right after the optional
`premise`, before the mode-specific directive, across all four prompt paths (overlay,
native, long body, and the long-mode cover, which renders via the overlay path). These
guards synthesize from `story.json` data the renderer already has; authors never hand-write
this boilerplate, and a proof render (see storybook-render's default flow below) is the
fast way to confirm they're enough before paying for a full run.

1. **No-duplicate-characters guard** — derived from the page's `kind: "character"` cast
   entries (via `cast_index()`, resolved to display names, never ids — PER-56): *"Exactly 3
   named characters in this scene, and no duplicates: one Eva, one Grandpa Vagif, one
   German. Unnamed background figures such as crowds or passers-by are allowed and are not
   counted."* Objects/locations are never counted; `cast: []` (scenery-only) pages get no
   guard. Mirrors the anti-duplication sentence `_build_ref_manifest` already carries on
   the OpenAI fallback path, now on the primary Gemini path too, where the defect was
   observed (two Evas in one frame, both matching the style sheet).
2. **`cast[].persistent_details`** — an optional string for a small accessory/prop the
   style sheet alone doesn't reliably hold onto (e.g. `"dark baseball cap with sunglasses
   resting on the brim"` dropped by flash despite being on all four sheet views). Appended
   as a continuity clause on every page that entry is on. This is the sanctioned channel
   the PER-42 `_appearance_echo` warning now points to — writing the same cue into
   `image_prompt` instead trips that warning, so the two must never be cross-checked
   against each other (that would recreate the exact trap this closes).
3. **`scene_text: "suppress" | "allow"`** (default `"suppress"`, book-level + per-page
   override + `--scene-text` CLI, precedence identical to `text_mode`'s: CLI > page field >
   story field > default) — bans invented diegetic lettering (signs, shopfronts, logos),
   since image models reliably garble it (a real airport page rendered two mangled,
   duplicated signs). The native-mode wording carve-outs the model's own story-text
   lettering (*"Apart from the story text specified below, ..."*) and is injected ahead of
   `NATIVE_TEXT_DIRECTIVE`, which stays the final token in native mode (same insertion
   discipline as PER-66's premise fold). `"allow"` emits **no replacement clause** — not
   even a "spell it correctly" directive, which in native mode would duplicate
   `NATIVE_TEXT_DIRECTIVE`'s existing exact-reproduction instruction one sentence later.
   Never reaches `build_text_bg_prompt` (the long-mode text-page background call) — that
   prompt already hard-bans lettering unconditionally, and must, regardless of the knob.

`FULL_BLEED_ART_DIRECTIVE` (long-mode art pages) is narrowed to ban only story text/
narrative typography, not all lettering — the blanket ban moved to the scene-text guard
above so `scene_text: "allow"` has something to opt out of. Net effect on the default path
is unchanged (suppress restores the total ban); this does change prompt bytes for every
long-mode page of every existing book, even ones setting none of the three new fields.

The default `storybook-render` flow (item 4) is a single-page proof render before any full
run: pick the page with the largest `cast`, `--only N`, review, apply fixes (including the
three fields above), **`rm pages/page-NN*.png`** (skip this and idempotency silently keeps
the pre-fix proof page in the full run), then render everything. See storybook-render's
SKILL.md for the full sequence.

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
`<id>` placeholder, filterable by id/name, all kinds with badges, keyboard nav —
**character-kind rows are shown disabled with a reason line once the page's character
lane is full (PER-97: hard cap 5); already-mentioned ids and object/location rows stay
selectable**, since re-mentioning an id already on the page is a no-op for the count), a
**read-only "Cast on this page" view derived from the prompt's `<id>` mentions** (PER-62:
the image_prompt is the single source of truth for a page's cast — `_castIdsFromPrompt()`
collects valid `<id>` tokens in first-appearance order, `_syncPageCast()` reconciles
`page.cast` on every prompt edit; hero = first character-kind mention; no manual
add/remove/reorder UI), render-status badges, **per-page image preview, generation
history browser, a regenerate button, a per-page model picker** (retry knob: set a page to `gemini-3-pro-image`
and hit Regenerate to retry that page on the stronger model without touching the rest), **a
per-page ref-count badge** (PER-58, made lane-aware in PER-83, made both lane caps
per-model in PER-96: amber "5 characters → pro required" when the intent-based **character**
count is 5 and the effective model is flash — render auto-upgrades at runtime; red
"N characters > hard cap 5 — save blocked" when character count exceeds 5 — the picker guard
above and `validate_story()`'s hard error (PER-97, see below) mean this state can no longer
reach a save; "N objects/locations > object-lane cap 10|6 — refs will drop" is still a
warning-only badge, since the object lane has no save block, when the object-lane
(objects + locations) count exceeds its cap for the **effective** model — 10 on flash, 6 on
pro, resolved after the same auto-upgrade check the badge mirrors from `select_refs`), and
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
warning (non-blocking) tiers — a page's character-kind count exceeding the hard cap of 5
is an error tier check (PER-97), same as unknown `<id>` placeholders; see
`validate_story()` in `edit_story.py`. Includes a 409 conflict guard: if `story.json`
changes on disk while the editor is open (e.g. Stage 2 writes `style_sheet` paths), the save
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
a reloaded client resume an in-flight sheet poll. It also includes a top-level
`style_frame_exists: bool` (PER-82) — read-only preview flag for the book-wide style frame;
no regen job to track (the editor has no generate/regenerate UI for it — override is
`rm style-frame.png` + re-run `make_style_sheet.py`, same as sheets).

**`make_style_sheet.py --only NAME`** (PER-59): process only the named cast entry; the slug
walk still runs for ALL entries so filenames stay stable. Exit 2 if name not found.
This is NOT equivalent to delete-PNG + full run: the full run rewrites `style_sheet` for
every entry (absolute paths), which would desync the client's single-field mtime patch.

**Known limitations:** pages using a regenerated sheet are stale but show no stale badge
(badge tracks session edits, not sheet changes). The confirm-dialog warns the user. The
book-wide style frame preview (PER-82) reflects the last full `/api/status` fetch (page
load, after a save, or after a sheet regen completes) — if `make_style_sheet.py` is run in
another terminal while the editor is open, the preview and ref-count badge only pick up the
new frame after a page reload.

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

## Tests

Two layers live in `tests/`. **Zero-API rule: no test may call Gemini or OpenAI.**

| Layer | Command | API key? | Browser? |
|---|---|---|---|
| Unit | `uv run tests/run_unit.py` | No | No |
| E2E | `uv run tests/run_e2e.py` | No | Yes (chromium) |

**Completion contract** (see also `.claude/rules/smoke-tests.md`):
- Run **all unit tests** on every task completion — must all pass.
- Run **scoped e2e** (the module covering the area you changed) — not the full suite.
- First-time browser setup: `uv run playwright install chromium`
- Background task agents only (launched by `scripts/new-worktree.sh`) additionally do a
  Playwright-MCP **visual check** on UI-visible changes — see the gated section in
  `.claude/rules/smoke-tests.md`. Interactive sessions skip it unless asked.

Unit tests cover `select_refs`, `resolve_cast_placeholders`, `collect_input_images`,
`validate_story`/`_appearance_echo`, and CLI exit codes for all three committed fixtures.

E2E tests cover the `@`-mention picker (PER-62 regression guard), cast-on-page derivation
(`_castIdsFromPrompt`/`_syncPageCast`), and save round-trip (`PUT /api/story`).

See `tests/README.md` for full documentation and subset runner usage.

## Repo layout notes

- This is a **git worktree** (`w1`); siblings `w2`, etc. share one bare repo. Skills live at
  top-level `skills/` and are served to Claude via a `.claude/skills` symlink at the parent
  level (commit `b73df4a`).
- `.gitignore` excludes generated artifacts: `pages/`, `story.json`, `*.png`. The `*.png` files
  in the tree (e.g. `eva.png`, `style-sheet-*.png`) are local sample data, not tracked.
- `README.md` is the human-facing intro and is **maintained manually by the user**. Do
  **not** update it as part of routine changes — leave it alone unless the user explicitly
  asks. `CLAUDE.md` is the living agent reference; keep that current instead.
