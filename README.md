# AI Storybook

> **Gemini Storybook, with the controls.** Approve the script before each step,
> lock each character's look, anchor faces to real photos, and tune text and
> layout per page.

Four Claude Code skills that turn a story idea (optionally with character photos) into a fully illustrated children's picture book.

> ⚠️ **Constant development — breaking changes are the norm.** This project evolves
> aggressively and makes no backward-compatibility promises. The `story.json` contract,
> script flags, and schema requirements change whenever a better design wins (e.g.
> `style_guide` is now mandatory and older `story.json` files won't render until they
> add it). Error messages always include what to fix. If you have an old book, expect
> to touch up its `story.json` before re-rendering.

## The pipeline

Each skill hands off a single file — `story.json` — to the next stage.

1. **storybook-story** — Free. Writes `story.json`: page text, image prompts, and cast list (characters, objects, and locations). Has an approval gate before any paid stage runs.
2. **storybook-stylesheet** — Paid (1 image call per cast entry). Generates a style-sheet PNG per cast entry. Has an approval gate before rendering.
3. **storybook-render** — Paid (1 image call per page). Renders all page illustrations concurrently and overlays text (overlay, native, or long mode).
4. **storybook-consolidate** — Free. Assembles rendered pages into PDF and/or EPUB, and optionally packages everything into a zip.

## Requirements

- [`uv`](https://github.com/astral-sh/uv) on PATH
- `GEMINI_API_KEY` in your environment

## Quick start

After approving `story.json` (stage 1), run the paid stages:

```bash
# Stage 2 — generate style sheets for all cast entries
uv run skills/storybook-stylesheet/scripts/make_style_sheet.py --story story.json

# Stage 3 — render all pages
uv run skills/storybook-render/scripts/render_book.py --story story.json

# Stage 4 — assemble PDF / EPUB (free, no API key needed)
uv run skills/storybook-consolidate/scripts/merge_pdf.py --story story.json
uv run skills/storybook-consolidate/scripts/merge_epub.py --story story.json
```

Re-run any command safely: already-generated files are skipped automatically.

## Visual editor

Edit `story.json` without touching raw JSON — browse cast photos, regenerate individual pages or style sheets, pick text mode per page, and run Stage 4 when ready:

```bash
uv run skills/storybook-story/scripts/edit_story.py --story /path/to/story.json
```

## More

See `CLAUDE.md` for full details: script flags, idempotency rules, text-overlay tuning, font configuration, and design decisions.
