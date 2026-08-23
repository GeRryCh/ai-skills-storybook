# Tests

## Two layers

| Layer | Runner | API key? | Browser? |
|---|---|---|---|
| **Unit** | `uv run tests/run_unit.py` | No | No |
| **E2E** | `uv run tests/run_e2e.py` | No | Yes (chromium) |

## Zero-API rule

No test may call Gemini or OpenAI. Paid-path entry guards
(`require_style_guide`, `require_cast_ids`) are tested via exit-code subprocess
calls that terminate before any API call.

## First-time browser setup (once)

```
uv run playwright install chromium
```

## Completion contract

On every task completion:

1. **Always** run the full unit suite — it must pass:
   ```
   uv run tests/run_unit.py
   ```

2. **Scoped e2e** — run only the module(s) covering the area you changed:
   - editor.html `@`-picker change → `uv run tests/run_e2e.py tests/e2e/test_mention_picker.py`
   - cast-derivation JS change → `uv run tests/run_e2e.py tests/e2e/test_cast_derivation.py`
   - save/API change → `uv run tests/run_e2e.py tests/e2e/test_save_roundtrip.py`
   - cost readout / `applyStatus()` / `/api/status`'s `costs` block → `uv run tests/run_e2e.py tests/e2e/test_cost_readout.py`
   - pure Python function change → no e2e needed

   Do **not** run the full e2e suite on every task — it is browser-heavy.

## Subset runner

Both runners accept optional file paths as arguments:

```
uv run tests/run_unit.py tests/unit/test_select_refs.py
uv run tests/run_e2e.py  tests/e2e/test_mention_picker.py
```

## Layout

```
tests/
  run_unit.py           # runner: all unit / subset
  run_e2e.py            # runner: all e2e / subset; checks chromium
  unit/
    _support.py         # sys.path setup + fixture helpers
    test_select_refs.py
    test_resolve_placeholders.py
    test_collect_input_images.py
    test_validate_story.py
    test_cli_exit_codes.py
    test_costs.py        # PER-35: pricing math + ledger round-trip (both scripts)
    test_book_premise.py # PER-66: premise injected into every page render prompt
    test_openai_fallback.py # PER-67: PROHIBITED_CONTENT auto-retry on gpt-image-2
    test_lane_caps_in_sync.py # PER-97: guards the triplicated lane-cap constants
    test_text_layout.py  # PER-104: one box model — fixed font, content-derived panel
  e2e/
    _support.py         # EditorServer (start/stop server + temp fixture copy)
    test_mention_picker.py
    test_cast_derivation.py
    test_save_roundtrip.py
    test_cost_readout.py # PER-35: top-bar cost readout renders from /api/status
  fixtures/             # committed sample books (read-only for tests)
    pip-storm/          # overlay + native pages, reference image, style sheet
    pip-storm-long/     # long-mode: full-bleed art, shared text-page background
    gazelle-valley/     # location cast entry with real-place ref photos
    crowded-cast/       # PER-97: 6 characters + 1 object, no rendered artifacts —
                         # drives the character-lane hard-cap picker/save tests
  gen_ref.py            # paid artifact-regeneration script (not a test)
  regen.sh              # paid regen script (not a test)
```
