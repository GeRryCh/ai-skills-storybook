---
name: storybook-story
description: >
  Stage 1 of 4 in the storybook pipeline — draft the manuscript.
  Use this skill whenever the user mentions: children's storybook, picture book,
  illustrated kids' book, bedtime story with pictures, story for my kid/child/toddler,
  "make a book about X", "write a storybook", "generate a kids book",
  illustrated book in any art style, comic-style book, graphic-novel-style book,
  all-ages illustrated book, or any request
  that combines a story idea with the word "illustrate", "pages", or "book".
  Even if the user only describes a character and says "make a story" — use this skill.
  This is the ENTRY POINT: it produces story.json (free, no API). Guides the user through
  a short schema-driven interview before writing story.json. The user edits and approves
  it, then storybook-stylesheet (Stage 2) and storybook-render (Stage 3) turn it into
  illustrated pages, and storybook-consolidate (Stage 4) assembles the finished book files.
metadata:
  requires:
    bins:
      - curl
---

# Storybook — Stage 1: Manuscript

## Pipeline

This is the first of four skills. Together they make a fully illustrated book:

1. **storybook-story** (this skill, free) — draft `story.json`: per-page text + image prompts + explicit cast (characters, objects, and locations — global + per-page). User edits and approves the text before any money is spent.
2. **storybook-stylesheet** (paid) — generate `style-sheet-{id}.png`: one reference image per cast entry (characters, objects, locations), plus one book-wide `style-frame.png`, the consistency anchors for every page.
3. **storybook-render** (paid) — generate each page illustration using only the cast entries' style sheets listed in that page's `cast` field, then overlay text. Output is page images only.
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
| Output directory | newly created folder `{cwd}/{slug(title)}/` | Don't ask unless the user named a path; surface in the config summary |

### Output directory resolution

The output directory is resolved **once, after the interview** (step 4 below), before any file is written:

- **Default:** `{out_dir} = {cwd}/{slug(title)}/` — a **new folder** named with the proposed working title's slug (same slug rule as `ref-{char-slug}.png` / `{slug(title)}.pdf`: lowercase, non-alphanumerics → hyphens, e.g. `pip-and-the-storm/`). Create it with `mkdir -p` before writing any crop, location photo, or `story.json`. All Stage-1 artifacts land inside.
- **Explicit user path:** if the user named an output directory in their request or during the interview, use that path verbatim — it IS the dedicated folder; do **not** nest a second `{slug}/` inside it.
- **Collision:** if the default folder already exists and is non-empty, append `-2` (then `-3`, …). Stage 1 is a fresh entry-point; no resume semantics.
- **Never** write book files directly into cwd.

All existing `{out_dir}` interpolations in this SKILL.md (`--out {out_dir}/ref-…`, `--out {out_dir}/loc-…`, `{out_dir}/story.json`, etc.) pick up this meaning unchanged.

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
4. **Propose working title and resolve `{out_dir}`** — derive a candidate title from the story idea (it can be refined at Gate 1) and apply the Output directory resolution rule above: create the new folder with `mkdir -p` now, before any file is written. Show the folder path to the user.
5. **Analyze any supplied photos** (see Source-photo analysis below) before authoring the cast.
6. **Detect real named places and gather location photos** (see Locations section below) — optional, skip gracefully if Perplexity MCP is unavailable.
7. Author cast and `style_guide` (from the style answer, a supplied **style reference image** if any, + STYLE_PRIMER).
8. **Gate 1: Configuration summary — confirm before writing** (see Configuration summary section below). Stop and wait for "go".
9. Draft page prose and image prompts — word counts per `text_mode` (and `age_band` when set; see STYLE_PRIMER for age-specific guidance; omit `age_band` for general/all-ages books). Drafting after Gate 1 avoids rework when a summary override changes word-count guidance.
10. Write `{out_dir}/story.json` following the schema exactly.
11. **Validate `story.json` against the schema** — run the validator and fix any errors before continuing:

```bash
uv run {skillDir}/scripts/validate_story.py --story {out_dir}/story.json
```

Exits 0 when clean. On errors (exit 2), fix `story.json` and re-run until clean. Surface any warnings to the user; they never block but may point to missing files or duplicate names worth reviewing.
12. Show the Handoff message.
13. **Gate 2: prose review** — stop and wait for explicit user approval before Stage 2.

### Style reference image (BEFORE authoring style_guide)

When the user supplies a **style reference image** (an illustration, screenshot, or any
image showing the look they want — as opposed to a character/cast photo), **view it with
the Read tool** before authoring `style_guide`. Then:

- Derive `style` (short label, e.g. `"anime cel-shaded, vivid palette"`) from what you see.
- Derive the full `style_guide` object: sample 3–5 dominant hex swatches for `palette`;
  name the `medium`, `line`, `lighting`, and `mood` you observe in the image.
- The image is analyzed **in-session only** — it is **not** persisted and **never** passed
  to the renderer or added to `cast`. The extracted text is the artifact; that text seeds
  the byte-identical style block injected into every paid API call.
- **Disambiguation:** if a supplied image's role is ambiguous (character likeness vs. style
  look), ask the user which it is before mapping. A style-ref image must never be added as
  a cast `ref_image`; a character photo must never seed `style_guide`.
- If no style reference image is provided, derive `style_guide` from the text answer as
  usual — this branch is fully opt-in.

The derived `style` + `style_guide` flow through Gate 1 like any other answer; the user
can confirm or edit them there and later in `edit_story.py`.

### Source-photo analysis (BEFORE authoring the cast)

When the user supplies reference photos (character/cast photos), **view every photo with
the Read tool before writing the cast** — never map a photo to a character without seeing
it first. Then map each photo (or an array of same-person angles) directly to that
character's `ref_image`.

### Cast — the `cast` array (drives consistency)

You MUST author an explicit `cast` array. Stage 2's style sheet is built from
this list and nothing else — it shows exactly these cast members and no others.

Each entry carries an optional `"kind"` field: `"character"` (default when absent),
`"object"` (a significant prop or vehicle), or `"location"` (a named real place).

- ONE entry per **real** character, object, or named place. Do not add scene words or pronouns.
- Each entry: `id` (stable lowercase slug, pattern `^[a-z][a-z0-9-]*$`, e.g. `pip`, `major-oak` — used in `pages[].cast` and `<id>` placeholders in `image_prompt`; also the style-sheet filename slug; NEVER reaches the image model), `name` (human-readable display name shown to Gemini, e.g. `Pip`), `appearance`
  (concrete: species, age, hair, **one specific outfit**, colours, distinguishing
  features — more specific = more consistent), and optional `ref_image` (photo
  path(s) for that one character).
- **Include exactly one outfit in `appearance`.** The outfit written there is locked
  into the style sheet at Stage 2 and the character wears it unchanged on every page.
  If the user did not specify clothing, invent one simple distinctive outfit and name
  it. Photos anchor face and hair likeness only — Stage 2 ignores clothing in photos.
- Optional `persistent_details` (PER-87): a small accessory/prop that the style sheet
  alone doesn't reliably hold onto (e.g. a cap, glasses) — Stage 3 auto-appends it as a
  continuity clause on every page this entry is on. See the name-only rule note below.
- If the user supplied character photos, map each to its character's `ref_image`
  following the Source-photo analysis step above. Use a single path for one photo,
  or an array of paths for several images of the same person (multiple angles).
  This Stage-1 mapping is the ONLY source of reference photos — Stage 2 builds
  each style sheet from that character's `ref_image` and nothing else. There is no
  shared global pool, so one character's photo never bleeds into another's sheet.
  Capped at 5 photos per character (Gemini 3 Pro Image character-lane limit).

Never rely on auto-extraction: the cast is never guessed from prose.

### Locations — real-place photo references (optional)

When the story mentions a **specific named real place** — a landmark, city, or recognizable
building (e.g. "the Eiffel Tower", "Sherwood Forest's Major Oak", "the Brandenburg Gate")
— you can download a few real photos of that place to use as references. Stage 2 turns the
photos into a `style-sheet-{id}.png` for the place — the same mechanism as characters and
objects — and Stage 3 renders pages against that sheet. Unlike objects, a location has no
render-time raw-photo fallback (PER-84): the photos exist only to build the Stage-2 sheet: a
page whose location has no usable `style_sheet` fails at render time rather than falling
back to a raw photo (a photoreal reference bleeds through and fights the book's art style).
This makes the rendered setting resemble the actual location while staying in the book's art
style.

**Never use this for generic settings** ("a forest", "the beach", "grandma's kitchen").
Those are described in prose only. When it is unclear whether a place is real and named, ask
the user along with the other Stage-1 questions.

#### Availability check (skip gracefully — never fail Stage 1)

The primary search path (below) only needs `curl`, which is required by this skill. Skip
the photo-download step and tell the user once only if **neither** path works — `curl`
unavailable/network-blocked **and** the Perplexity MCP fallback also unavailable:

> "Location photo references skipped — no Commons API access and Perplexity MCP not
> configured; the book renders fine without them: the place still gets a Stage-2
> reference sheet generated from its 'appearance' description alone."

Never block or fail Stage 1 because photo search is unavailable.

#### Searching for photos (per place)

Prefer the **Wikimedia Commons API directly** — it's faster, deterministic, free, and
returns better candidates than a general web search. Use `curl -s` (not WebFetch — WebFetch
summarizes the page through a model, which is the wrong tool for parsing API JSON) via Bash.
Try in order:

1. **Category listing** (best hit quality — prefer this when you know or can guess the
   place's Commons category name, e.g. `Category:Eiffel Tower`, `Category:Flame Towers`):
   ```bash
   curl -s "https://commons.wikimedia.org/w/api.php?action=query&format=json&list=categorymembers&cmtitle=Category:{Category Name}&cmtype=file&cmlimit=25"
   ```
   Category listing has been observed to beat text search noticeably — e.g.
   `Category:Flame Towers` returned usable Baku photos where the equivalent text query
   returned Calgary Tower, postage stamps, and scanned PDFs.
2. **File text search** — when you don't know the category name, or to discover it (`srnamespace=14` searches category names instead of files):
   ```bash
   curl -s "https://commons.wikimedia.org/w/api.php?action=query&format=json&list=search&srsearch={place name}&srnamespace=6&srlimit=10"
   ```
3. **`perplexity_search`** (fallback only — when the Commons API is unreachable, or to help
   identify the right category name): query like
   `"{place name}" photo site:commons.wikimedia.org`. **Avoid** `perplexity_ask` for direct
   image URLs — it has been observed to return hallucinated URLs that 404.

Prefer **freely-licensed** sources (Wikimedia Commons CC0/PD/CC-BY, or Unsplash public
domain). Find **about 3 distinct Commons photos** of the place — prefer different angles or
views (a wide establishing shot, a closer view, a distinctive detail close-up). Distinct
views give Stage 2 a stronger anchor than a single photo. For each candidate note its
`File:` title (e.g. `File:Tour_Eiffel_Wikimedia_Commons.jpg`).

**Landmark searches skew heavily toward night shots** — skylines and towers are
disproportionately photographed lit up after dark. A night reference biases the location
style sheet dark, which then bleeds into every page that uses that sheet. **Prefer daylight,
people-free frames**; check this before accepting a download, not just at the final review
below. Expect to reject several candidates on these grounds — searching Baku landmarks,
4 of the first 6 candidates were night skylines, and 4 were rejected in total before 3
usable daylight photos were found.

#### Building the direct-download URL

Use the **Special:FilePath** redirect for a deterministic, no-parsing URL:

```
https://commons.wikimedia.org/wiki/Special:FilePath/{File-title-without-File:-prefix}?width=1600
```

Spaces become `_` (Commons convention) — but this is cosmetic only: `fetch_location.py`
percent-encodes the URL itself, so a title can be passed human-readable, non-ASCII
characters included (e.g. Cyrillic, Azerbaijani, CJK place names). Example:

```
https://commons.wikimedia.org/wiki/Special:FilePath/Tour_Eiffel_Wikimedia_Commons.jpg?width=1600
```

Optionally query the Commons API for the canonical URL and license info:
```bash
curl -s "https://commons.wikimedia.org/w/api.php?action=query&titles=File:Tour_Eiffel_Wikimedia_Commons.jpg&prop=imageinfo&iiprop=url|extmetadata&format=json"
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
- It is a **daylight** shot — reject night/dusk skylines (see above; they bias the style
  sheet dark, which bleeds into every page using it).
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

References ride two independent lanes (PER-83), not one flat cap, and both lane caps are
per-model (PER-96): a character lane (4 flash / 5 pro) and an object lane, shared by
locations + objects, that's **10 on flash but only 6 on pro**. Within the object lane,
locations rank above objects — a wrong-style background poisons the whole frame, a
slightly-off prop does not:

> character lane: hero sheet → remaining character sheets
> object lane: location refs → object refs → book-wide style frame (lowest priority)

The location reference (its Stage-2 sheet — hard-required, no photo fallback, PER-84) only
competes against the object lane's cap — it is never dropped for having "too many characters" on the
page. It can still be dropped if the object lane itself is over its cap (logged — never
silently dropped), which on a pro page needs fewer object/location refs to trigger than on
flash. On pages whose `cast` lists only the place (no characters or objects), the location
sheet is the sole reference image.

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
- **Pages 2 to N-1**: story body. Spread word counts guided by `text_mode` and reading level; use age-band word counts from STYLE_PRIMER when `age_band` is set.
- **Page N**: closing spread. One short sentence or just title/end.

### Per-page `cast` field (required)

Every page MUST have a `cast` array listing the **ids** of all cast members that appear
on that page. ids must match `cast[].id` exactly. Use `[]` for wordless or
character-free pages (title cards, scenery-only spreads).

**Order matters: put the page hero first.** The first character-kind entry (kind=`"character"` or absent) is treated as the hero and leads the reference-ordering into the character lane — its style sheet is positioned first so it is never dropped. List the protagonist (e.g. the child the book is about) first on every page they appear. Characters contribute only their style sheet (no extra photo). References ride two independent lanes (PER-83): a character lane (4-ref flash cap, 5 with pro) and a separate object lane for objects + locations. Both lane caps are per-model (PER-96): the object lane is 10 on flash but only 6 on pro — Pro's documented budget spends more of its 14 slots on the character lane and a style-reference lane we don't use yet. A four-character flash page carries all four sheets exactly within the character-lane cap; a fifth character triggers auto-upgrade to pro before any sheet is dropped — but that same upgrade also shrinks the page's object lane from 10 to 6, so a page with a 5th character and several props can lose props it would have kept on flash. Object/location refs on the same page don't count against the character lane at all — they only compete against the object lane's cap for whichever model ends up being used.

Example:
```json
{ "page_num": 3, "cast": ["pip", "mira"], "text": "...", ... }
```

This drives Stage 3: `render_book.py` sends only those cast members' style sheets as
reference images when generating that page — the model never sees character sheets for
cast members not on the page.

### image_prompt rules

Every `image_prompt` MUST:
- Reference cast members with `<id>` placeholders (e.g. `<pip>`) — Stage 3 substitutes each `<id>` with the entry's display `name` before the Gemini call, so the model always sees real names.
- State the art style.
- NOT contain the actual story text — the script renders it (baked into the illustration in native mode, Pillow-overlaid in overlay mode).
- NOT contain text-position or safe-zone language — the script appends those transparently from `text_placement`.

**`<id>` placeholder + name-only rule (PER-56 + PER-42):** Use `<id>` placeholders for every entry in `pages[].cast` —
never repeat their `appearance` (species, age, colours, outfit, physical
traits). Each cast entry is reference-backed at render time (character/object → style
sheet; location → photo or sheet); the sheet/photo is a stronger, more consistent signal
than prose, and inline appearance description makes the model deviate from the reference.

- ✅ `<pip> scrambles up the hill` — placeholder + action, no appearance prose
- ✅ `<red-umbrella> tumbles in the wind` — placeholder; the object sheet carries its look
- ✅ `..at <eiffel-tower>..` — place placeholder; the location photo carries the setting
- ❌ `A small brave hedgehog named Pip scrambles up the hill` — echoes `appearance`, weakens sheet

**Pose, action, expression, and scene description stay in the prompt** — only inherent
appearance (what the cast entry looks like) is dropped.

**Non-cast background figures** (unnamed visitors, a park keeper, a passing dog) are
described in prose as usual — they have no style sheet to anchor on.

**The name-only rule narrows for small accessories (PER-87).** The style sheet is the
stronger signal for face, build, and main garment — but empirically not always for a
small accessory (e.g. a cap, glasses) on flash, which can drop it even with the sheet
attached. Do not fight this by writing a shortened accessory cue into `image_prompt` to
dodge the PER-42 appearance-echo warning — that games the check instead of satisfying it.
Set `cast[].persistent_details` on the entry instead (e.g. `"dark baseball cap with
sunglasses resting on the brim"`); Stage 3 appends it automatically on every page that
entry is on, and it never touches `image_prompt`, so the echo warning can't fire on it.

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
| age_band | {chosen or "general (omitted — all-ages default)"} | answered / default (general) |
| style | {chosen} | answered / default |
| text_mode | {chosen} | answered / default |
| aspect_ratio | {chosen or "unset — 3:2 used at render time"} | answered / default (3:2) |
| resolution | {value or "unset — 2K used at render time"} | default |
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

Required fields (`title`, `style`, `style_guide`, `cast`, `pages`) are always written. Every **optional** field (including `age_band`) is written to `story.json` only when the user's choice diverges from the omission semantics — accepting a default means the key is omitted (preserves the editor's round-trip contract; optional fields that match the documented default are never materialised). `age_band` is omitted when the user accepts the `general` default; only write it when the user specifies `3-5` or `5-8`.

Important edge cases:
- `saved_formats: []` records a "no book files" preference consumed by Stage 4 (storybook-consolidate) as a hint — this is NOT the same as omitting the field (omitted means "all formats" as default hint). No script reads this field; the consolidate skill uses it as the default answer when asking which formats to export, and the interactive choice there always wins. Only write `[]` when the user explicitly requests no book files.
- `aspect_ratio` omitted = built-in default `3:2` used at render time (deterministic — every page renders at the same framing). Only write `"auto"` when the user explicitly wants the model to pick framing per page call (non-deterministic, produces mixed page sizes — discourage this for any book ending in a bound PDF/EPUB). Write any other enum value when the user wants a different locked framing.
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
