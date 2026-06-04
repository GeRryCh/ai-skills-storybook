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

1. **storybook-story** (free, no API) — writes `story.json`: per-page `text`, `image_prompt`,
   per-page `characters` cast list, and a global `characters` array. **Has a hard approval
   gate** — it must stop and wait for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call per character) — generates one
   `style-sheet-{name}.png` per character from the `characters` array, writes each
   character's `style_sheet` path back into `story.json`. **Approval gate**: show all
   sheets, get confirmation before rendering — a wrong sheet poisons every page that
   character appears on.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using only the style sheets for the characters listed in that page's `characters` field
   (per-page selection, cap 3), then overlays text. Output: `pages/page-NN.png` (overlay)
   or `pages/page-NN-native.png` (native). After a full render, automatically assembles
   all pages into `{title}.pdf` (or `{title}-native.pdf`) via `merge_pdf.py` at no extra
   API cost.

`story.json` is the contract between stages; its schema is `skills/storybook-story/assets/story_schema.json`.

## Critical external dependency

The two paid scripts call the **Gemini image API directly** (via the `google-genai`
Python SDK, declared as a PEP-723 inline dependency). They build a `genai.Client` with
`api_key` from the environment, model `gemini-3-pro-image` (style sheets) or `gemini-3.1-flash-image`
(page renders), send the prompt plus up to 3
input images as `types.Part.from_bytes`, and extract the returned image from
`part.inline_data.data`.
Requires `uv` on PATH and `GEMINI_API_KEY` in the environment. No sibling skill is
needed (an earlier version shelled out to `nano-banana-pro-openrouter`; that logic is now
inlined in each script — `run_nano_banana()` in `render_book.py` and `generate_image()` in
`make_style_sheet.py`).

## Running the scripts

All scripts are PEP-723 inline-dependency scripts — always run with `uv run` (it resolves
deps like Pillow automatically), never `python`:

```bash
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
```

`render_book.py` flags: `--from N` (resume), `--only N`, `--resolution 1K|2K|4K`,
`--text-mode overlay|native`, `--no-pdf` (skip auto PDF merge).
Pages are independent and all fired concurrently via `asyncio` (one async Gemini
request per page, no thread pool, no concurrency cap). Transient 429/5xx are retried
with exponential backoff + jitter, so wall-clock ≈ the slowest single page.

After a full render (`--only` not set), `render_book.py` automatically invokes
`merge_pdf.py` and writes `{title}.pdf` (overlay) or `{title}-native.pdf` (native)
next to `story.json`. Pass `--no-pdf` to suppress (e.g. for `--from` partial runs).

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
character's own `ref_image` (a single path or an array of paths), capped at 3 (the image
API input limit). There is no shared global pool — the cast-to-photo mapping is fixed in
Stage 1, so one character's photo never bleeds into another's sheet.

Each page also carries an explicit `characters` list (`pages[].characters`) naming which
cast members appear on it. `render_book.py`'s `collect_input_images(story, page)` uses
this to send only the relevant per-character style sheets — the model never sees sheets
for characters not on the page. The **first** name in `pages[].characters` is the page
**hero**: it additionally contributes its first original `ref_image` photo, so the render
anchors the hero's facial likeness on the real photo, not only on the (lossy) style sheet.
**Convention: author the hero/child first in each page's cast list.** Priority into the
3-image cap is hero sheet → hero photo → remaining characters' sheets in order; anything
past the cap (e.g. a third character's sheet) is logged, never silently dropped.

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

## Smoke-testing changes (do this on task completion)

When a task is complete, smoke-test the change against the **pip-storm fixture** in
`tests/` before declaring done. The fixture ships committed artifacts (a reference image,
a style sheet, and pre-rendered overlay + native pages under
`tests/fixtures/pip-storm/`) so the no-API paths can be exercised for free:

```bash
# No API cost — exercise text overlay against a committed fixture page
uv run skills/storybook-render/scripts/overlay_text.py \
  --image tests/fixtures/pip-storm/pages/page-01.png \
  --text "Once upon a time..." --placement bottom --out /tmp/smoke.png

# No API cost — re-merge the committed fixture pages into a PDF
uv run skills/storybook-render/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-render/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
```

Prefer these zero-cost checks; they cover overlay, PDF merge, font resolution, and
per-page selection logic without an image API call. Only fall back to the paid
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
