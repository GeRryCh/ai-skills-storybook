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
uv run skills/storybook-story/scripts/validate_story.py \
  --story tests/fixtures/crowded-cast/story.json

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

## Automated tests (PER-63)

Two test layers now live in `tests/`. **Completion contract** (replaces the manual
checklist above for tasks touching tested code):

### On every task completion — run ALL unit tests

```bash
uv run tests/run_unit.py
```

All 53 tests must pass. No API key needed. No browser needed.

### On task completion — run SCOPED e2e (area you changed only)

Do **not** run the full e2e suite every time — it is browser-heavy. Run only the
module covering what you changed:

| Area changed | E2E command |
|---|---|
| editor.html `@`-picker / `_showMentionPop` | `uv run tests/run_e2e.py tests/e2e/test_mention_picker.py` |
| `_castIdsFromPrompt` / `_syncPageCast` / cast-on-page view | `uv run tests/run_e2e.py tests/e2e/test_cast_derivation.py` |
| `PUT /api/story` save / story.json round-trip | `uv run tests/run_e2e.py tests/e2e/test_save_roundtrip.py` |
| `#cost-readout` / `applyStatus()` / `/api/status`'s `costs` block | `uv run tests/run_e2e.py tests/e2e/test_cost_readout.py` |
| Pure Python function change only | No e2e needed |

First-time browser setup (once per machine):

```bash
uv run playwright install chromium
```

See `tests/README.md` for full documentation.
