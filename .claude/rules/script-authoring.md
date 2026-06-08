---
paths:
  - "skills/**/*.py"
  - "tests/regen.sh"
---

# Script authoring rules

## Always `uv run`, never `python`

All scripts are PEP-723 inline-dependency scripts. Always invoke with `uv run` so deps (Pillow, google-genai, etc.) resolve automatically. Never `python script.py`.

## Preserve idempotency

Both paid scripts skip work whose output already exists. `make_style_sheet.py` skips any `style-sheet-{name}.png` that already exists; `render_book.py` skips any `page-NN.png` that exists. **Preserve this behaviour** — it makes partial-failure re-runs cheap. To force a regenerate, `rm` the target first.

## Keep duplicated helpers in sync

`build_style_block()` and `append_api_log()` are intentionally duplicated in both `render_book.py` and `make_style_sheet.py`. Any change to either must be applied to both copies.

## No migration shim for legacy keys

Both paid scripts reject pre-PER-34 `story.json` files (legacy keys `characters`, `locations`, `pages[].characters`, `pages[].location`) with `exit 2` and a migration message. No shim — clean break.

## `style_guide` required

Both paid scripts refuse to run (`require_style_guide()`, exit 2) when `style_guide` is missing or empty from `story.json`.

## Explicit cast; no auto-extraction from prose

The `cast` array in `story.json` is authored explicitly and is the **only** source for style sheets. Do not reintroduce any regex/NLP that scrapes character names from `image_prompt` prose — this previously minted phantom characters (a fish "Deep" from "deep twilight sky", a girl "She" from "She holds a rabbit") and poisoned every page. See `get_cast()` docstring in `make_style_sheet.py`.

## Name-only refs in `image_prompt`

`image_prompt` must reference cast members **by name only** (PER-42). Repeating a cast member's `appearance` prose makes the render model deviate from the reference sheet. Pose, action, expression, and scene description stay in the prompt — only inherent appearance is omitted.

## Outfit lock

For `kind=character` entries, `appearance` must name exactly one outfit. The style-sheet prompt takes clothing from `appearance`, never from `ref_image` photos (which may show multiple outfits). Stage 3 takes clothing from the sheet, not the hero photo.

## Keep label wording in sync with `IMAGE_SYSTEM_PROMPT`

Each reference image in `run_nano_banana` is preceded by a `"Next image: {label}."` text part. The 6 label kinds are defined in `IMAGE_SYSTEM_PROMPT`. If you change a label string, update the system prompt's "kind" vocabulary to match (and vice versa):

- `"character style sheet for {name}"`
- `"real photograph of the character {name} (facial likeness reference)"`
- `"object reference sheet for {name}"`
- `"real photograph of the object {name} (appearance reference)"`
- `"location reference sheet for {name}"`
- `"real photograph of the location {name} (setting reference)"`

## `STYLE_ANCHOR` — preserve "of a character"

`STYLE_ANCHOR` in `render_book.py` contains the phrase `"of a character"` in its photo-matching sentence. This prevents the anchor from instructing the model to extract a face from a landmark photo on scenery-only pages. Do not remove it.

## Per-page location selection is mandatory

Never add a location ref to every page globally — it bleeds the place's environment into pages set elsewhere (PER-33 lesson). Only list a place name in `pages[].cast` for pages physically set there.
