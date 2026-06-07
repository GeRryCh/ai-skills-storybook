---
name: storybook-consolidate
description: >
  Stage 4 of 4 in the storybook pipeline — assemble and export the finished book.
  Use when rendered pages already exist (from storybook-render) and the user wants
  finished book files or a shareable bundle — e.g. "make the PDF", "export the book",
  "assemble the EPUB", "re-merge", "rename the output file", "zip the book",
  "package the book". Free — no API calls: merges rendered page PNGs into a multi-page
  PDF and/or fixed-layout EPUB3, supports custom output names, and packages story.json
  + pages + style sheets + book files into one zip. Re-merging never re-renders.
  If pages are missing, run storybook-render first.
metadata:
  requires:
    bins:
      - uv
---

# Storybook — Stage 4: Consolidate & Export

## Preconditions

- `{out_dir}/story.json` exists (from storybook-story)
- Rendered page images exist in `{out_dir}/pages/` (from storybook-render)
- `uv` installed (no `GEMINI_API_KEY` required — this stage is free)

## Choose formats

Before assembling, ask the user which formats they want unless their request already names one (e.g. "make the PDF" → PDF only).

The `saved_formats` field in `story.json` is the **default answer**:
- Omitted → both PDF and EPUB (all formats)
- `["pdf", "epub"]` → both
- `["pdf"]` or `["epub"]` → that format only
- `[]` → user recorded "no book files" at Stage 1 — confirm before assembling anything

No script reads this field. The consolidate skill uses it as a hint and the interactive choice always wins.

## Assemble

```bash
# PDF — overlay mode (default naming: {slug(title)}.pdf):
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json
# native mode → {slug(title)}-native.pdf:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --text-mode native
# long mode → {slug(title)}-long.pdf:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --text-mode long
# explicit output path:
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --out my-book.pdf

# EPUB — overlay mode (default naming: {slug(title)}.epub):
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json
# native mode → {slug(title)}-native.epub:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json --text-mode native
# long mode → {slug(title)}-long.epub:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json --text-mode long
# explicit output path:
uv run {skillDir}/scripts/merge_epub.py --story {out_dir}/story.json --out my-book.epub
```

Output naming (`{title}` = slug of the book title from story.json):

| Mode    | PDF                        | EPUB                        |
|---------|----------------------------|-----------------------------|
| overlay | `{title}.pdf`              | `{title}.epub`              |
| native  | `{title}-native.pdf`       | `{title}-native.epub`       |
| long    | `{title}-long.pdf`         | `{title}-long.epub`         |

`--text-mode` overrides every page for this run (default: per-page/book-level story fields, then `native`). Per-page `text_mode` fields in `story.json` are honored for file selection (e.g. one long-mode page in an otherwise native book picks up its `page-NN-long.png` pair while other pages use `page-NN-native.png`). The output filename suffix (`-native`, `-long`, or plain) reflects the book-level mode, not per-page overrides. Files are written next to `story.json`.

Missing pages emit a warning and are skipped; the output file is still built from the rest.

## EPUB details

The EPUB is fixed-layout EPUB3 (pre-paginated): one full-bleed image per physical page, viewport = image dimensions, page text carried as `<img>` alt attribute. In long mode, the EPUB nav only lists art pages (one entry per logical story page); text pages follow each art page in the spine but don't add nav entries.

`dc:language` metadata comes from story.json's `language` field (BCP-47, default `en`).

## Custom output names

Pass `--out` to override the default slug-based name:

```bash
uv run {skillDir}/scripts/merge_pdf.py --story {out_dir}/story.json --out "Gift for Emma.pdf"
```

Re-running with a different `--out` is free — pages are not regenerated.

## Package into a zip

To bundle story.json, rendered pages, style sheets, and assembled book files:

```bash
uv run {skillDir}/scripts/package_book.py --story {out_dir}/story.json
# explicit output path:
uv run {skillDir}/scripts/package_book.py --story {out_dir}/story.json --out /path/to/book.zip
```

Default zip name: `{slug(title)}-book.zip` next to `story.json`.

Zip contents:
- `story.json`
- `pages/*.png` (final pages — excludes `raw-page-*.png` intermediates and the `pages/history/` generation archive)
- `style-sheet*.png` (character/object style sheets)
- `*.pdf` and `*.epub` (assembled book files — run the merges above first)

Run `merge_pdf.py` / `merge_epub.py` before `package_book.py` so the book files are included.

## Idempotency / cost

Everything in this stage is free Pillow/stdlib work — no Gemini calls. Outputs (PDF, EPUB, zip) are always rebuilt and overwritten on each run. Nothing ever re-renders pages. Re-run any time, including after re-rendering pages with storybook-render.
