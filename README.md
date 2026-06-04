# AI Storybook

Three Claude Code skills that turn a story idea (optionally with character photos) into a fully illustrated children's picture book.

## The pipeline

Each skill hands off a single file — `story.json` — to the next stage.

1. **storybook-story** — Free. Writes `story.json`: page text, image prompts, and cast list. Has an approval gate before any paid stage runs.
2. **storybook-stylesheet** — Paid (1 image call per character). Generates a style-sheet PNG per character. Has an approval gate before rendering.
3. **storybook-render** — Paid (1 image call per page). Renders all page illustrations concurrently, overlays text, and auto-assembles a PDF.

## Requirements

- [`uv`](https://github.com/astral-sh/uv) on PATH
- `GEMINI_API_KEY` in your environment

## Quick start

After approving `story.json` (stage 1), run the paid stages:

```bash
# Stage 2 — generate character style sheets
uv run skills/storybook-stylesheet/scripts/make_style_sheet.py --story story.json

# Stage 3 — render all pages and produce a PDF
uv run skills/storybook-render/scripts/render_book.py --story story.json
```

Re-run either command safely: already-generated files are skipped automatically.

## More

See `CLAUDE.md` for full details: script flags, idempotency rules, text-overlay tuning, font configuration, and design decisions.
