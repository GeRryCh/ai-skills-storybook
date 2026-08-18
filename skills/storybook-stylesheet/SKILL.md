---
name: storybook-stylesheet
description: >
  Stage 2 of 4 in the storybook pipeline — generate per-character style sheets.
  Use when an approved story.json already exists (from storybook-story) and the user
  wants to build, regenerate, or fix the character reference sheets — e.g. "make the
  style sheet", "regenerate the character sheets", "the characters look inconsistent /
  wrong", "redo the style sheet". Produces one style-sheet-{id}.png per character,
  which render_book.py selects per page for consistency, plus one book-wide
  style-frame.png (PER-82) sent as a reference on every page render. Requires an
  existing story.json with a 'cast' array. Costs one image API call per eligible cast
  entry, plus one for the book-wide style frame. If no story.json exists yet, run
  storybook-story first.
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
`ref_image` (a single path or an array of paths — multiple angles of the same person are
accepted), capped at 5 reference photos per call.
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

**Sheet rendering style (identity vs. book style).** A reference sheet is an **identity
artifact**, not a style artifact — the book's look is applied downstream at page-render
time by the style block plus `style-frame.png`, which is strong enough on its own.
Rendering the sheet itself in a hard/graphic book style measurably costs identity:
a flattened character sheet lost face structure entirely, and palette-dominant rendering
recolours identity-carrying attributes (hair, skin) toward the book palette before they
ever reach a page.

So by default **character** sheets render in a built-in identity-safe watercolor reference
style (natural hair/skin colour, neutral light), while **object and location** sheets keep
the book's own style — their identity is shape, which survives styling. The style frame
always uses the book style.

Override with the top-level `sheet_style` field in `story.json`, or `--sheet-style STYLE`
for one run (the flag wins). When set, it applies to **every** cast kind. The literal value
`book` renders sheets in the book's own `style_guide`:

```bash
# draw the sheets in the book's own style (pre-change behaviour)
uv run {skillDir}/scripts/make_style_sheet.py --story {out_dir}/story.json --sheet-style book
```

Never encode a sheet's rendering style by writing style instructions into
`cast[].appearance` — that field describes *who the character is*, and style prose there
competes with the photo-likeness instructions in the same prompt.

**Other flags:** `--out-dir DIR` — output directory (default: same dir as `--story`).
`--aspect-ratio RATIO` — override `story.json`'s `aspect_ratio` field for this run (choices:
`1:1` `2:3` `3:2` `3:4` `4:3` `4:5` `5:4` `9:16` `16:9` `21:9`; default: unset — model
chooses). `--resolution 1K|2K|4K` is accepted for symmetry with `render_book.py` but is
Gemini-era and has no effect here — `gpt-image-2`'s call size comes from
`aspect_to_size(aspect_ratio)` instead (logged only).

**Book-wide style frame (PER-82, "Lever B").** After the cast loop, a **full run** (no
`--only`) also generates ONE `style-frame.png` per book — an abstract style board (palette
swatches, a line/texture sample, a lighting study; no characters, no places, no scenery) —
and writes its path into the top-level `style_frame` field. `render_book.py` sends it as
the lowest-priority reference on every page render to anchor the look of everything that
isn't cast (backgrounds, crowds, lighting, props). Idempotent (skipped if `style-frame.png`
already exists). **Skipped under `--only ID`**: `--only`'s contract is exactly one
top-level diff (that entry's `style_sheet`), which the visual editor's per-cast regenerate
flow depends on to resync safely without a full reload — generating the frame there too
would add an unsynced second field write. To force a regenerate: `rm style-frame.png` and
re-run a full (no `--only`) invocation.

---

## Approval gate

After generation, the script prints `MEDIA:` lines for every sheet **and for the style
frame**, followed by a cost summary (PER-35) when it made at least one paid call:
`Cost this run: $X.XX (N calls)` and `Book total: $Y.YY (M calls) [out_dir/costs.jsonl]`.
Show **all** the `MEDIA:` images to the user and ask them to confirm every character looks
right **before** rendering pages, and report the cost summary alongside them. Each character
sheet anchors that character on every page it appears — a wrong sheet poisons those pages.
The style frame is sent on *every* page, so a bad frame is strictly worse: it poisons the
whole book, not just the pages one character appears on.

**Check age and body proportions specifically** for every character sheet — compare
against the age stated (or implied) in that cast entry's `appearance`, not just whether
the face looks right. Reference photos spanning a range of ages and the soft-illustration
idiom both bias young by default (PER-88); a too-young sheet is the failure hardest to
un-see after 8 pages render and the cheapest to catch right here.

If a sheet is wrong:
1. Fix that character's entry in the `cast` array in `story.json` (sharpen
   `appearance`, set/update `ref_image`).
2. Delete only that character's sheet file (e.g. `rm style-sheet-{id}.png`).
3. Re-run the command above.

If the style frame is wrong (e.g. it depicts a scene or leaks narrative content), fix
`style_guide` if needed, `rm style-frame.png`, and re-run.

Do not proceed to Stage 3 until the user approves all sheets and the style frame.

---

## Handoff

Once approved:

```
All character sheets approved → ready for Stage 3.
Next: storybook-render generates each page, sending only the sheets for the cast members
      listed in that page's 'cast' field.
```
