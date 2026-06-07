---
name: storybook-story
description: >
  Stage 1 of 4 in the storybook pipeline — draft the manuscript.
  Use this skill whenever the user mentions: children's storybook, picture book,
  illustrated kids' book, bedtime story with pictures, story for my kid/child/toddler,
  "make a book about X", "write a storybook", "generate a kids book", or any request
  that combines a story idea with the word "illustrate", "pages", or "book".
  Even if the user only describes a character and says "make a story" — use this skill.
  This is the ENTRY POINT: it produces story.json (free, no API). Guides the user through
  a short schema-driven interview before writing story.json. The user edits and approves
  it, then storybook-stylesheet (Stage 2) and storybook-render (Stage 3) turn it into
  illustrated pages, and storybook-consolidate (Stage 4) assembles the finished book files.
metadata:
  requires: {}
---

# Storybook — Stage 1: Manuscript

## Pipeline

This is the first of four skills. Together they make a fully illustrated picture book:

1. **storybook-story** (this skill, free) — draft `story.json`: per-page text + image prompts + explicit character cast (global + per-page). User edits and approves the text before any money is spent.
2. **storybook-stylesheet** (paid) — generate `style-sheet-{name}.png`: one reference image per character, the consistency anchors for every page.
3. **storybook-render** (paid) — generate each page illustration using only the character sheets for the characters listed on that page, then overlay text. Output is page images only.
4. **storybook-consolidate** (free) — after the user reviews the rendered pages, choose formats interactively (`saved_formats` is the default answer), merge pages into PDF and/or fixed-layout EPUB3, package everything into a zip. No API calls.

The four skills hand off a single file: `story.json` in the output directory.

Read `assets/STYLE_PRIMER.md` and `assets/story_schema.json` before writing the manifest. Mimic the structure in `assets/story_example.json`.

---

## Interview

Act as a guide through the decisive configuration choices **before writing anything**. Never re-ask what the user already stated in their request — ask only unknowns.

### Static inputs (creative/structural — not schema-driven)

These stay fixed in SKILL.md because they are prose-authored, not scalar knobs that churn with schema changes:

| Input | Default | Notes |
|-------|---------|-------|
| Story idea | required | Core premise, setting, who the book is about |
| Character look | — | Real photos of child/family, or invented characters? + absolute photo paths if photos supplied (may contain multiple people — see Source-photo analysis) |
| Number of pages | `8` | Story length; ask alongside `text_mode` — both express length (PER-31) |
| Output directory | current working directory | Don't ask; surface in the config summary |

### Schema-driven questions — the generic algorithm

The following algorithm covers **scalar book-level knobs** that churn when the schema evolves. SKILL.md never needs editing when a knob is added; only `story_schema.json` changes.

1. Read `assets/story_schema.json`. Collect every top-level property whose definition contains an `"x-interview"` key. Properties without it are never asked.
2. `"priority": "core"` → ask now, in the interview batch. `"priority": "advanced"` → never ask; present once in the configuration summary (Gate 1) as an overridable default.
3. Per core question: question text = the `"ask"` value; if the property has an `"enum"`, the enum values become options with the default first (schema `"default"` if present, else the first enum value); option glosses derived from the property's `"description"`; non-enum fields are free-text with examples from `"ask"`.
4. Enums with more than 4 values (e.g. `aspect_ratio`): present the default plus the 3 most visually representative options; any legal enum value typed free-form is accepted (the structured tool's automatic "Other" covers this).
5. Use a structured question tool (e.g. AskUserQuestion) when available — batch core schema questions with still-unknown static inputs, up to the tool's per-call limit (4 questions/call); use a second call only if needed; otherwise degrade to one compact plain-chat message. Target ~4–6 questions total.
6. **Required-field guard:** after collecting, cross-check `schema.required`. Any required field NOT covered by a core question or the known prose-authored set (`title`, `style_guide`, `cast`, `pages`) is a gap — surface it to the user and ask explicitly. Never skip it silently. (The guard makes the dynamic mechanism self-maintaining for the dangerous case where a new required scalar field is added to the schema.)

---

## Steps

1. Read `assets/STYLE_PRIMER.md` (word counts, text placement, safe-zone rule).
2. Read `assets/story_schema.json` and `assets/story_example.json`.
3. **Run the Interview** — collect static inputs and core schema choices in one or two structured-question batches. Fold "is this a specific real named place?" into the batch when the story idea mentions a recognizable landmark (see Locations below).
4. **Analyze any supplied photos** (see Source-photo analysis below) before authoring the cast.
5. **Detect real named places and gather location photos** (see Locations section below) — optional, skip gracefully if Perplexity MCP is unavailable.
6. Author cast, propose title, author `style_guide` (from the style answer + STYLE_PRIMER).
7. **Gate 1: Configuration summary — confirm before writing** (see Configuration summary section below). Stop and wait for "go".
8. Draft page prose and image prompts — word counts per the now-locked `age_band` and `text_mode` (drafting after Gate 1 avoids rework when a summary override changes word-count guidance).
9. Write `{out_dir}/story.json` following the schema exactly.
10. **Validate `story.json` against the schema** — run the validator and fix any errors before continuing:

```bash
uv run {skillDir}/scripts/validate_story.py --story {out_dir}/story.json
```

Exits 0 when clean. On errors (exit 2), fix `story.json` and re-run until clean. Surface any warnings to the user; they never block but may point to missing files or duplicate names worth reviewing.
11. Show the Handoff message.
12. **Gate 2: prose review** — stop and wait for explicit user approval before Stage 2.

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
— you can download a few real photos of that place to use as references. Stage 2 turns the
photos into a `style-sheet-{slug}.png` for the place — the same mechanism as characters and
objects — and Stage 3 renders pages against that sheet (the raw photos are only a fallback).
This makes the rendered setting resemble the actual location while staying in the book's art
style.

**Never use this for generic settings** ("a forest", "the beach", "grandma's kitchen").
Those are described in prose only. When it is unclear whether a place is real and named, ask
the user along with the other Stage-1 questions.

#### Availability check (skip gracefully — never fail Stage 1)

This step needs the Perplexity MCP tools (`perplexity_search` etc.). If they are not
available in this session, skip the photo-download step and tell the user once:

> "Location photo references skipped — Perplexity MCP not configured; the book renders
> fine without them: the place still gets a Stage-2 reference sheet generated from its
> 'appearance' description alone."

Never block or fail Stage 1 because of a missing MCP.

#### Searching for photos (per place)

Call `perplexity_search` with a query like `"{place name}" photo site:commons.wikimedia.org`.

Prefer **freely-licensed** sources (Wikimedia Commons CC0/PD/CC-BY, or Unsplash public
domain). Find **about 3 distinct Commons photos** of the place — prefer different angles or
views (a wide establishing shot, a closer view, a distinctive detail close-up). Distinct
views give Stage 2 a stronger anchor than a single photo. For each candidate note its
`File:` title (e.g. `File:Tour_Eiffel_Wikimedia_Commons.jpg`).

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

#### Downloading and validating (per photo)

Run `fetch_location.py` once per photo with a numbered output name:

```bash
# Photo 1 — wide establishing view
uv run {skillDir}/scripts/fetch_location.py \
  --url "https://commons.wikimedia.org/wiki/Special:FilePath/{File-title-1}?width=1600" \
  --out {out_dir}/loc-{slug}-1.jpg

# Photo 2 — closer view or different angle
uv run {skillDir}/scripts/fetch_location.py \
  --url "https://commons.wikimedia.org/wiki/Special:FilePath/{File-title-2}?width=1600" \
  --out {out_dir}/loc-{slug}-2.jpg

# Photo 3 — distinctive detail or third angle
uv run {skillDir}/scripts/fetch_location.py \
  --url "https://commons.wikimedia.org/wiki/Special:FilePath/{File-title-3}?width=1600" \
  --out {out_dir}/loc-{slug}-3.jpg
```

**Naming convention:** `loc-{slug}-N.jpg` (numbered), where slug is the place name
lowercased with non-alphanumerics replaced by hyphens — same rule as `ref-{char-slug}.png`
for crops. Example: `loc-eiffel-tower-1.jpg`, `loc-eiffel-tower-2.jpg`, `loc-major-oak-1.jpg`.

On a non-zero exit code, read the error message and try the next candidate image. The
script silently overwrites the output file — iteration is free.

After each successful download, **view the file with the Read tool** and confirm:
- The image shows the **right place**, recognizably.
- It is **well-framed** (no extreme close-ups or partial views).
- It contains **no prominent people** — a person in the frame risks being read as a
  character by the render model. If present, pick another image.

**Target 3 accepted photos; accept fewer (minimum 1) when Commons lacks enough suitable
people-free photos — tell the user how many you found. Never pad with wrong or
people-heavy photos just to reach 3.**

#### Writing to story.json

Add an entry to the top-level `cast` array with `"kind": "location"`:

```json
{
  "name": "Eiffel Tower",
  "kind": "location",
  "appearance": "iron lattice tower on the Champ de Mars, Paris",
  "ref_image": [
    "loc-eiffel-tower-1.jpg",
    "loc-eiffel-tower-2.jpg",
    "loc-eiffel-tower-3.jpg"
  ],
  "source_url": [
    "https://commons.wikimedia.org/wiki/File:Tour_Eiffel_Wikimedia_Commons.jpg",
    "https://commons.wikimedia.org/wiki/File:Eiffel_Tower_from_Trocadero.jpg",
    "https://commons.wikimedia.org/wiki/File:Paris_Eiffel_Tower_seen_from_the_Seine.jpg"
  ]
}
```

`source_url` is an array **parallel to `ref_image`** — one Commons file-page URL per
downloaded photo, same order (license/attribution provenance). Stage 2 generates
`style-sheet-eiffel-tower.png` from these photos, exactly as for characters and objects.

Then add `"Eiffel Tower"` to the `pages[].cast` array of every page **physically set at
that place only** — never book-wide. This per-page selection is mandatory: without it,
the place's environment would bleed into every page of the book (see
docs/future-explorations.md, PER-33).

The page's `image_prompt` must also **name the place** (e.g. "...under the Eiffel Tower...")
so the render model knows the setting. Use the place **name only** — do not re-describe
its appearance in the prompt; the location sheet (or photo) is the reference, and prose
re-description makes the model deviate from it (same rule as character/object entries,
see **image_prompt rules** below).

#### Cap note

The location reference (its Stage-2 sheet, or the first photo as fallback) is the
**lowest-priority** reference image within Stage 3's per-model cap (4 flash default / 5 pro):

> hero sheet → hero photo → remaining character sheets → object refs → location sheet

On pages with 3 or more cast members, the location reference may be dropped from the cap
(it will be logged — never silently dropped). On pages whose `cast` lists only the place
(no characters or objects), the location sheet is the sole reference image.

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
about) first on every page they appear; with a 4-ref cap (flash default; 5 with pro), a fifth reference
may be dropped to make room for the hero's photo. Three-character pages on flash can carry hero
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

**Name-only rule for cast members (PER-42):** Refer to every entry in `pages[].cast` by
**name only** — never repeat their `appearance` (species, age, colours, outfit, physical
traits). Each cast entry is reference-backed at render time (character/object → style
sheet; location → photo or sheet); the sheet/photo is a stronger, more consistent signal
than prose, and inline appearance description makes the model deviate from the reference.

- ✅ "Pip scrambles up the hill" — name + action, no appearance prose
- ✅ "Red Umbrella tumbles in the wind" — name only; the object sheet carries its look
- ✅ "...at the Eiffel Tower..." — place name; the location photo carries the setting
- ❌ "A small brave hedgehog named Pip scrambles up the hill" — echoes `appearance`, weakens sheet

**Pose, action, expression, and scene description stay in the prompt** — only inherent
appearance (what the cast entry looks like) is dropped.

**Non-cast background figures** (unnamed visitors, a park keeper, a passing dog) are
described in prose as usual — they have no style sheet to anchor on.

---

## Configuration summary (Gate 1 — before writing story.json)

Present the full resolved configuration for confirmation **before writing anything**. This is the first gate; the prose-review gate (Gate 2) follows after `story.json` is written.

**Show a static header block:**
```
📖 Title (proposed): {proposed_title}
📄 Pages: {N}
📁 Output: {out_dir}
🎭 Cast: {name} → {photo path or "invented"}, ...
🎨 Style: {style_guide gist: medium + palette}
```

**Then a settings table:**

| Setting | Value | Source |
|---------|-------|--------|
| age_band | {chosen} | answered / default |
| style | {chosen} | answered / default |
| text_mode | {chosen} | answered / default |
| resolution | {value or "unset — 2K used at render time"} | default |
| aspect_ratio | {value or "unset — model picks per page"} | default |
| saved_formats | {value or "pdf + epub (all)"} | default |
| language | {value} | default |
| fonts | {value or "bundled Andika / Patrick Hand"} | default |

The table rows come from the `x-interview`-annotated properties in `story_schema.json` — if new annotated fields appear in the schema, they appear here automatically.

**Closing prompt:**
```
Reply "go" to accept this configuration, or name any setting to change
(e.g. "resolution 4K, aspect_ratio 3:4"). Enum values for each setting
are listed in the schema; any legal value is accepted.
```

Validate overrides against the field's `enum`; re-show only the changed rows; then proceed to drafting.

### Omission rule

Required fields (`title`, `age_band`, `style`, `style_guide`, `cast`, `pages`) are always written. Every **optional** field is written to `story.json` only when the user's choice diverges from the omission semantics — accepting a default means the key is omitted (preserves the editor's round-trip contract; optional fields that match the documented default are never materialised).

Important edge cases:
- `saved_formats: []` records a "no book files" preference consumed by Stage 4 (storybook-consolidate) as a hint — this is NOT the same as omitting the field (omitted means "all formats" as default hint). No script reads this field; the consolidate skill uses it as the default answer when asking which formats to export, and the interactive choice there always wins. Only write `[]` when the user explicitly requests no book files.
- `aspect_ratio` omitted = model picks framing per page call (non-deterministic). Only write it when the user wants locked framing.
- `resolution` omitted = 2K at render time. Only write it when the user specifies a quality.

---

## Handoff

### Optional top-level config

Every optional book-level knob is defined in `assets/story_schema.json`; each property's `"description"` is the authoritative reference; interview/summary behavior comes from its `"x-interview"` annotation. See `CLAUDE.md` for the contract.

Per-page `text_placement` defaults to `"floating"` (native mode). Override to `"top"` or `"bottom"` to pin text to a fixed band.

**Long mode text-page backgrounds:** an optional top-level `text_background_prompt` customizes the shared book-wide background (omit for a generic style-matched one). An optional per-page `text_background_prompt` gives that page its OWN dedicated background instead of the shared one, via one extra paid Gemini call.

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
page (text, image prompt, per-page cast with hero-first ordering, per-page text
mode override, per-page image model override). Fields that have no effect given
the current effective text mode are greyed-out; the `floating` placement option
is hard-hidden when not in native mode. Validates against `assets/story_schema.json`
before saving back to the same file and preserves all fields it does not recognise.

Stop. Do not proceed until the user explicitly approves.
