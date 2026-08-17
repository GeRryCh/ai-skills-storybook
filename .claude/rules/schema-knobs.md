---
paths:
  - "skills/storybook-story/assets/story_schema.json"
  - "skills/storybook-story/SKILL.md"
---

# Interactive interview (x-interview annotations, PER-27)

The storybook-story skill guides users through a short schema-driven interview before writing `story.json`. The mechanism is annotation-based — the skill reads `story_schema.json` at runtime and never needs editing when a knob is added.

## Contract

Add `"x-interview": {"priority": "core"|"advanced", "ask": "<one-line hint>"}` inside top-level property definitions in `skills/storybook-story/assets/story_schema.json`:

- `"priority": "core"` — asked up front in the interview batch; enum values become options, default first (schema `"default"` if present, else first enum value).
- `"priority": "advanced"` — never asked; shown once in the pre-write configuration summary (Gate 1) as an overridable default.
- No annotation — never asked, never in the summary (by design).

## The rule

**A new book-level knob = schema property + `x-interview` annotation (or a deliberate decision to omit one) — never a hard-coded question in `SKILL.md`.** `SKILL.md` holds only the generic algorithm, which is stable across schema changes.

## Required-field warning

Required fields authored by prose logic (`title`, `style_guide`, `cast`, `pages`) are intentionally un-annotated — the agent writes them from the story content, not a question. Any **new required scalar knob** MUST get a `"core"` annotation or explicit authoring logic; otherwise the interview never produces it and `story.json` fails validation. `SKILL.md`'s required-field guard cross-checks `schema.required` at runtime and surfaces gaps — it does not silently skip them.

## Script safety

`edit_story.py` and `editor.html` read only `enum` / `default` / `required` / property key names from the schema — custom `x-*` keys inside property definitions are invisible to both (verified: `_schema_enums` lines 76-91, `_known_keys` lines 94-102, `editor.html` `enumFor`/`defaultFor`). Never make scripts depend on `x-interview`.

## Omission rule

Accepting a default at Gate 1 means the optional key is **omitted** from `story.json` (preserves the editor's round-trip contract where "optional fields never get materialised when absent"). `saved_formats: []` (records "no book files" preference — hint for Stage 4) is NOT the same as omitted (hint = all formats). No script reads `saved_formats`; storybook-consolidate uses it as the default answer when asking which formats to export, and the interactive choice there always wins. `aspect_ratio` omitted = built-in default `3:2` used by both paid scripts (PER-88; a schema `"default"` alone does nothing — `render_book.py`/`make_style_sheet.py` resolve it via `args.aspect_ratio or story.get("aspect_ratio") or DEFAULT_ASPECT_RATIO`, so the fallback lives in script code, not just the schema). Write the literal value `"auto"` to opt back into the old per-call model-chosen framing.

## `ask` field guidance

Keep `ask` to one line. The property `"description"` remains the authoritative detail and is the source of option glosses shown during the interview.
