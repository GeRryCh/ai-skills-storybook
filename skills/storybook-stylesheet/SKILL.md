---
name: storybook-stylesheet
description: >
  Stage 2 of 3 in the storybook pipeline — generate the character style sheet.
  Use when an approved story.json already exists (from storybook-story) and the user
  wants to build, regenerate, or fix the cast reference sheet — e.g. "make the style
  sheet", "regenerate the character sheet", "the characters look inconsistent / wrong",
  "redo the style sheet". Produces style-sheet.png, the single reference image that
  anchors character consistency across every page. Requires an existing story.json with
  a 'characters' array. Costs one image API call. If no story.json exists yet, run
  storybook-story first.
metadata:
  requires:
    bins:
      - uv
    env:
      - OPENROUTER_API_KEY
---

# Storybook — Stage 2: Character Style Sheet

## Preconditions

- `{out_dir}/story.json` exists and contains a non-empty `characters` array (authored in Stage 1 by **storybook-story**).
- `OPENROUTER_API_KEY` is set; `uv` is installed. The script calls the OpenRouter image API directly (no sibling skill needed).

If `story.json` is missing, run **storybook-story** first. If the `characters` array is missing, add it to `story.json` before running (the sheet is built from that list, never guessed from prose).

---

## Run

```bash
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

This makes one OpenRouter image call to produce `style-sheet.png` showing **exactly** the characters in `story.json`'s `characters` array — no auto-guessing, no phantom characters. Reference images are used as input: per-character `ref_image` first, then the global `character_refs` pool, capped at 3. The script writes `style_sheet_path` back into `story.json`.

If the user supplied no character refs, the script still runs (prompt-only generation).

**The script skips generation if `style-sheet.png` already exists.** To force a fresh sheet (e.g. after editing `characters`), delete `style-sheet.png` first.

---

## Approval gate

After generation, show the sheet to the user (`MEDIA:` the path) and ask them to confirm every character looks right **before** rendering pages. The sheet anchors every page — a wrong sheet poisons the whole book.

If it is wrong:
1. Fix the `characters` array in `story.json` (sharpen `appearance`, add/remove a character, set a `ref_image`).
2. `rm style-sheet.png`
3. Re-run the command above.

Do not proceed to Stage 3 until the user approves the sheet.

---

## Handoff

Once approved:

```
style-sheet.png approved → ready for Stage 3.
Next: storybook-render generates each page using this sheet as the consistency anchor.
```
