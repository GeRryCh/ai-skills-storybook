---
name: children-storybook
description: >
  Create a fully illustrated children's picture book from a story idea.
  Use this skill whenever the user mentions: children's storybook, picture book,
  illustrated kids' book, bedtime story with pictures, story for my kid/child/toddler,
  "make a book about X", "write a storybook", "generate a kids book", or any request
  that combines a story idea with the word "illustrate", "pages", or "book".
  Even if the user only describes a character and says "make a story" — use this skill.
  Works in two stages so the user can edit the text before spending money on images.
metadata:
  requires:
    bins:
      - uv
    env:
      - OPENROUTER_API_KEY
---

# Children's Storybook

## Overview

Two-stage workflow:

**Stage 1 (free)** — You draft a `story.json` manifest with per-page text and image prompts. The user edits and approves.

**Stage 2 (paid API calls)** — Scripts generate illustrations via the `nano-banana-pro-openrouter` skill (Gemini 3 image), then overlay the story text using a bundled children's-book font (Pillow). One API call per page.

Read `assets/STYLE_PRIMER.md` and `assets/story_schema.json` before writing the manifest. Mimic the structure in `assets/story_example.json`.

---

## Inputs

Gather these from the user (ask once if not provided):

| Input | Flag / source | Default |
|-------|---------------|---------|
| Story idea | free-text prompt | required |
| Character reference photos | `--character-image path` (repeatable) | none |
| Target age band | `--age 3-5` or `5-8` | `3-5` |
| Number of pages (spreads) | `--pages N` | `8` |
| Illustration style | `--style "soft watercolor, pastel palette"` | `"soft watercolor, gentle pastel palette, children's picture book"` |
| Output directory | `--out-dir path` | current working directory |

---

## Stage 1 — Draft the manuscript

1. Read `assets/STYLE_PRIMER.md` (word counts, text placement, safe-zone rule).
2. Read `assets/story_schema.json` to understand required fields.
3. Read `assets/story_example.json` as a concrete pattern to follow.
4. Write `{out_dir}/story.json` following the schema exactly.

### Page structure

- **Page 1**: cover. `text` = title only. `image_prompt` = full cover scene.
- **Pages 2 to N-1**: story body. Spread word counts guided by age (see STYLE_PRIMER).
- **Page N**: closing spread. One short sentence or just title/end.

### image_prompt rules

Every `image_prompt` MUST:
- Name every character that appears on that page (use consistent names throughout).
- State the art style.
- Include the text-safe-zone directive (the render script appends it, but write it anyway for clarity):
  > "Leave the [top|bottom] quarter of the image as a soft, low-detail, lightly-toned area suitable for overlaying text."
- NOT contain the actual story text — that is overlaid by Pillow.

### After writing story.json

Tell the user:
```
story.json written to: {out_dir}/story.json

Please review and edit the text and image prompts, then tell me when to proceed to Stage 2.
Tip: open the file in any text editor. Change 'text' fields freely. Keep 'page_num' intact.
```

Stop. Do not proceed until the user explicitly approves.

---

## Stage 2a — Generate character style sheet

Once the user approves, run:

```bash
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

This calls nano-banana once to produce `style-sheet.png` showing all named characters in the chosen style. The script writes the path back into `story.json`. Character ref images are used as input images (up to 3).

If the user supplied no character refs, the script still runs (prompt-only generation).

---

## Stage 2b — Render pages

```bash
uv run {skillDir}/scripts/render_book.py \
  --story {out_dir}/story.json \
  --resolution 2K
```

**Useful flags:**
- `--from N` — resume from page N (skips earlier pages, also skips any already-existing files)
- `--only N` — render a single page (good for testing one page before a full run)
- `--resolution 1K|2K|4K` — 1K is faster/cheaper for proofing, 2K for final output
- `--concurrency N` — render N pages in parallel (default `4`; pass `1` for serial). Pages are independent (each call only uses the shared style sheet + character refs), so parallel generation is safe and much faster. Lower it if you hit OpenRouter rate limits.

Output: `{out_dir}/pages/page-01.png` … `page-NN.png`

Each final file is printed as `MEDIA: <path>` so the IDE can display it inline.

---

## Cost & failure notes

- Each page = one nano-banana image call. 8 pages = 8 calls.
- **Strongly suggest** a 2-page proof run first: `--only 2` then `--only 3`.
- On any error, re-run with `--from N` — already-rendered pages are skipped.
- For API errors, see nano-banana-pro-openrouter's troubleshooting table (OPENROUTER_API_KEY, uv, credits).
- Each image uses 2K resolution by default (~2048px). Suitable for print at ~8"×8" and any screen size.

---

## Fonts (bundled, OFL licensed)

| Key | File | Use |
|-----|------|-----|
| `reader` | `Andika-Regular.ttf` | Body text — literacy-designed, open letterforms |
| `display` | `PatrickHand-Regular.ttf` | Cover/title overlays |

Pass `--font display` to `overlay_text.py` for title pages if desired.

---

## Quick overlay test (no API cost)

To verify Pillow + font before any image generation:

```bash
uv run {skillDir}/scripts/overlay_text.py \
  --image /path/to/any.jpg \
  --text "Once upon a time there was a brave little hedgehog." \
  --placement bottom \
  --out /tmp/test-overlay.png
```
