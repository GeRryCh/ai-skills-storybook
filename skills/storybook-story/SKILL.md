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

1. **storybook-story** (this skill, free) — draft `story.json`: per-page text + image prompts + explicit character cast. User edits and approves the text before any money is spent.
2. **storybook-stylesheet** (paid) — generate `style-sheet.png`: one reference image of the whole cast, the consistency anchor for every page.
3. **storybook-render** (paid) — generate each page illustration and overlay the text.

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
  more specific = more consistent), and optional `ref_image` (absolute path to
  a photo for that one character).
- If the user supplied a character photo, set it as that character's `ref_image`
  AND keep it in `character_refs`.

Never rely on auto-extraction: the cast is never guessed from prose.

### Page structure

- **Page 1**: cover. `text` = title only. `image_prompt` = full cover scene.
- **Pages 2 to N-1**: story body. Spread word counts guided by age (see STYLE_PRIMER).
- **Page N**: closing spread. One short sentence or just title/end.

### image_prompt rules

Every `image_prompt` MUST:
- Name every character that appears on that page (use the exact names from the `characters` array).
- State the art style.
- Include the text-safe-zone directive (the render script appends it, but write it anyway for clarity):
  > "Leave the [top|bottom] quarter of the image as a soft, low-detail, lightly-toned area suitable for overlaying text."
- NOT contain the actual story text — that is overlaid by Pillow in Stage 3.

---

## Handoff

### Optional top-level config

- **`fonts`** — book-wide role → font-family map (see STYLE_PRIMER typography rules). Omit to use bundled Andika/PatrickHand.
- **`resolution`** — `"1K"` / `"2K"` / `"4K"` image quality for page rendering. Default `"2K"`. Set this at the approval gate (it is a cost/quality decision for the user, not something to auto-pick). `"1K"` for fast/cheap drafts; `"4K"` for large-format print. The render CLI `--resolution` flag overrides if passed explicitly.

Do **not** auto-set `resolution` — leave it out unless the user asks for a specific quality.

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
