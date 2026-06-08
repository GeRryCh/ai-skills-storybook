# Smoke-testing changes (do this on task completion)

When a task is complete, smoke-test the change against the **pip-storm fixture** in
`tests/` before declaring done. Two fixtures ship committed artifacts so the no-API paths
can be exercised for free: `tests/fixtures/pip-storm/` (overlay + native pages, reference
image, style sheet) and `tests/fixtures/pip-storm-long/` (the long-mode fixture: full-bleed
art pages, shared text-page background `pages/text-bg-long.png`, text pages, long books):

```bash
# No API cost — validate all committed fixtures (must all exit 0)
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/pip-storm-long/story.json
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/gazelle-valley/story.json

# No API cost — crop the committed fixture ref image (happy path + overwrite loop)
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.2,0.1,0.8,0.9 --out /tmp/smoke-crop.png
# Adjust box and re-run (must overwrite silently)
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.1,0.05,0.9,0.95 --out /tmp/smoke-crop.png
# Pixel coords → exit 2 with "fractions, not pixels" message
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 120,40,800,900 --out /tmp/smoke-bad.png
# Degenerate box (left ≥ right) → exit 2
uv run skills/storybook-story/scripts/crop_character.py \
  --image tests/fixtures/pip-storm/refs/pip-ref.png \
  --box 0.8,0.1,0.2,0.9 --out /tmp/smoke-bad.png

# No API cost — exercise text overlay against a committed fixture page
uv run skills/storybook-render/scripts/overlay_text.py \
  --image tests/fixtures/pip-storm/pages/page-01.png \
  --text "Once upon a time..." --placement bottom --out /tmp/smoke.png

# No API cost — exercise text-page mode (long mode) against the committed shared bg
uv run skills/storybook-render/scripts/overlay_text.py \
  --image tests/fixtures/pip-storm-long/pages/text-bg-long.png \
  --text "Pip loved sunny days in the meadow." \
  --text-page --out /tmp/smoke-textpage.png

# No API cost — re-merge the committed fixture pages into a PDF (all three modes)
# Scripts now live in storybook-consolidate (moved from storybook-render in PER-44)
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-consolidate/scripts/merge_pdf.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long

# No API cost — re-merge into a fixed-layout EPUB3 (all three modes)
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm/story.json --text-mode native
uv run skills/storybook-consolidate/scripts/merge_epub.py \
  --story tests/fixtures/pip-storm-long/story.json --text-mode long

# No API cost — package smoke (--out /tmp to avoid polluting fixture dirs)
uv run skills/storybook-consolidate/scripts/package_book.py \
  --story tests/fixtures/pip-storm/story.json --out /tmp/smoke-package.zip
# Note: rebuilt EPUBs always differ (timestamp+uuid in content.opf) — run
# `git restore tests/fixtures` after smoking to discard churned fixture binaries.
```

Prefer these zero-cost checks; they cover overlay, text-page composition, PDF merge, EPUB
assembly, font resolution, and per-page selection logic without an image API call. Only fall
back to the paid `tests/regen.sh` (~10 image calls, needs `GEMINI_API_KEY`) when a change
actually touches the Gemini API call paths and must be verified end-to-end. When editing a
paid script, re-run a single proof first (`render_book.py --only N` /
`make_style_sheet.py` on one character) before any full regen.
