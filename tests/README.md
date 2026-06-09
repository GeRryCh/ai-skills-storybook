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
  e2e/
    _support.py         # EditorServer (start/stop server + temp fixture copy)
    test_mention_picker.py
    test_cast_derivation.py
    test_save_roundtrip.py
  fixtures/             # committed sample books (read-only for tests)
  gen_ref.py            # paid artifact-regeneration script (not a test)
  regen.sh              # paid regen script (not a test)
```
