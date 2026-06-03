---
name: storybook-story
description: >
  Stage 1 of 3 in the storybook pipeline — draft the manuscript.
  Use this skill whenever the user mentions: children's storybook, picture book,
  illustrated kids' book, bedtime story with pictures, story for my kid/child/toddler,
  "make a book about X", "write a storybook", "generate a kids book", or any request
  that combines a story idea with the word "illustrate", "pages", or "book".
  Even if the user only describes a character and says "make a story" — use this skill.
  This is the ENTRY POINT: it produces story.json (free, no API). The user edits and
  approves it, then storybook-stylesheet (Stage 2) and storybook-render (Stage 3) turn
  it into illustrated pages.
metadata:
  requires: {}
---

# Storybook — Stage 1: Manuscript

## Pipeline

This is the first of three skills. Together they make a fully illustrated picture book:

1. **storybook-story** (this skill, free) — draft `story.json`: per-page text + image prompts + explicit character cast (global + per-page). User edits and approves the text before any money is spent.
2. **storybook-stylesheet** (paid) — generate `style-sheet-{name}.png`: one reference image per character, the consistency anchors for every page.
3. **storybook-render** (paid) — generate each page illustration using only the character sheets for the characters listed on that page, then overlay text.

The three skills hand off a single file: `story.json` in the output directory.

Read `assets/STYLE_PRIMER.md` and `assets/story_schema.json` before writing the manifest. Mimic the structure in `assets/story_example.json`.

---

## Inputs

Gather these from the user (ask once if not provided):

| Input | Source | Default |
|-------|--------|---------|
| Story idea | free-text prompt | required |
| Character reference photos | absolute paths | none |
| Target age band | `3-5` or `5-8` | `3-5` |
| Number of pages (spreads) | N | `8` |
| Illustration style | free text | `"soft watercolor, gentle pastel palette, children's picture book"` |
| Output directory | path | current working directory |

---

## Steps

1. Read `assets/STYLE_PRIMER.md` (word counts, text placement, safe-zone rule).
2. Read `assets/story_schema.json` to understand required fields.
3. Read `assets/story_example.json` as a concrete pattern to follow.
4. Write `{out_dir}/story.json` following the schema exactly.

### Cast — the `characters` array (drives consistency)

You MUST author an explicit `characters` array. Stage 2's style sheet is built from
this list and nothing else — it shows exactly these characters and no others.

- ONE entry per **real** character. Do not add scene words, places, or pronouns.
- Each entry: `name` (exactly as written in `image_prompt`s), `appearance`
  (concrete: species, age, hair, clothing, colours, distinguishing features —
  more specific = more consistent), and optional `ref_image` (photo path(s) for
  that one character).
- If the user supplied character photos, map each photo to its character here by
  setting that character's `ref_image`. Use a single path for one photo, or an
  array of paths for several (e.g. multiple angles of the same person). This
  Stage-1 mapping is the ONLY source of reference photos — Stage 2 builds each
  style sheet from that character's `ref_image` and nothing else. There is no
  shared global pool, so one character's photo never bleeds into another's sheet.
  Capped at 3 photos per character (the image API input limit).

Never rely on auto-extraction: the cast is never guessed from prose.

### Page structure

- **Page 1**: cover. `text` = title only. `image_prompt` = full cover scene.
- **Pages 2 to N-1**: story body. Spread word counts guided by age (see STYLE_PRIMER).
- **Page N**: closing spread. One short sentence or just title/end.

### Per-page `characters` field (required)

Every page MUST have a `characters` array listing the names of all characters that appear
on that page. Names must match `characters[].name` exactly. Use `[]` for wordless or
character-free pages (title cards, scenery-only spreads).

**Order matters: put the page hero first.** The first name is treated as the hero, and Stage
3 additionally feeds that character's original reference photo into the render to lock its
facial likeness. List the protagonist (e.g. the child the book is about) first on every page
they appear; with a 3-image cap, a fourth reference (a third character's sheet) may be dropped
to make room for the hero's photo.

Example:
```json
{ "page_num": 3, "characters": ["Pip", "Mira"], "text": "...", ... }
```

This drives Stage 3: `render_book.py` sends only those characters' style sheets as
reference images when generating that page — the model never sees character sheets for
characters not on the page.

### image_prompt rules

Every `image_prompt` MUST:
- Name every character that appears on that page (use the exact names from the `characters` array).
- State the art style.
- NOT contain the actual story text — the script renders it (baked into the illustration in native mode, Pillow-overlaid in overlay mode).
- NOT contain text-position or safe-zone language — the script appends those transparently from `text_placement`.

---

## Handoff

### Optional top-level config

- **`text_mode`** — `"native"` (default) or `"overlay"`. Native bakes the story text directly into each illustration; overlay Pillow-composites it post-generation. Omit to use the default.
- **`fonts`** — book-wide role → font-family map (see STYLE_PRIMER typography rules). Omit to use bundled Andika/PatrickHand.
- **`resolution`** — `"1K"` / `"2K"` / `"4K"` image quality for page rendering. Default `"2K"`. Set this at the approval gate (it is a cost/quality decision for the user, not something to auto-pick). `"1K"` for fast/cheap drafts; `"4K"` for large-format print. The render CLI `--resolution` flag overrides if passed explicitly.

Do **not** auto-set `resolution` — leave it out unless the user asks for a specific quality.

Per-page `text_placement` defaults to `"floating"` (native mode). Override to `"top"` or `"bottom"` to pin text to a fixed band.

---

After writing `story.json`, tell the user:

```
story.json written to: {out_dir}/story.json

Please review and edit the text, image prompts, and character descriptions, then
tell me when to proceed. Open the file in any editor — change 'text' freely, keep
'page_num' intact, and make 'characters[].appearance' as specific as you can.

Next: storybook-stylesheet (Stage 2) builds the character style sheet.
```

Stop. Do not proceed until the user explicitly approves.
