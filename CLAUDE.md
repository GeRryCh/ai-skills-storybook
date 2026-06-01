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
   and an explicit `characters` cast. **Has a hard approval gate** — it must stop and wait
   for the user to edit/approve before any paid stage runs.
2. **storybook-stylesheet** (paid, 1 image call) — generates `style-sheet.png` from the
   `characters` array, writes `style_sheet_path` back into `story.json`. **Approval gate**:
   show the sheet, get confirmation before rendering — a wrong sheet poisons every page.
3. **storybook-render** (paid, 1 image call per page) — generates each page illustration
   using the style sheet + character refs as the consistency anchor, then overlays text.
   Output: `pages/page-NN.png`.

`story.json` is the contract between stages; its schema is `skills/storybook-story/assets/story_schema.json`.

## Critical external dependency

The two paid scripts call the **OpenRouter image API directly** (via the `openai`
Python SDK, declared as a PEP-723 inline dependency). They build an OpenAI client with
`base_url=https://openrouter.ai/api/v1`, model `google/gemini-3-pro-image-preview`, send
the prompt plus up to 3 base64 data-URL input images, and decode the returned image.
Requires `uv` on PATH and `OPENROUTER_API_KEY` in the environment. No sibling skill is
needed (an earlier version shelled out to `nano-banana-pro-openrouter`; that logic is now
inlined in each script — `run_nano_banana()` in `render_book.py` and `generate_image()` in
`make_style_sheet.py`).

## Running the scripts

All scripts are PEP-723 inline-dependency scripts — always run with `uv run` (it resolves
deps like Pillow automatically), never `python`:

```bash
# Stage 2
uv run skills/storybook-stylesheet/scripts/make_style_sheet.py --story story.json

# Stage 3 — render all pages (2K default, 4 parallel)
uv run skills/storybook-render/scripts/render_book.py --story story.json --resolution 2K

# Render / re-render a single page (proof before a full run)
uv run skills/storybook-render/scripts/render_book.py --story story.json --only 3

# Text overlay only, no image API cost (verify Pillow + fonts)
uv run skills/storybook-render/scripts/overlay_text.py \
  --image any.png --text "Once upon a time..." --placement bottom --out /tmp/t.png
```

`render_book.py` flags: `--from N` (resume), `--only N`, `--resolution 1K|2K|4K`,
`--concurrency N` (default 4; pages are independent so parallel is safe).

## Idempotency / re-run semantics (important when editing scripts)

Both paid scripts **skip work whose output already exists**: `make_style_sheet.py` skips if
`style-sheet.png` exists; `render_book.py` skips any `page-NN.png` that exists. To force a
regenerate you must `rm` the target file (and the matching `raw-page-NN.png` for a page)
first. Preserve this behaviour — it makes partial-failure re-runs cheap.

## Key design decision: explicit cast, never prose-scraped

The `characters` array in `story.json` is authored explicitly and is the **only** source for
the style sheet. An earlier regex that scraped characters from prose minted phantom
characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit")
and poisoned every page. Do not reintroduce auto-extraction. See the docstring on
`get_characters()` in `make_style_sheet.py`. Reference images are capped at 3 (the image
API input limit): per-character `ref_image` first, then the global `character_refs` pool.

## Text overlay (`overlay_text.py`)

Pillow composites text on a feathered, semi-transparent rounded white panel that blends into
the art (no hard edge). Font size auto-shrinks to fit the bottom/top 25% safe zone. Two
bundled OFL fonts: `reader` (Andika, body) and `display` (PatrickHand, titles). Tunables are
module constants near the top (`BOX_ALPHA`, `FEATHER_PX`, padding, font px range) exposed as
`--box-alpha` / `--feather` flags. The render script never bakes story text into the
generated image — every `image_prompt` reserves a low-detail safe zone for this overlay.

## Repo layout notes

- This is a **git worktree** (`w1`); siblings `w2`, etc. share one bare repo. Skills live at
  top-level `skills/` and are served to Claude via a `.claude/skills` symlink at the parent
  level (commit `b73df4a`).
- `.gitignore` excludes generated artifacts: `pages/`, `story.json`, `*.png`. The `*.png` and
  `eva.png` / `style-sheet.png` in the tree are local sample data, not tracked.
