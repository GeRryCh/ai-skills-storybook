---
paths:
  - "skills/**/*.py"
  - "tests/regen.sh"
---

# Script authoring rules

## Always `uv run`, never `python`

All scripts are PEP-723 inline-dependency scripts. Always invoke with `uv run` so deps (Pillow, google-genai, etc.) resolve automatically. Never `python script.py`.

## Preserve idempotency

Both paid scripts skip work whose output already exists. `make_style_sheet.py` skips any `style-sheet-{id}.png` that already exists (filename is the cast entry's `id` — PER-56); `render_book.py` skips any `page-NN.png` that exists. **Preserve this behaviour** — it makes partial-failure re-runs cheap. To force a regenerate, `rm` the target first.

## Keep duplicated helpers in sync

`build_style_block()`, `append_api_log()`, `require_cast_ids()`, `CAST_ID_MIGRATION_MESSAGE`,
`aspect_to_size()` (+ `_PORTRAIT_RATIOS` / `_LANDSCAPE_RATIOS`), and `get_api_key()` are
intentionally duplicated in both `render_book.py` and `make_style_sheet.py`. Any change to
any of these must be applied to both copies.

**PER-35 cost-accounting helpers** join this list: `PRICING`, `PRICING_AS_OF`,
`openai_call_cost()`, `append_cost_record()`, `read_cost_ledger()`,
`summarize_cost_records()`, and `format_cost_line()` are duplicated identically in both
files (`tests/unit/test_costs.py`'s `TestPricingTablesInSync` guards the `PRICING` copies
against drift). `gemini_call_cost()` lives only in `render_book.py` — `make_style_sheet.py`
never calls Gemini — but its `PRICING` copy still carries the Gemini model entries so the
table itself stays identical either way. `edit_story.py` carries a **read-only** third copy
of `read_cost_ledger()` (renamed `summarize_costs_for_status()` for the summarizer, since its
output shape is the `/api/status` JSON block, not a stdout line) — it never writes records,
so `append_cost_record()`/`PRICING` have no reason to exist there.

## No migration shim for legacy keys

Both paid scripts reject pre-PER-34 `story.json` files (legacy keys `characters`, `locations`, `pages[].characters`, `pages[].location`) with `exit 2` and a migration message. No shim — clean break.

## `style_guide` required

Both paid scripts refuse to run (`require_style_guide()`, exit 2) when `style_guide` is missing or empty from `story.json`.

## Explicit cast; no auto-extraction from prose

The `cast` array in `story.json` is authored explicitly and is the **only** source for style sheets. Do not reintroduce any regex/NLP that scrapes character names from `image_prompt` prose — this previously minted phantom characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit") and poisoned every page. See `get_cast()` docstring in `make_style_sheet.py`.

Each cast entry has a stable `id` (PER-56): lowercase slug, pattern `^[a-z][a-z0-9-]*$` (e.g. `pip`, `major-oak`). `pages[].cast` holds ids; `image_prompt` uses `<id>` placeholders. The id is internal structure — it never reaches the image model.

## `<id>` placeholders in `image_prompt` (PER-56 + PER-42)

`image_prompt` references cast members with `<id>` placeholders (e.g. `"<pip> runs through rain"`). `render_book.py`'s `resolve_cast_placeholders()` substitutes each `<id>` → cast entry's display `name` before every Gemini call, so the model always sees real names. This is the name-only rule (PER-42): placeholder → name is the substitution; repeating a cast member's `appearance` prose still weakens the reference-sheet signal and must be avoided. Pose, action, expression, and scene description stay in the prompt.

## Outfit lock

For `kind=character` entries, `appearance` must name exactly one outfit. The style-sheet prompt takes clothing from `appearance`, never from `ref_image` photos (which may show multiple outfits). Stage 3 takes clothing from the sheet, not the hero photo.

## Keep label wording in sync with `IMAGE_SYSTEM_PROMPT`

Each reference image in `run_nano_banana` is preceded by a `"Next image: {label}."` text part. The 6 label kinds are defined in `IMAGE_SYSTEM_PROMPT`. If you change a label string, update the system prompt's "kind" vocabulary to match (and vice versa):

- `"character style sheet for {name}"`
- `"real photograph of the character {name} (facial likeness reference)"`
- `"object reference sheet for {name}"`
- `"real photograph of the object {name} (appearance reference)"`
- `"location reference sheet for {name}"`
- `"book style reference — match its rendering technique, palette, and line treatment exactly; it depicts no specific scene"` (PER-82 — the one book-wide style frame; not a cast entry, so it has no `{name}`)

Locations have **no** render-side photo-fallback label (PER-84 — `missing_required_sheets` hard-requires a `style_sheet` for every location before render; unlike objects, there is no legitimate raw-photo path into a page render call). `make_style_sheet.py`'s Stage-2-only input-photo label (`"real photograph of the location {name} (setting reference)"`) is a separate, OpenAI-side vocabulary used only when *building* the sheet — it never reaches `render_book.py`.

The book style frame's label is also referenced by `_build_ref_manifest` (OpenAI fallback path) and `collect_input_images` (`render_book.py`) — keep all three in sync if it changes.

## `STYLE_ANCHOR` — preserve "of a character"

`STYLE_ANCHOR` in `render_book.py` contains the phrase `"of a character"` in its photo-matching sentence. This prevents the anchor from instructing the model to extract a face from a landmark photo on scenery-only pages. Do not remove it.

## Per-page location selection is mandatory

Never add a location ref to every page globally — it bleeds the place's environment into pages set elsewhere (PER-33 lesson). Only list a place name in `pages[].cast` for pages physically set there.
