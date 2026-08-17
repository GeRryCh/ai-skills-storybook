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

### Visual check via Playwright MCP — BACKGROUND TASK AGENTS ONLY

**Applies only to background task agents** — the sessions `scripts/new-worktree.sh` launches
with `--settings scripts/bg-task-guardrails.json`, which is the only place `mcp__playwright__*`
is pre-authorized. **If you are an interactive session, skip this section** unless the user
asks for a visual check: your permissions would prompt per call, and a human is already
looking at the screen. Everything above this heading applies to every session.

The scoped e2e asserts behaviour; it does not tell you whether the thing **looks** right,
and a background agent has nobody watching. So when a bg task's change alters what the
editor renders — `assets/editor.html` markup/CSS/JS, or a server field the UI displays
(`/api/status`, `/api/versions`, `/api/sheet/versions`, `/api/consolidate/status`) — do one
visual pass with the `playwright` MCP server before committing. This is **in addition to**,
never a substitute for, the unit + scoped-e2e contract.

Serve a **fixture** book, never the user's own book:

```bash
# leave this running in the background; --no-browser so no real browser window opens
uv run skills/storybook-story/scripts/edit_story.py \
  --story tests/fixtures/pip-storm/story.json --no-browser --port 8766
```

Then, with the MCP tools: `browser_navigate` to `http://127.0.0.1:8766`,
`browser_resize` to a realistic desktop viewport, `browser_snapshot` to read the
accessibility tree, and `browser_take_screenshot` of the control you changed — attach or
reference that screenshot in the completion report. Close with `browser_close` and stop the
server; a stray editor holding port 8766 breaks the next run (use another port if 8766 is
already taken).

Rules for the visual pass:

- **Never click a paid or mutating control**: `↻ Regenerate` / `↻ Render`,
  `↻ Re-generate All Pages`, style-sheet `Generate`/`Regenerate`, or the Consolidate
  modal's Run button. Regenerate is one Gemini call per page; sheet regen is an OpenAI
  call and rewrites `story.json`. Free, safe interactions: navigating, expanding
  fieldsets, typing in fields, opening the `@`-mention picker, opening the Consolidate
  modal without running it, `✎ Re-composite (free)`.
- **Do not save** unless the change is a save-path change. Unlike the e2e suite — which
  serves a throwaway copy in `tempfile` (`tests/e2e/_support.py`, also `--port 0`, so it
  never collides or churns) — the command above serves the tracked fixture directly, so a
  save writes `tests/fixtures/pip-storm/story.json`. If the check needs a save, either
  `cp -R tests/fixtures/pip-storm /tmp/vis-check` and point `--story` there, or
  `git restore tests/fixtures` afterwards (same discipline as the EPUB churn note above).
- **Assert via `browser_snapshot` / `browser_find`, not JavaScript.** `browser_evaluate`
  and `browser_run_code_unsafe` are on the deny list in `scripts/bg-task-guardrails.json`,
  so they will simply fail for you.
- **No dialogs.** A `window.confirm` (the regenerate confirms, the long-mode fresh-bg
  prompt) blocks every subsequent MCP command. Avoid the controls that raise them.
- Pure Python change with no rendered-output effect → no visual check needed.
