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
| Character reference photos | absolute paths (photos may contain multiple people — see Source-photo analysis below) | none |
| Target age band | `3-5` or `5-8` | `3-5` |
| Number of pages (spreads) | N | `8` |
| Illustration style | free text | `"soft watercolor, gentle pastel palette, children's picture book"` |
| Output directory | path | current working directory |

---

## Steps

1. Read `assets/STYLE_PRIMER.md` (word counts, text placement, safe-zone rule).
2. Read `assets/story_schema.json` to understand required fields.
3. Read `assets/story_example.json` as a concrete pattern to follow.
4. **Analyze any supplied photos** (see Source-photo analysis below) before authoring the cast.
5. **Detect real named places and gather location photos** (see Locations section below) — optional, skip gracefully if Perplexity MCP is unavailable.
6. Write `{out_dir}/story.json` following the schema exactly.
7. **Validate `story.json` against the schema** — run the validator and fix any errors before continuing:

```bash
uv run {skillDir}/scripts/validate_story.py --story {out_dir}/story.json
```

Exits 0 when clean. On errors (exit 2), fix `story.json` and re-run until clean. Surface any warnings to the user; they never block but may point to missing files or duplicate names worth reviewing.

### Source-photo analysis (BEFORE authoring the cast)

When the user supplies reference photos, **view every photo with the Read tool before
writing the cast** — never map a photo to a character without seeing it first.

**Single-person photo:** map the photo directly to that character's `ref_image` (unchanged
behavior). No crop needed — add the path as-is.

**Multi-person photo** (two or more distinct people in frame): do NOT point any character's
`ref_image` at the original group photo. Instead:

1. **Describe** each distinct person visible: approximate age, hair colour/length,
   clothing, position in frame (e.g. "the girl on the left in the red jacket"), and any
   distinguishing features. Be concrete — this description drives the crop box.
2. **Ask the user** which people should become characters and what to name each one.
   Never auto-promote everyone; the user may only want one or two of the people shown.
3. **For each chosen person**, produce a single-person crop:
   - Estimate a **generous** fractional bounding box — full person head-to-toe with
     comfortable margin so hair and limbs are never clipped. When in doubt, go bigger;
     background context is harmless, a clipped head is not.
   - Run the crop script (free, Pillow only, no API call; requires `uv` on PATH — same
     dependency as Stages 2–3):
     ```
     uv run {skillDir}/scripts/crop_character.py \
       --image /path/to/source.jpg \
       --box L,T,R,B \
       --out {out_dir}/ref-{char-slug}.png
     ```
     `L,T,R,B` are fractions of image width/height in [0, 1] — **not pixel coordinates**.
     Example: `--box 0.05,0.08,0.45,0.95`
   - **View the crop with the Read tool** to verify: right person captured, head/hair not
     clipped, no other person dominating the frame. If anything is off, adjust the box
     and re-run — the script silently overwrites the output file, so iteration is free.
   - Set that character's `ref_image` to the crop path.

**Naming convention for crop files:** `ref-{char-slug}.png` in the output directory, where
`slug` is the character name lowercased with non-alphanumerics replaced by hyphens (e.g.
`ref-mia.png`, `ref-little-bear.png`). If a character has an additional solo photo, list
both: `["ref-mia.png", "/photos/mia-solo.jpg"]`. For a second crop of the same character
use `-2`/`-3` suffixes (`ref-mia-2.png`). Crop filenames must be unique within the book.

**The original multi-person photo must never appear in any character's `ref_image`.**

### Cast — the `cast` array (drives consistency)

You MUST author an explicit `cast` array. Stage 2's style sheet is built from
this list and nothing else — it shows exactly these cast members and no others.

Each entry carries an optional `"kind"` field: `"character"` (default when absent),
`"object"` (a significant prop or vehicle), or `"location"` (a named real place).

- ONE entry per **real** character, object, or named place. Do not add scene words or pronouns.
- Each entry: `name` (exactly as written in `image_prompt`s), `appearance`
  (concrete: species, age, hair, **one specific outfit**, colours, distinguishing
  features — more specific = more consistent), and optional `ref_image` (photo
  path(s) for that one character).
- **Include exactly one outfit in `appearance`.** The outfit written there is locked
  into the style sheet at Stage 2 and the character wears it unchanged on every page.
  If the user did not specify clothing, invent one simple distinctive outfit and name
  it. Photos anchor face and hair likeness only — Stage 2 ignores clothing in photos.
- If the user supplied character photos, map each to its character's `ref_image`
  following the Source-photo analysis step above. **Every path in `ref_image` must
  be a single-person image** — a solo photo or a Stage-1 crop from
  `scripts/crop_character.py`. For multi-person source photos, use only the
  per-person crop, never the group original. Use a single path for one photo, or
  an array of paths for several images of the same person (multiple angles or a
  crop plus a solo photo). This Stage-1 mapping is the ONLY source of reference
  photos — Stage 2 builds each style sheet from that character's `ref_image` and
  nothing else. There is no shared global pool, so one character's photo never
  bleeds into another's sheet. Capped at 5 photos per character (Gemini 3 Pro
  Image character-lane limit).

Never rely on auto-extraction: the cast is never guessed from prose.

### Locations — real-place photo references (optional, Perplexity MCP)

When the story mentions a **specific named real place** — a landmark, city, or recognizable
building (e.g. "the Eiffel Tower", "Sherwood Forest's Major Oak", "the Brandenburg Gate")
— you can find a real photo of that place and use it as a visual reference during rendering.
This makes the rendered background resemble the actual location.

**Never use this for generic settings** ("a forest", "the beach", "grandma's kitchen").
Those are described in prose only. When it is unclear whether a place is real and named, ask
the user along with the other Stage-1 questions.

#### Availability check (skip gracefully — never fail Stage 1)

This step needs the Perplexity MCP tools (`perplexity_search` etc.). If they are not
available in this session, skip locations entirely and tell the user once:

> "Location photo references skipped — Perplexity MCP not configured; the book renders
> fine without them, places will be drawn from the prompt text alone."

Never block or fail Stage 1 because of a missing MCP.

#### Searching for a photo (per place)

Call `perplexity_search` with a query like `"{place name}" photo site:commons.wikimedia.org`.

Prefer **freely-licensed** sources (Wikimedia Commons CC0/PD/CC-BY, or Unsplash public
domain). Pick a result that links to a Wikimedia Commons file page and note its `File:`
title (e.g. `File:Tour_Eiffel_Wikimedia_Commons.jpg`).

**Avoid** using `perplexity_ask` to obtain direct image URLs — it has been observed to
return hallucinated URLs that return 404. If you do use it as a last resort, the download
script still verifies the URL; a 404 produces a clear exit-1 error so you can try again.

#### Building the direct-download URL

Use the **Special:FilePath** redirect for a deterministic, no-parsing URL:

```
https://commons.wikimedia.org/wiki/Special:FilePath/{File-title-without-File:-prefix}?width=1600
```

URL-encode spaces as `_` (Commons convention). Example:

```
https://commons.wikimedia.org/wiki/Special:FilePath/Tour_Eiffel_Wikimedia_Commons.jpg?width=1600
```

Optionally query the Commons API for the canonical URL and license info:
```
https://commons.wikimedia.org/w/api.php?action=query&titles=File:Tour_Eiffel_Wikimedia_Commons.jpg&prop=imageinfo&iiprop=url|extmetadata&format=json
```

#### Downloading and validating

```bash
uv run {skillDir}/scripts/fetch_location.py \
  --url "https://commons.wikimedia.org/wiki/Special:FilePath/{File-title}?width=1600" \
  --out {out_dir}/loc-{slug}.jpg
```

**Naming convention:** `loc-{slug}.jpg`, where slug is the place name lowercased with
non-alphanumerics replaced by hyphens — same rule as `ref-{char-slug}.png` for crops.
Example: `loc-eiffel-tower.jpg`, `loc-major-oak.jpg`.

On a non-zero exit code, read the error message and try the next candidate image. The
script silently overwrites the output file — iteration is free.

After a successful download, **view the file with the Read tool** and confirm:
- The image shows the **right place**, recognizably.
- It is **well-framed** (no extreme close-ups or partial views).
- It contains **no prominent people** — a person in the frame risks being read as a
  character by the render model. If present, pick another image.

If the image is wrong, pick another candidate URL and re-run.

#### Writing to story.json

Add an entry to the top-level `cast` array with `"kind": "location"`:

```json
{
  "name": "Eiffel Tower",
  "kind": "location",
  "appearance": "iron lattice tower on the Champ de Mars, Paris",
  "ref_image": "loc-eiffel-tower.jpg",
  "source_url": "https://commons.wikimedia.org/wiki/File:Tour_Eiffel_Wikimedia_Commons.jpg"
}
```

Then add `"Eiffel Tower"` to the `pages[].cast` array of every page **physically set at
that place only** — never book-wide. This per-page selection is mandatory: without it,
the place's environment would bleed into every page of the book (see
docs/future-explorations.md, PER-33).

The page's `image_prompt` must also **name the place in prose** (e.g. "...under the
Eiffel Tower...") so the render model knows the setting even in the text part of the prompt.

#### Cap note

The location photo is the **lowest-priority** reference image within Stage 3's 4-image cap:

> hero sheet → hero photo → remaining character sheets → object refs → location photo

On pages with 3 or more cast members, the location photo may be dropped from the cap (it
will be logged — never silently dropped). On scenery-only pages (`cast: []`) the
location photo is the sole reference image.

---

### Style guide — the `style_guide` object (REQUIRED)

You MUST author a top-level `style_guide` object — both paid scripts refuse to run
without it. Each page is rendered in a separate stateless API call; the only book-wide
consistency mechanism is injecting the exact same style descriptor into every call. Both
scripts assemble `style_guide` into one byte-identical block (fixed field order:
medium → palette → line → lighting → mood) and inject it verbatim into every Gemini call.

- Fields (all strings unless noted): `medium`, `palette` (array, 3–5 swatches — include
  hex codes for precision, e.g. `"warm cream #F5E9D4"`), `line`, `lighting`, `mood`.
- At least one field must be non-empty; fill all five for the strongest lock.
- Keep `style` as the short human-readable label (used in prose/image_prompts); the
  `style_guide` is what the renderer actually anchors on.
- See `assets/STYLE_PRIMER.md` for the full field reference and a worked example.

### Page structure

- **Page 1**: cover. `text` = title only. `image_prompt` = full cover scene.
- **Pages 2 to N-1**: story body. Spread word counts guided by age (see STYLE_PRIMER).
- **Page N**: closing spread. One short sentence or just title/end.

### Per-page `cast` field (required)

Every page MUST have a `cast` array listing the names of all cast members that appear
on that page. Names must match `cast[].name` exactly. Use `[]` for wordless or
character-free pages (title cards, scenery-only spreads).

**Order matters: put the page hero first.** The first character-kind entry (kind=`"character"` or absent) is treated as the hero, and Stage
3 additionally feeds that character's first `ref_image` (a solo photo or a Stage-1 crop)
into the render to lock its facial likeness. List the protagonist (e.g. the child the book is
about) first on every page they appear; with a 4-image cap (flash model), a fifth reference
may be dropped to make room for the hero's photo. Three-character pages can now carry hero
sheet + hero photo + both supporting sheets without dropping anything.

Example:
```json
{ "page_num": 3, "cast": ["Pip", "Mira"], "text": "...", ... }
```

This drives Stage 3: `render_book.py` sends only those cast members' style sheets as
reference images when generating that page — the model never sees character sheets for
cast members not on the page.

### image_prompt rules

Every `image_prompt` MUST:
- Name every character that appears on that page (use the exact names from the `cast` array).
- State the art style.
- NOT contain the actual story text — the script renders it (baked into the illustration in native mode, Pillow-overlaid in overlay mode).
- NOT contain text-position or safe-zone language — the script appends those transparently from `text_placement`.

---

## Handoff

### Optional top-level config

- **`text_mode`** — `"native"` (default), `"overlay"`, or `"long"`. Native bakes the story text directly into each illustration; overlay Pillow-composites it post-generation. **Long mode** splits each body page into two physical pages: a full-bleed illustration with no text, followed by a text-only page with the story text on a centered panel over ONE shared model-generated background reused book-wide (designed with a reserved central text area; +1 paid call total for the book). Choose `"long"` for text-heavy stories, older readers (age 5–8), or when the story carries more than ~80 words per spread. Cover (page 1) stays a single combined page in long mode. Pages with empty `text` emit an art-only page. Omit to use the default (`"native"`).
- **`saved_formats`** — array of `"pdf"` and/or `"epub"` specifying which book file(s) the render stage assembles after a full render. Omit to produce all formats (default). Set `[]` to skip assembly. The render CLI `--saved-formats` flag overrides if passed explicitly.
- **`language`** — BCP-47 language tag for the book text (e.g. `"en"`, `"de"`, `"en-GB"`). Used as `dc:language` metadata in the EPUB. Omit for the `"en"` default; set only when the story is non-English.
- **`fonts`** — book-wide role → font-family map (see STYLE_PRIMER typography rules). Omit to use bundled Andika/PatrickHand.
- **`resolution`** — `"1K"` / `"2K"` / `"4K"` image quality for page rendering. Default `"2K"`. Set this at the approval gate (it is a cost/quality decision for the user, not something to auto-pick). `"1K"` for fast/cheap drafts; `"4K"` for large-format print. The render CLI `--resolution` flag overrides if passed explicitly.
- **`aspect_ratio`** — book-wide framing for both style sheets and page renders. Choices: `"1:1"` `"2:3"` `"3:2"` `"3:4"` `"4:3"` `"4:5"` `"5:4"` `"9:16"` `"16:9"` `"21:9"`. When omitted the model picks framing on each call (non-deterministic). Set only when the user wants consistent, fixed framing across the whole book. The render/stylesheet CLI `--aspect-ratio` flag overrides if passed explicitly.

Do **not** auto-set `resolution` — leave it out unless the user asks for a specific quality.

Do **not** auto-set `aspect_ratio` — leave it out unless the user wants fixed framing. Omitting it preserves today's behavior (model chooses per call) and avoids silently changing framing on existing books.

Per-page `text_placement` defaults to `"floating"` (native mode). Override to `"top"` or `"bottom"` to pin text to a fixed band.

**Long mode text-page backgrounds:** an optional top-level `text_background_prompt` customizes the shared book-wide background (omit for a generic style-matched one). An optional per-page `text_background_prompt` gives that page its OWN dedicated background instead of the shared one, via one extra paid Gemini call (the book style block is injected; no character references are sent; a low-detail central area is reserved for text).

---

After writing `story.json` and confirming `validate_story.py` exits 0, tell the user (include any warnings from the validator so they can address them):

```
story.json written to: {out_dir}/story.json

Please review and edit the text, image prompts, and character descriptions, then
tell me when to proceed. Open the file in any editor — change 'text' freely, keep
'page_num' intact, and make 'cast[].appearance' as specific as you can.
Or say "open the editor" for a visual form instead of raw JSON.

Next: storybook-stylesheet (Stage 2) builds the character style sheet.
```

### Visual editor (optional)

When the user asks to open the editor or prefers a visual form over raw JSON,
launch the local browser editor:

```bash
uv run {skillDir}/scripts/edit_story.py --story {out_dir}/story.json
```

It opens a browser form (localhost only, free, no API key) covering the title,
style guide with live palette swatches, the cast with photo previews, and every
page (text, image prompt, per-page cast with hero-first ordering). Validates
against `assets/story_schema.json` before saving back to the same file and
preserves all fields it does not recognise.

Stop. Do not proceed until the user explicitly approves.
