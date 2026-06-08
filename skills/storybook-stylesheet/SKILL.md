---
name: storybook-stylesheet
description: >
  Stage 2 of 4 in the storybook pipeline — generate per-character style sheets.
  Use when an approved story.json already exists (from storybook-story) and the user
  wants to build, regenerate, or fix the character reference sheets — e.g. "make the
  style sheet", "regenerate the character sheets", "the characters look inconsistent /
  wrong", "redo the style sheet". Produces one style-sheet-{id}.png per character,
  which render_book.py selects per page for consistency. Requires an existing story.json
  with a 'cast' array. Costs one image API call per eligible cast entry. If no story.json
  exists yet, run storybook-story first.
metadata:
  requires:
    bins:
      - uv
    env:
      - STORYBOOK_SKILL_OPENAI_API_KEY
---

# Storybook — Stage 2: Character Style Sheets

## Preconditions

- `{out_dir}/story.json` exists and contains a non-empty `cast` array (authored in Stage 1 by **storybook-story**).
- `STORYBOOK_SKILL_OPENAI_API_KEY` (or `OPENAI_API_KEY`) is set; `uv` is installed. The script calls the OpenAI gpt-image-2 image API directly (no sibling skill needed).

If `story.json` is missing, run **storybook-story** first. If the `cast` array is missing, add it to `story.json` before running (sheets are built from that list, never guessed from prose).

---

## Run

```bash
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

This makes **one OpenAI gpt-image-2 image call per cast entry** to produce individual PNGs
(`style-sheet-{id}.png` — filename is the cast entry's `id` field, e.g. `style-sheet-pip.png`) — one sheet per entry, no combined cast sheet. **Every cast
entry gets a sheet**: characters (`kind` absent or `"character"`), objects (`kind:
"object"`), and locations (`kind: "location"`). Location sheets are generated from the
entry's downloaded real-place photo(s) in `ref_image` (Stage 1, PER-50) when present —
preserving the place's recognisable architecture, landmarks, and geography in the book's art
style — or from `appearance` alone for fictional places. Each sheet shows that cast member
alone: for characters, exactly four views — full-body front (анфас), full-body left profile,
full-body right profile, and a face close-up; for objects and locations, multiple
representative angles. Reference images are used as input: only that entry's own
`ref_image` (a single path or an array of paths — **every path must be a single-person
image** for characters; if the source photo was a group photo, use the per-person crop
produced in Stage 1, not the original), capped at 5 reference photos per call.
There is no shared global pool — refs are mapped per entry in Stage
1, so one character's photo never bleeds into another's sheet. The script writes each
entry's `style_sheet` path back into the `cast` array in `story.json`.

**Outfit lock.** Each sheet renders the character in exactly one canonical outfit —
taken from the character's `appearance` description, never from the reference photos
(which may show multiple outfits). If `appearance` names no clothing, the model
invents one simple outfit. This outfit propagates to every page: Stage 3 sends the
sheet as the clothing reference, ignoring photo outfit variation.

If the user supplied no character refs, the script still runs (prompt-only generation).

**The script is idempotent per character.** If `style-sheet-{id}.png` already exists
it is skipped. To force a regenerate for one character, use `--only ID` (PER-59) after
deleting that character's file:

```bash
rm style-sheet-pip.png   # replace 'pip' with the cast entry's id
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json --only pip
# or re-run the full sheet stage (regenerates only missing sheets):
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json
```

`--only ID` processes exactly one cast entry by exact id match (exit 2 if not found). This is also what the
visual editor's "↻ Regenerate sheet" button invokes (it deletes the PNG first, then calls
`--only ID` via the server).

---

## Approval gate

After generation, the script prints `MEDIA:` lines for every sheet. Show **all** sheets
to the user and ask them to confirm every character looks right **before** rendering pages.
Each sheet anchors that character on every page it appears — a wrong sheet poisons those
pages.

If a sheet is wrong:
1. Fix that character's entry in the `cast` array in `story.json` (sharpen
   `appearance`, set/update `ref_image`).
   - If the likeness anchored onto the wrong person or the crop clipped the subject,
     fix the crop first: re-run `crop_character.py` with an adjusted `--box` (it
     overwrites silently), then update `ref_image` to the corrected crop path.
2. Delete only that character's sheet file (e.g. `rm style-sheet-{id}.png`).
3. Re-run the command above.

Do not proceed to Stage 3 until the user approves all sheets.

---

## Handoff

Once approved:

```
All character sheets approved → ready for Stage 3.
Next: storybook-render generates each page, sending only the sheets for the cast members
      listed in that page's 'cast' field.
```
