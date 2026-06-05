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
      - GEMINI_API_KEY
---

# Storybook — Stage 3: Render Pages

## Preconditions

- `{out_dir}/story.json` exists (from **storybook-story**).
- `{out_dir}/style-sheet.png` exists and `story.json` has `style_sheet_path` set (from **storybook-stylesheet**). The script warns and produces weaker consistency if it is missing.
- `GEMINI_API_KEY` is set; `uv` is installed. The script calls the Gemini image API directly (no sibling skill needed).

If the style sheet is missing, run **storybook-stylesheet** first.

---

## Render

```bash
uv run {skillDir}/scripts/render_book.py \
  --story {out_dir}/story.json
```

**Useful flags:**
- `--from N` — resume from page N (skips earlier pages, also skips any already-existing files)
- `--only N` — render a single page (good for testing one page before a full run, or re-doing one page). Does **not** trigger the auto PDF/EPUB merge (it's a proof operation).
- `--resolution 1K|2K|4K` — override the resolution from `story.json` for this run. Resolution is normally configured in `story.json` via the top-level `resolution` field (default `2K` when not set); pass this flag to override it ad-hoc. `1K` is faster/cheaper for drafts; `4K` for large-format print.
- `--aspect-ratio RATIO` — override the aspect ratio from `story.json` for this run (choices: `1:1` `2:3` `3:2` `3:4` `4:3` `4:5` `5:4` `9:16` `16:9` `21:9`). Aspect ratio is normally configured via the top-level `aspect_ratio` field in `story.json`; when neither is set the model chooses framing per call.
- `--saved-formats pdf epub|none` — override `story.json`'s `saved_formats` for this run: which book file(s) to assemble after a full render. `epub` is a fixed-layout EPUB3 (pre-paginated, full-bleed pages). `none` skips assembly entirely (useful for partial `--from` runs where more pages are coming). `saved_formats` is normally configured in `story.json` (default: all formats when omitted).

All pages are fired concurrently via `asyncio` — one async Gemini request per page, no thread pool and no concurrency cap. Pages are independent (each call only uses the shared style sheet + character refs), so wall-clock ≈ the slowest single page. Transient `429`/`5xx` responses are retried automatically with exponential backoff + jitter (honoring `Retry-After`), so a momentary rate-limit no longer drops a page.

Output:
- `{out_dir}/pages/page-01.png` … `page-NN.png` (overlay mode)
- `{out_dir}/pages/page-01-native.png` … (native mode)
- `{out_dir}/{title}.pdf` or `{out_dir}/{title}-native.pdf` — assembled after a full run (per `saved_formats`)
- `{out_dir}/{title}.epub` or `{out_dir}/{title}-native.epub` — assembled after a full run (per `saved_formats`)

Each final file is printed as `MEDIA: <path>` so the IDE can display it inline.

To re-render a page after editing its `image_prompt`, delete `pages/page-NN.png` (and `pages/raw-page-NN.png`) then run with `--only N`.

---

## PDF & EPUB output

After a full render succeeds, book file(s) are assembled automatically (no extra API
cost) per the `saved_formats` field in `story.json` (default: all formats — both PDF
and EPUB). Files sit next to `story.json`, named after the book title:

- Overlay mode → `{out_dir}/{title}.pdf` and/or `{out_dir}/{title}.epub`
- Native mode  → `{out_dir}/{title}-native.pdf` and/or `{out_dir}/{title}-native.epub`

The EPUB is fixed-layout EPUB3 (pre-paginated): one full-bleed page image per spread,
viewport = image dimensions, page text carried as `<img>` alt attribute. Works for both
overlay and native text modes.

To rebuild from already-rendered pages (no render cost):

```bash
# PDF:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json
# native mode:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --text-mode native
# explicit output path:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --out my-book.pdf

# EPUB:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json
# native mode:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json --text-mode native
# explicit output path:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json --out my-book.epub
```

Missing pages emit a warning and are skipped; the output file is still built from the rest.

---

## Cost & failure notes

- Each page = one Gemini image call. 8 pages = 8 calls.
- **Strongly suggest** a 2-page proof run first: `--only 2` then `--only 3`.
- On any error, re-run with `--from N` — already-rendered pages are skipped.
- API errors: check `GEMINI_API_KEY` is set, `uv` installed, and Gemini account has credits.
- Resolution defaults to 2K (from `story.json`'s `resolution` field, or the 2K built-in fallback). Override ad-hoc with `--resolution 1K|2K|4K`.
- Aspect ratio defaults to unset (model chooses per call) unless `story.json`'s `aspect_ratio` field is set. Override ad-hoc with `--aspect-ratio`.

---

## Fonts (bundled, OFL licensed)

| Key | File | Use |
|-----|------|-----|
| `reader` | `Andika-Regular.ttf` | Body text — literacy-designed, open letterforms |
| `display` | `PatrickHand-Regular.ttf` | Cover/title overlays |

These are **roles**. Each page's `font` field in `story.json` selects the role (default `reader`); set the cover to `"font": "display"` for a title look.

**Custom fonts (config-level).** A top-level `fonts` map in `story.json` redefines what each role's font is:
```json
"fonts": { "reader": "Arial", "display": "Patrick Hand" }
```
Family names resolve at render time, in order: (1) bundled asset in `assets/fonts/`, (2) system-installed font (Arial, Georgia, Helvetica… — no manual install needed), (3) the bundled role default + a warning if the name can't be found. Rendering rasterizes to pixels, so using a system font does not redistribute the font file. Omit `fonts` to keep bundled Andika/PatrickHand.

Ad-hoc test: `overlay_text.py --font display --font-name "Arial"` (the `--font` role is the fallback if `--font-name` can't be resolved).

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

**Text-panel blending:** the text sits on a soft, feathered white panel that blends into the illustration (no hard edge). A `bottom` panel is anchored flush to the image bottom (full-bleed, no gap); a `top` panel keeps a 4%-of-image-height margin so its feather fades instead of clipping. Tune with:
- `--box-alpha N` — panel opacity 0–255 (default `205`; lower = more transparent, higher = more legible over busy art)
- `--feather N` — edge blur radius in px (default `14`; `0` = hard edge)
- `--align left|center` — horizontal text alignment (default `left`); driven per page by `text_align` in `story.json`. Use `center` for cover/title pages.

These default sensibly in `render_book.py`; only pass them when overriding for a specific image.

**Sizing:** font size auto-fits — it shrinks from 72px toward a 22px floor so the text fills the comfortable ~¼ safe zone. The panel may grow past that zone for long pages but is hard-capped at ⅓ of the page height; if text won't fit ⅓ even at the 22px floor, the font shrinks below the floor (down to a 12px hard minimum) so it still fits rather than clipping.

Per-page `story.json` text fields: `text_placement` (top/bottom/floating, default floating), `text_color_hint` (dark/light), `text_align` (left/center), `font` (reader/display).
