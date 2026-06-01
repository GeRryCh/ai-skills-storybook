---
name: storybook-render
description: >
  Stage 3 of 3 in the storybook pipeline — render the illustrated pages.
  Use when an approved story.json AND a style-sheet.png already exist (from
  storybook-story + storybook-stylesheet) and the user wants to generate, re-render,
  or fix page illustrations — e.g. "render the book", "render the pages", "re-render
  page 3", "regenerate the pages", "redo the cover". Generates one illustration per
  page (using the style sheet as the consistency anchor) and overlays the story text.
  Costs one image API call per page. If the style sheet is missing, run
  storybook-stylesheet first; if story.json is missing, run storybook-story first.
metadata:
  requires:
    bins:
      - uv
    env:
      - OPENROUTER_API_KEY
---

# Storybook — Stage 3: Render Pages

## Preconditions

- `{out_dir}/story.json` exists (from **storybook-story**).
- `{out_dir}/style-sheet.png` exists and `story.json` has `style_sheet_path` set (from **storybook-stylesheet**). The script warns and produces weaker consistency if it is missing.
- `OPENROUTER_API_KEY` is set; `uv` is installed.
- The sibling skill `nano-banana-pro-openrouter` is installed (the script calls it).

If the style sheet is missing, run **storybook-stylesheet** first.

---

## Render

```bash
uv run {skillDir}/scripts/render_book.py \
  --story {out_dir}/story.json \
  --resolution 2K
```

**Useful flags:**
- `--from N` — resume from page N (skips earlier pages, also skips any already-existing files)
- `--only N` — render a single page (good for testing one page before a full run, or re-doing one page)
- `--resolution 1K|2K|4K` — 1K is faster/cheaper for proofing, 2K for final output
- `--concurrency N` — render N pages in parallel (default `4`; pass `1` for serial). Pages are independent (each call only uses the shared style sheet + character refs), so parallel generation is safe and much faster. Lower it if you hit OpenRouter rate limits.

Output: `{out_dir}/pages/page-01.png` … `page-NN.png`

Each final file is printed as `MEDIA: <path>` so the IDE can display it inline.

To re-render a page after editing its `image_prompt`, delete `pages/page-NN.png` (and `pages/raw-page-NN.png`) then run with `--only N`.

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

**Text-panel blending:** the text sits on a soft, feathered white panel that blends into the illustration (no hard edge). Tune with:
- `--box-alpha N` — panel opacity 0–255 (default `140`; lower = more transparent, higher = more legible over busy art)
- `--feather N` — edge blur radius in px (default `32`; `0` = hard edge)

These default sensibly in `render_book.py`; only pass them when overriding for a specific image.
