---
name: storybook-stylesheet
description: >
  Stage 2 of 3 in the storybook pipeline — generate per-character style sheets.
  Use when an approved story.json already exists (from storybook-story) and the user
  wants to build, regenerate, or fix the character reference sheets — e.g. "make the
  style sheet", "regenerate the character sheets", "the characters look inconsistent /
  wrong", "redo the style sheet". Produces one style-sheet-{name}.png per character,
  which render_book.py selects per page for consistency. Requires an existing story.json
  with a 'characters' array. Costs one image API call per character. If no story.json
  exists yet, run storybook-story first.
metadata:
  requires:
    bins:
      - uv
    env:
      - OPENROUTER_API_KEY
---

# Storybook — Stage 2: Character Style Sheets

## Preconditions

- `{out_dir}/story.json` exists and contains a non-empty `characters` array (authored in Stage 1 by **storybook-story**).
- `OPENROUTER_API_KEY` is set; `uv` is installed. The script calls the OpenRouter image API directly (no sibling skill needed).

If `story.json` is missing, run **storybook-story** first. If the `characters` array is missing, add it to `story.json` before running (sheets are built from that list, never guessed from prose).

---

## Run

```bash
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

This makes **one OpenRouter image call per character** to produce individual PNGs
(`style-sheet-{name}.png`) — one sheet per character, no combined cast sheet. Each sheet
shows that character alone at multiple angles. Reference images are used as input: only
that character's own `ref_image` (a single path or an array of paths), capped at 3 (the
image API input limit). There is no shared global pool — refs are mapped per character in
Stage 1, so one character's photo never bleeds into another's sheet. The
script writes each character's `style_sheet` path back into the `characters` array in
`story.json`.

If the user supplied no character refs, the script still runs (prompt-only generation).

**The script is idempotent per character.** If `style-sheet-{name}.png` already exists
it is skipped. To force a regenerate for one character, delete that character's file
and re-run:

```bash
rm style-sheet-pip.png   # replace 'pip' with the character's slug
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

---

## Approval gate

After generation, the script prints `MEDIA:` lines for every sheet. Show **all** sheets
to the user and ask them to confirm every character looks right **before** rendering pages.
Each sheet anchors that character on every page it appears — a wrong sheet poisons those
pages.

If a sheet is wrong:
1. Fix that character's entry in the `characters` array in `story.json` (sharpen
   `appearance`, set/update `ref_image`).
2. Delete only that character's sheet file (e.g. `rm style-sheet-pip.png`).
3. Re-run the command above.

Do not proceed to Stage 3 until the user approves all sheets.

---

## Handoff

Once approved:

```
All character sheets approved → ready for Stage 3.
Next: storybook-render generates each page, sending only the sheets for the characters
      listed in that page's 'characters' field.
```
