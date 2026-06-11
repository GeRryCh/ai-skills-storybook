#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
#     "Pillow",
# ]
# ///
"""
Render all pages of a children's storybook.

For each page in story.json:
  1. Generates the illustration via the Gemini API (default: gemini-3.1-flash-image),
     passing the style sheet + character refs as input images for consistency.
  2. In overlay/long mode: runs overlay_text.py to composite the story text.
  3. Prints MEDIA: <path> for each final page.

Pages are fully independent, so they are all fired concurrently via asyncio
(one async Gemini request per page, no thread pool, no local concurrency cap).
Transient 429/5xx responses are retried with exponential backoff + jitter.

Skips pages whose final file(s) already exist (safe to re-run after partial failure).
In long mode, per-artifact skip checks mean a missing text page can be rebuilt without
re-firing the paid art call — and without needing GEMINI_API_KEY when no paid call is made.

Requires GEMINI_API_KEY in the environment when a paid Gemini call is needed.

This script produces page images only. Assembly into PDF/EPUB is Stage 4
(storybook-consolidate skill: merge_pdf.py / merge_epub.py / package_book.py — free,
no API cost, run independently after reviewing the rendered pages).

Model selection (precedence: CLI --model > page 'model' > story 'model' > default flash):
  gemini-3.1-flash-image — default; faster/cheaper, up to 4 reference images per call.
  gemini-3-pro-image     — higher quality, up to 5 reference images per call.
  Use --model or set page-level/book-level 'model' in story.json to override.
  Style sheets (Stage 2) always use gemini-3-pro-image regardless of this setting.
  Auto-upgrade: when a page's reference list has ≥5 images and the effective model is
  flash (including an explicit CLI or per-page flash override), that page is silently
  upgraded to gemini-3-pro-image for that call only. Logged as:
    "auto-upgraded page N to gemini-3-pro-image (5 refs > flash cap 4)"
  story.json is never modified. Manually pinning a page's model to pro solely to
  avoid the 4-ref cap is therefore no longer necessary.

Text modes:
  overlay — safe-zone art + Pillow text overlay → pages/page-NN.png
  native  — model bakes text into illustration → pages/page-NN-native.png
  long    — full-bleed art (no text) + separate text page → pages/page-NN-long.png +
            pages/page-NN-long-text.png. Cover (page 1) stays combined (overlay-style).
            Text pages sit on ONE shared model-generated background per book
            (pages/text-bg-long.png — generated once, before pages fire, +1 paid call
            total); a page with text_background_prompt gets its own dedicated bg instead
            (pages/page-NN-long-bg.png, +1 call for that page). Text-page composition
            itself is free Pillow work. Cost: N art calls + 1 shared bg call.

Usage:
  uv run render_book.py --story /path/to/story.json [--out-dir DIR]
                        [--from N] [--only N] [--resolution 1K|2K|4K]
                        [--aspect-ratio RATIO] [--text-mode overlay|native|long]
                        [--model gemini-3.1-flash-image|gemini-3-pro-image]
                        [--saved-formats pdf epub|none]
"""

from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import json
import mimetypes
import os
import random
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
OVERLAY_SCRIPT = SCRIPTS_DIR / "overlay_text.py"

# Gemini image-generation config.
FLASH_IMAGE_MODEL = "gemini-3.1-flash-image"
PRO_IMAGE_MODEL = "gemini-3-pro-image"
IMAGE_MODEL = FLASH_IMAGE_MODEL  # default; overridable per page/book/CLI
IMAGE_MODELS = [FLASH_IMAGE_MODEL, PRO_IMAGE_MODEL]  # keep in sync with story_schema.json
MODEL_MAX_INPUT_IMAGES = {
    FLASH_IMAGE_MODEL: 4,  # Gemini 3.1 Flash Image: up to 4 reference images per call
    PRO_IMAGE_MODEL: 5,    # Gemini 3 Pro Image: up to 5 reference images per call
}
MAX_INPUT_IMAGES = 4  # safe fallback cap for unrecognised models

# Retry policy for transient failures (429 rate-limit / 5xx). Pages are fired all
# at once, so a single 429 must not silently drop a page.
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete image in the book's art style. Pay attention to composition, "
    "lighting, color, and visual balance. Reference images follow the prompt, "
    "each introduced by a short text note identifying it. Rules by kind: "
    "A 'character style sheet' defines that character's design, outfit, and "
    "the book's art style — follow it exactly. "
    "A 'character photograph' supplies that character's facial likeness only — "
    "clothing, outfit, and character design always come from the style sheet, "
    "never from any photograph. "
    "An 'object reference sheet' defines that object's design, colours, and "
    "proportions in the book's art style — follow it exactly. "
    "An 'object photograph' supplies that object's shape, materials, and "
    "distinguishing details — redraw it fully in the book's art style, "
    "never as a photograph. "
    "A 'location reference sheet' defines that place's look in the book's art "
    "style — follow it exactly; it is scenery, never a character. "
    "A 'location photograph' shows a real place that is the SETTING of the "
    "scene: reproduce its recognizable architecture, landmarks, and geography, "
    "redrawn fully in the book's art style — it is scenery, never a "
    "character or a person, and never a photograph. "
    "The identification notes are instructions, not story text; never letter "
    "them into the image. "
    "Output only the generated image without additional commentary."
)

TEXT_SAFE_ZONE_DIRECTIVE = (
    "Leave the {placement} quarter of the image as a "
    "low-detail, {tone} area suitable for overlaying text. "
    "Do not place any narrative text in the image."
)

# Long mode: the model fills the full canvas — no text, no safe zone reserved.
FULL_BLEED_ART_DIRECTIVE = (
    "Use the full canvas for the illustration — rich, full-bleed artwork from edge to edge "
    "with no reserved text area. Do not render any words, letters, captions, or typography "
    "anywhere in the image."
)
# Long mode: one shared text-page background per book, generated once in render_all
# before pages fire (pages only consume it — generating inside render_page would race).
SHARED_TEXT_BG_NAME = "text-bg-long.png"
STYLE_ANCHOR = (
    "Art style and character design must match the provided reference "
    "sheet(s) exactly. If a reference photograph of a character is also provided, "
    "match that character's facial likeness and identity to the photo, but redraw "
    "fully in the art style of the sheet(s) — never reproduce photographic detail. "
    "Each character wears exactly the outfit shown on their reference sheet; "
    "never take clothing or outfit from a photograph. "
    "Consistent character design, {style}. "
    "Keep this exact style identical on every page of the book."
)

# Ink-and-contrast clause spliced into NATIVE_TEXT_DIRECTIVE.
# dark: near-black ink on the model's habitual lightly-toned safe area — default, high contrast.
# light: cream-white ink, but the model must also provide a suitably DARK backdrop so the light
#   ink stays legible (without the backdrop instruction the model defaults to a light patch, which
#   strands light ink on a light field and is invisible).
_INK_CLAUSE = {
    "dark": (
        "dark ink, crisp and highly legible against the low-detail background"
    ),
    "light": (
        "cream-white ink, crisp and highly legible against a darker, low-detail "
        "area of the scene (use a subtle dark tone behind the text, never a light field)"
    ),
}

# Used in --text-mode native: tells the model to render text into the illustration.
# One fixed, detailed letterform descriptor reused verbatim on every page so the
# lettering style stays consistent book-wide (each page is a separate stateless call
# with no seed). {font_ref} names a target font family when story.fonts configures one;
# the descriptor adjectives must stay coherent with that family.
# {ink_clause} carries both ink color and required backdrop (see _INK_CLAUSE above).
NATIVE_TEXT_DIRECTIVE = (
    "Render this exact story text as part of the illustration, {placement_clause}. "
    "Letter it in a clean, legible style consistent with the book's art style{font_ref}: "
    "even weight, steady baseline, generous letter spacing, {ink_clause} — "
    "and keep this exact "
    "lettering style identical on every page of the book. Preserve the text's natural "
    "reading direction. Reproduce every word, comma, quotation mark, and dash exactly as "
    'written — no changes, no omissions, no extra text: "{text}"'
)


# Placement clause spliced into NATIVE_TEXT_DIRECTIVE. "floating" hands the model creative
# control over where the text lands; top/bottom pin it to a band.
FLOATING_PLACEMENT_CLAUSE = (
    "integrated naturally into the scene wherever it best suits the composition — you choose "
    "the most visually pleasing, creative placement for the page (open sky, an uncluttered "
    "patch of background, along an edge or corner), kept clear of faces and the main "
    "subject and fully legible"
)


def _placement_clause(placement: str) -> str:
    if placement == "floating":
        return FLOATING_PLACEMENT_CLAUSE
    return f"integrated naturally into the {placement} of the scene"


def _overlay_placement(placement: str) -> str:
    """Overlay (Pillow) composites at a fixed band; it can't float. Degrade to bottom."""
    return "bottom" if placement == "floating" else placement


def load_story(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def resolve_model(
    story: dict,
    page: dict | None = None,
    cli_model: str | None = None,
) -> str:
    """Resolve which Gemini model to use for one render call.

    Precedence: CLI --model > page 'model' field > story top-level 'model' > IMAGE_MODEL default.
    page=None skips per-page resolution (used for book-wide shared text-bg generation).
    """
    page_model = page.get("model") if page else None
    return cli_model or page_model or story.get("model") or IMAGE_MODEL


def resolve_text_mode(
    story: dict,
    page: dict | None = None,
    cli_mode: str | None = None,
) -> str:
    """Resolve the effective text mode for one page (or book-wide when page=None).

    Precedence: CLI --text-mode > page 'text_mode' field > story top-level 'text_mode' > "native".
    page=None skips per-page resolution (used for summary/shared-bg logic where no specific
    page is in scope).

    Keep in sync with _page_text_mode() in merge_pdf.py and merge_epub.py
    (the skills share no module; all three copies must stay identical).
    """
    page_mode = page.get("text_mode") if page else None
    return cli_mode or page_mode or story.get("text_mode") or "native"


# Keep in sync with the copy in make_style_sheet.py
# (the two skills share no module; both copies must stay identical).
LEGACY_KEY_MESSAGE = (
    "ERROR: story.json uses the pre-PER-34 schema. The cast contract changed "
    "(breaking, no shim):\n"
    "  top-level \"characters\"  ->  \"cast\"  (same entry shape; add optional\n"
    "                                         \"kind\": \"character\"|\"object\"|\"location\",\n"
    "                                         default character)\n"
    "  top-level \"locations\"   ->  cast entries with \"kind\": \"location\"\n"
    "                                (keep ref_image and source_url; fold\n"
    "                                 \"description\" into \"appearance\")\n"
    "  pages[].characters      ->  pages[].cast  (ONE flat name list, mixed\n"
    "                                kinds; hero = first character-kind entry)\n"
    "  pages[].location        ->  append the place name to that page's\n"
    "                                \"cast\" list\n"
    "Migrate story.json (or re-run Stage 1) and re-run. See\n"
    "skills/storybook-story/assets/story_schema.json."
)


def reject_legacy_keys(story: dict) -> None:
    """Fail fast (exit 2) on pre-PER-34 story.json files. Breaking rename, no shim."""
    found: list[str] = []
    if "characters" in story:
        found.append('top-level "characters"')
    if "locations" in story:
        found.append('top-level "locations"')
    pages = story.get("pages")
    if isinstance(pages, list):
        for p in pages:
            if isinstance(p, dict):
                pn = p.get("page_num", "?")
                if "characters" in p:
                    found.append(f'pages[{pn}].characters')
                if "location" in p:
                    found.append(f'pages[{pn}].location')
    if found:
        print(
            f"Legacy keys found: {', '.join(found)}\n\n{LEGACY_KEY_MESSAGE}",
            file=sys.stderr,
        )
        sys.exit(2)


# Keep in sync with the copies in make_style_sheet.py
# (the two skills share no module; both copies must stay identical).
STYLE_GUIDE_EXAMPLE = """  "style_guide": {
    "medium": "soft watercolor with thin pen-and-ink outline",
    "palette": ["warm cream #F5E9D4", "sage green #8FAF85", "dusty coral #E8917A"],
    "line": "thin sepia ink, even weight, rounded corners",
    "lighting": "golden-hour side-light, soft warm shadows",
    "mood": "cozy, gentle, storybook calm"
  }"""


def build_style_block(story: dict) -> str:
    """Verbatim style descriptor for this book, injected byte-identically into every call.

    Assembles a deterministic block from the required 'style_guide' object's fields in
    fixed order (medium → palette → line → lighting → mood) so every API call receives
    exactly the same string by construction — the documented cross-page consistency
    mechanism (per Google's Book_illustration workflow: one verbatim style string reused
    on every independent call).

    'style_guide' is required. main() validates it via require_style_guide() before any
    paid API work; raises ValueError if it is missing, malformed, or assembles empty.
    """
    guide = story.get("style_guide")
    parts: list[str] = []
    if isinstance(guide, dict):
        if guide.get("medium"):
            parts.append(f"Medium: {guide['medium']}")
        if guide.get("palette"):
            palette_str = ", ".join(str(s) for s in guide["palette"])
            parts.append(f"Palette: {palette_str}")
        if guide.get("line"):
            parts.append(f"Line: {guide['line']}")
        if guide.get("lighting"):
            parts.append(f"Lighting: {guide['lighting']}")
        if guide.get("mood"):
            parts.append(f"Mood: {guide['mood']}")
    if not parts:
        raise ValueError("story.json is missing a usable top-level 'style_guide' object")
    return ". ".join(parts)


def require_style_guide(story: dict) -> None:
    """Fail fast — before any paid API work — when 'style_guide' is missing or empty."""
    try:
        build_style_block(story)
    except ValueError:
        print(
            "ERROR: story.json must define a top-level 'style_guide' object — it is the\n"
            "book-wide consistency anchor injected verbatim into every image call.\n"
            "Add for example:\n\n" + STYLE_GUIDE_EXAMPLE + "\n\n"
            "See skills/storybook-story/assets/STYLE_PRIMER.md for the field reference.",
            file=sys.stderr,
        )
        sys.exit(2)


# Keep in sync with the copy in make_style_sheet.py
# (the two skills share no module; both copies must stay identical).
CAST_ID_MIGRATION_MESSAGE = (
    "ERROR: story.json uses the pre-PER-56 schema. The cast reference contract changed "
    "(breaking, no shim):\n"
    "  cast[].id  — add a stable lowercase slug to every cast entry\n"
    "               pattern: ^[a-z][a-z0-9-]*$  (e.g. 'pip', 'major-oak')\n"
    "  pages[].cast — change name strings to id strings (matching cast[].id)\n"
    "  image_prompt — replace literal name mentions with <id> placeholders\n"
    "                 (render resolves <id> → display name before every Gemini call)\n"
    "Migrate story.json (or re-run Stage 1) and re-run. See\n"
    "skills/storybook-story/assets/story_schema.json."
)

_CAST_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def require_cast_ids(story: dict) -> None:
    """Fail fast (exit 2) on pre-PER-56 story.json files missing cast[].id. Breaking change, no shim."""
    cast = story.get("cast")
    if not isinstance(cast, list):
        return  # other validators will catch a missing/malformed cast
    missing: list[str] = []
    for entry in cast:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        name = entry.get("name") or "unnamed"
        if not isinstance(cid, str) or not _CAST_ID_PATTERN.match(cid):
            missing.append(repr(name))
    if missing:
        print(
            f"Cast entries missing a valid 'id': {', '.join(missing)}\n\n"
            f"{CAST_ID_MIGRATION_MESSAGE}",
            file=sys.stderr,
        )
        sys.exit(2)


def resolve_cast_placeholders(text: str, story: dict) -> str:
    """Replace <id> placeholders in a prompt with cast entry display names.

    Broad regex <([^<>]+)> catches malformed tokens (e.g. <Pip>, <grandma rosa>)
    so they can never slip through unsubstituted to the image model (in native text
    mode a strong text-renderer would letter them into the art). Uses a function
    replacement — never a string replacement — to avoid misinterpreting \\1/\\g
    escapes in display names. Unknown/malformed tokens are stripped of angle brackets
    and logged as warnings; the validator is the hard gate.
    """
    cast_by_id: dict[str, str] = {
        c.get("id", ""): c.get("name", c.get("id", ""))
        for c in story.get("cast", [])
        if isinstance(c, dict) and c.get("id")
    }

    def _replace(m: re.Match) -> str:
        token = m.group(1)
        if token in cast_by_id:
            return cast_by_id[token]
        # Unknown / malformed: strip brackets, warn. Never let <…> reach the model.
        print(
            f"Warning: image_prompt placeholder <{token}> is not a known cast id; "
            "stripping angle brackets. Add the id to story.json cast or use validate_story.py.",
            file=sys.stderr,
        )
        return token

    return re.sub(r"<([^<>]+)>", _replace, text)


def book_premise(story: dict) -> str:
    """Book-wide narrative anchor (PER-66) — render-only, injected verbatim into every page
    prompt immediately after STYLE_ANCHOR.

    Deliberately NOT part of build_style_block(): that helper also feeds the OpenAI
    gpt-image-2 stylesheet calls (Stage 2), where narrative premise is noise and could
    distort the character sheet. Stage 2 stays untouched.

    Empty / absent premise → returns '' → zero behavioural change.
    """
    return (story.get("premise") or "").strip()


def build_image_prompt(page: dict, story: dict, text_mode: str = "native") -> str:
    placement = page.get("text_placement", "floating")
    style = build_style_block(story)
    anchor = STYLE_ANCHOR.format(style=style)
    # Fold the optional book-wide narrative anchor into the STYLE_ANCHOR text (PER-66).
    # One insertion point — all three mode returns carry it unchanged via {anchor}.
    # Keeps NATIVE_TEXT_DIRECTIVE (the lettering instruction) as the final token in native
    # mode, which is critical: an end-append would risk the model lettering the premise.
    premise = book_premise(story)
    if premise:
        anchor = f"{anchor}. {premise}"

    # Resolve <id> placeholders → display names. Single chokepoint covering all text modes.
    # The model must never receive raw <id> tokens — especially in native mode where a
    # strong text-renderer would letter them into the art.
    resolved_prompt = resolve_cast_placeholders(page.get("image_prompt", ""), story)

    if text_mode == "native":
        # Strip any baked-in safe-zone sentence (". Leave the <...>.") from the prompt so the
        # model gets a clean slate — then append NATIVE_TEXT_DIRECTIVE with the verbatim text.
        raw_prompt = re.sub(r"\.\s*Leave the [^.]+\.?\s*$", "", resolved_prompt)
        base = raw_prompt.rstrip(". ")
        text = page.get("text", "")
        # Reuse the same role -> family map the overlay path uses; name the family as a
        # lettering reference so the baked-in text matches the book's configured font.
        font_role = page.get("font", "reader")
        family = (story.get("fonts") or {}).get(font_role)
        font_ref = f", styled after the {family} typeface" if family else ""
        color = page.get("text_color_hint", "dark")
        ink_clause = _INK_CLAUSE.get(color, _INK_CLAUSE["dark"])
        native = NATIVE_TEXT_DIRECTIVE.format(
            placement_clause=_placement_clause(placement), text=text,
            font_ref=font_ref, ink_clause=ink_clause,
        )
        return f"{base}. {anchor}. {native}"

    if text_mode == "long":
        # Long mode art page: strip any baked-in safe-zone sentence; request full-bleed
        # art with no text (text is rendered on a separate physical page).
        raw_prompt = re.sub(r"\.\s*Leave the [^.]+\.?\s*$", "", resolved_prompt)
        base = raw_prompt.rstrip(". ")
        return f"{base}. {anchor}. {FULL_BLEED_ART_DIRECTIVE}"

    # overlay (default): unchanged behaviour. "floating" is native-only -> bottom here.
    # tone: light hint → request a dark text-safe zone so the white panel blends cleanly.
    base = resolved_prompt.rstrip(". ")
    color = page.get("text_color_hint", "dark")
    tone = "darkly-toned" if color == "light" else "lightly-toned"
    safe_zone = TEXT_SAFE_ZONE_DIRECTIVE.format(
        placement=_overlay_placement(placement), tone=tone
    )
    return f"{base}. {safe_zone}. {anchor}"


def build_text_bg_prompt(story: dict, page: dict | None = None) -> str:
    """Prompt for a text-page background (long mode).

    One shared background per book by default (pages/text-bg-long.png); a page with
    text_background_prompt set gets its own dedicated background instead. No character
    references are sent for this call, so STYLE_ANCHOR is not used. The style block is
    injected verbatim for book-wide visual consistency.

    Description precedence: page-level text_background_prompt (when page is given) ->
    top-level text_background_prompt -> generic default.
    """
    style = build_style_block(story)
    bg_desc = ""
    if page is not None:
        bg_desc = page.get("text_background_prompt", "").rstrip(". ")
    if not bg_desc:
        bg_desc = story.get("text_background_prompt", "").rstrip(". ")
    if not bg_desc:
        bg_desc = "A plain background for the book's text pages"
    return (
        f"{bg_desc}. "
        "Render a low-detail, full-bleed background in the book's art style — "
        "no characters, no faces, no lettering, no typography anywhere in the image. "
        "Use the full canvas from edge to edge, and reserve a large, especially "
        "low-detail, lightly-toned central area where story text will be placed. "
        f"Art style: {style}. "
        "Keep this exact style identical on every page of the book."
    )


def resolve_story_rel(path_str: str, base_dir: Path) -> Path:
    """Resolve a story-data path: absolute as-is, relative against the story.json
    directory (base_dir). Mirrors edit_story.py's resolve_story_rel so the paid
    scripts and the editor agree on relative-path semantics — a relative
    `ref_image`/`style_sheet` means "relative to story.json", not to cwd.
    Keep in sync with the copy in make_style_sheet.py.
    """
    p = Path(path_str)
    return p if p.is_absolute() else base_dir / p


def _ref_photos(entry: dict, base_dir: Path) -> list[str]:
    """This cast entry's own reference photo paths, in order, existing only.

    Mirrors collect_ref_images_for_entry() in make_style_sheet.py (the two skills share
    no module): normalize a string-or-list `ref_image` -> dedup keeping order ->
    resolve each against base_dir (the story.json dir) -> drop missing files. No cap
    here; the caller's per-model budget governs. Returns resolved (absolute) path
    strings so byte-reads and the audit log work from any cwd.
    """
    raw = entry.get("ref_image")
    if isinstance(raw, str):
        refs = [raw]
    elif isinstance(raw, list):
        refs = raw
    else:
        refs = []
    ordered: list[str] = []
    seen: set[str] = set()
    for r in refs:
        r = r.strip() if isinstance(r, str) else ""
        if r and r not in seen:
            seen.add(r)
            ordered.append(r)
    out: list[str] = []
    for r in ordered:
        resolved = resolve_story_rel(r, base_dir)
        if resolved.exists():
            out.append(str(resolved))
    return out


def collect_input_images(
    story: dict, page: dict, log: list[str] | None = None,
    base_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """Build the full prioritized reference-image list for one page render call.

    Returns ALL candidates in priority order — no cap applied. The caller
    (run_nano_banana via select_refs) decides the effective cap after optionally
    auto-upgrading the model.

    base_dir is the story.json directory; relative `style_sheet`/`ref_image` paths
    resolve against it (absolute paths pass through). Defaults to cwd when omitted
    (the historical assumption — kept so callers passing absolute paths are
    unaffected); main() always passes story_path.parent.

    page['cast'] is ONE flat name list of mixed kinds (names must match
    story['cast'][].name exactly). Contribution by kind:
      character — its style sheet only. The HERO (first character-kind entry in
                  page order) still leads the ref-ordering into the cap, but no
                  longer contributes a photo. CONVENTION: author the hero/child first.
      object    — its reference sheet; falls back to its first ref_image photo
                  when no sheet exists.
      location  — its reference sheet (Stage 2 generates one for every location,
                  from its real-place photos and/or 'appearance' — PER-50); falls
                  back to its first ref_image photo when no sheet exists yet.

    Priority order: hero sheet → remaining character sheets (page order) →
    object refs (page order) → location refs (page order, lowest priority,
    first to drop when the cap is applied by the caller).

    Returns (label, path) pairs; labels are interleaved identification notes in
    run_nano_banana. Label vocabulary must stay in sync with IMAGE_SYSTEM_PROMPT's
    rules-by-kind.
    """
    def warn(msg: str) -> None:
        if log is not None:
            log.append(f"  Warning: {msg}")
        else:
            print(f"Warning: {msg}", file=sys.stderr)

    if base_dir is None:
        base_dir = Path.cwd()

    cast_index: dict[str, dict] = {
        c.get("id", ""): c for c in story.get("cast", [])
        if isinstance(c, dict) and c.get("id")
    }
    page_cast: list[str] = page.get("cast", [])

    # Partition the page cast by kind, preserving page order within each group.
    # page_cast entries are cast ids (PER-56); display names come from the entry.
    characters: list[tuple[str, dict]] = []
    objects: list[tuple[str, dict]] = []
    locations: list[tuple[str, dict]] = []
    for cid in page_cast:
        entry = cast_index.get(cid)
        if entry is None:
            warn(f"page references unknown cast id {cid!r}; skipping.")
            continue
        kind = (entry.get("kind") or "character").strip() or "character"
        name = entry.get("name") or cid  # display name for labels
        if kind == "object":
            objects.append((name, entry))
        elif kind == "location":
            locations.append((name, entry))
        else:  # character (default)
            characters.append((name, entry))

    # Prioritized (label, path) candidates; trimmed to the cap below.
    candidates: list[tuple[str, str]] = []

    for name, entry in characters:
        sheet = entry.get("style_sheet", "")
        sheet_path = resolve_story_rel(sheet, base_dir) if sheet else None
        if not sheet:
            warn(
                f"character {name!r} has no style_sheet; skipping. "
                "Run make_style_sheet.py first."
            )
        elif not sheet_path.exists():
            warn(f"style sheet for {name!r} not found on disk ({sheet}); skipping.")
        else:
            candidates.append((f"character style sheet for {name}", str(sheet_path)))

    for name, entry in objects:
        sheet = entry.get("style_sheet", "")
        sheet_path = resolve_story_rel(sheet, base_dir) if sheet else None
        if sheet_path and sheet_path.exists():
            candidates.append((f"object reference sheet for {name}", str(sheet_path)))
        else:
            if sheet:
                warn(
                    f"object sheet for {name!r} not found on disk ({sheet}); "
                    f"falling back to photo."
                )
            photos = _ref_photos(entry, base_dir)
            if photos:
                candidates.append(
                    (f"real photograph of the object {name} (appearance reference)", photos[0])
                )
            else:
                warn(
                    f"object {name!r} has neither a usable style_sheet nor a "
                    f"ref_image; skipping. Run make_style_sheet.py first."
                )

    # Location refs — lowest priority, first to drop from the cap.
    for name, entry in locations:
        sheet = entry.get("style_sheet", "")
        sheet_path = resolve_story_rel(sheet, base_dir) if sheet else None
        if sheet_path and sheet_path.exists():
            candidates.append((f"location reference sheet for {name}", str(sheet_path)))
        else:
            if sheet:
                warn(
                    f"location sheet for {name!r} not found on disk ({sheet}); "
                    f"falling back to photo."
                )
            photos = _ref_photos(entry, base_dir)
            if photos:
                candidates.append(
                    (f"real photograph of the location {name} (setting reference)", photos[0])
                )
            else:
                warn(
                    f"location {name!r} has neither a style_sheet nor a "
                    f"ref_image; skipping."
                )

    return candidates


def missing_character_sheets(
    story: dict, page: dict, base_dir: Path | None = None,
) -> list[tuple[str, str, str]]:
    """kind=character cast ids on this page whose style_sheet is absent or not on disk.

    PER-69 hard gate: a character MUST have a usable style sheet to render — its sheet
    defines the canonical design/outfit, and there is no legitimate fallback (objects
    and locations keep their real-photo fallback, so they are NOT checked here).

    Returns (id, display_name, sheet_str) tuples; sheet_str is "" when no style_sheet
    field is set, else the raw (unresolved) path for the error message. base_dir is the
    story.json dir for relative-path resolution (defaults to cwd, matching
    collect_input_images).
    """
    if base_dir is None:
        base_dir = Path.cwd()
    cast_index: dict[str, dict] = {
        c.get("id", ""): c for c in story.get("cast", [])
        if isinstance(c, dict) and c.get("id")
    }
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for cid in page.get("cast", []):
        entry = cast_index.get(cid)
        if entry is None:
            continue  # unknown id is handled (warned) in collect_input_images
        kind = (entry.get("kind") or "character").strip() or "character"
        if kind != "character":
            continue
        sheet = entry.get("style_sheet", "")
        if not (sheet and resolve_story_rel(sheet, base_dir).exists()):
            if cid not in seen:
                seen.add(cid)
                out.append((cid, entry.get("name") or cid, sheet))
    return out


def select_refs(
    candidates: list[tuple[str, str]], model: str,
) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Apply the per-model ref cap, auto-upgrading flash → pro when candidates
    exceed the flash cap (runtime-only; story.json is never modified).

    If the effective model is flash and the candidate list exceeds the flash cap (4),
    the model is silently promoted to gemini-3-pro-image before the cap is applied.
    The caller is responsible for logging the upgrade.

    Returns (effective_model, selected_pairs, dropped_labels).
    """
    flash_cap = MODEL_MAX_INPUT_IMAGES[FLASH_IMAGE_MODEL]
    if model == FLASH_IMAGE_MODEL and len(candidates) > flash_cap:
        model = PRO_IMAGE_MODEL
    cap = MODEL_MAX_INPUT_IMAGES.get(model, MAX_INPUT_IMAGES)
    return model, candidates[:cap], [label for label, _ in candidates[cap:]]


def _retry_delay(attempt: int, exc: Exception) -> float:
    """Backoff for the given attempt (0-indexed). Honors a Retry-After header if present."""
    retry_after = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw = headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
    if retry_after is not None:
        return min(retry_after, BACKOFF_MAX_SECONDS)
    # Exponential backoff with full jitter.
    capped = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
    return random.uniform(0, capped)


def _extract_image_bytes(response) -> bytes | None:
    """Safely pull the first inline image from a Gemini response.

    A 200 response may carry no image part — empty/None ``candidates``,
    ``content`` or ``parts`` (safety/recitation block, empty completion,
    finish_reason != STOP). Return None in every such case instead of letting
    a ``NoneType is not iterable`` crash take down the whole concurrent render.
    """
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "inline_data", None) is not None:
                return part.inline_data.data
    return None


def _empty_response_reason(response) -> str:
    """Best-effort human reason for why a 200 response carried no image."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        blocked = getattr(feedback, "block_reason", None)
        return f"blocked: {blocked}" if blocked else "no candidates returned"
    finish = getattr(candidates[0], "finish_reason", None)
    return f"finish_reason={finish}" if finish else "no image parts"


class _LazyClient:
    """Builds the genai.Client on first paid API call.

    Allows runs that only rebuild free artifacts (e.g. a missing text page in long mode,
    or an overlay composite when the raw already exists) to complete without a
    GEMINI_API_KEY in the environment, honoring the repo's idempotency contract:
    'regen free artifacts for free'.

    When composite_only=True, any would-be paid Gemini call returns False immediately
    with a clear error message — guaranteeing zero API spend.
    """

    def __init__(self, composite_only: bool = False) -> None:
        self._client = None
        self.composite_only = composite_only

    def get(self):
        if self._client is None:
            from google import genai as _genai
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                print(
                    "ERROR: GEMINI_API_KEY is not set in the environment.",
                    file=sys.stderr,
                )
                sys.exit(1)
            self._client = _genai.Client(api_key=api_key)
        return self._client


def _ensure_png(data: bytes) -> bytes:
    """Transcode image bytes to PNG when they aren't already.

    The Gemini API may return JPEG inline data; every downstream consumer
    (merge_epub.py's IHDR parser, the EPUB image/png media-type, the .png file
    contract) requires real PNG bytes, so convert at the save site.
    Keep in sync with the copy in make_style_sheet.py.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).save(buf, format="PNG")
    return buf.getvalue()


def append_api_log(
    log_path: Path,
    *,
    script: str,
    target: Path,
    model: str,
    resolution: str,
    aspect_ratio: str | None,
    response_modalities: list[str],
    system_instruction: str,
    contents_desc: list[tuple],  # ("text", str) | ("image", path_str, mime, n_bytes)
) -> None:
    """Append one human-readable entry describing an outgoing Gemini request.

    Audit log of exactly what is sent: full config, full system instruction,
    full prompt, and per-reference-image metadata (never raw bytes), in
    contents order. Append-only; grows across regenerates by design.
    Best-effort — a logging failure must never fail a paid render.
    Single os.write to an O_APPEND fd: atomic across concurrent processes
    (editor regenerates spawn one render_book.py process per page).
    Keep in sync with the copy in make_style_sheet.py.
    """
    try:
        lines: list[str] = [
            "=" * 78,
            f"{datetime.now().astimezone().isoformat(timespec='seconds')}  {script}  ->  {target.name}",
            f"target: {target}",
            f"model: {model}",
            f"resolution: {resolution}",
            f"aspect_ratio: {aspect_ratio or '(unset - model chooses)'}",
            f"response_modalities: {', '.join(response_modalities)}",
            "",
            "--- system_instruction ---",
            system_instruction,
            "",
        ]
        for i, item in enumerate(contents_desc):
            if item[0] == "text":
                head = "prompt" if i == 0 else "text"
                lines += [f"--- contents[{i}]: {head} ---", item[1], ""]
            else:
                _, src, mime, n = item
                lines += [
                    f"--- contents[{i}]: image ---",
                    f"source: {src}",
                    f"mime: {mime}",
                    f"bytes: {n}",
                    "",
                ]
        entry = ("\n".join(lines) + "\n").encode("utf-8")
        fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, entry)  # single write: atomic on O_APPEND regular files
        finally:
            os.close(fd)
    except Exception as e:
        print(f"WARNING: failed to write API log {log_path}: {e}", file=sys.stderr)


async def run_nano_banana(
    client: _LazyClient,
    prompt: str,
    raw_path: Path,
    story: dict,
    page: dict,
    resolution: str,
    log: list[str],
    aspect_ratio: str | None = None,
    model: str = IMAGE_MODEL,
    base_dir: Path | None = None,
) -> bool:
    """Generate one illustration via the Gemini API and write it to raw_path.

    base_dir is the story.json directory, used to resolve relative
    `style_sheet`/`ref_image` paths; passed through to collect_input_images.
    """
    # --composite-only guard: refuse any paid call, return False so the run continues.
    # The caller checks the return value; the run exits 1 via the "N page(s) failed" path.
    if client.composite_only:
        log.append(
            f"  ERROR: {raw_path.name} requires a paid Gemini call, but --composite-only "
            "is set. Delete only the composite output (not the raw/art file) and re-run, "
            "or run without --composite-only to generate the image."
        )
        return False

    # PER-69 hard gate: every character on this page must have a usable style sheet.
    # Characters have no legitimate fallback (their sheet defines the canonical
    # design/outfit); objects/locations keep their photo fallback and are not checked.
    # Fail the page with an actionable error and make NO paid call. Pages with an
    # empty cast (e.g. the shared text background) pass trivially.
    missing = missing_character_sheets(story, page, base_dir)
    if missing:
        for cid, name, sheet in missing:
            where = f"not found on disk ({sheet})" if sheet else "no style_sheet set"
            log.append(
                f"  ERROR: character {name!r} (id={cid}) has no usable style sheet: {where}."
            )
        log.append(
            "  Run make_style_sheet.py to generate character sheets before rendering "
            "(characters require a sheet; objects/locations may fall back to a photo)."
        )
        return False

    from google.genai import errors, types

    # Build contents: text prompt, then for each reference image a short
    # identification note followed by the image Part. The note tells the model
    # what the next image IS (sheet vs photograph, per cast kind);
    # the behavioural rules for each kind live in IMAGE_SYSTEM_PROMPT.
    contents: list = [prompt]
    contents_desc: list[tuple] = [("text", prompt)]  # mirrors contents for audit log
    candidates = collect_input_images(story, page, log, base_dir=base_dir)
    new_model, ref_pairs, dropped = select_refs(candidates, model)
    if new_model != model:
        log.append(
            f"  Note: auto-upgraded page {page.get('page_num', '?')} to {new_model} "
            f"({len(candidates)} refs > flash cap {MODEL_MAX_INPUT_IMAGES[FLASH_IMAGE_MODEL]})"
        )
        model = new_model
    if dropped:
        log.append(
            f"  Note: cap ({MODEL_MAX_INPUT_IMAGES.get(model, MAX_INPUT_IMAGES)}) reached; "
            f"dropped: {', '.join(dropped)}"
        )
    for label, img_path in ref_pairs:
        p = Path(img_path)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        data = p.read_bytes()
        contents.append(f"Next image: {label}.")
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
        contents_desc.append(("text", f"Next image: {label}."))
        contents_desc.append(("image", str(p), mime, len(data)))
    if ref_pairs:
        log.append(
            f"  Refs: {'; '.join(label for label, _ in ref_pairs)}"
        )

    response_modalities = ["TEXT", "IMAGE"]
    config = types.GenerateContentConfig(
        system_instruction=IMAGE_SYSTEM_PROMPT,
        response_modalities=response_modalities,
        image_config=types.ImageConfig(image_size=resolution, aspect_ratio=aspect_ratio),
    )

    log.append(f"  Generating: {raw_path.name} ({model})")
    # Build the genai client lazily — only on first actual paid call, so runs that
    # only rebuild free artifacts (text pages in long mode) need no GEMINI_API_KEY.
    genai_client = client.get()
    # Audit log: every paid target lives directly inside pages_dir = out_dir/pages,
    # so out_dir/log.txt is raw_path.parent.parent / "log.txt".
    append_api_log(
        raw_path.parent.parent / "log.txt",
        script="render_book.py",
        target=raw_path,
        model=model,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        response_modalities=response_modalities,
        system_instruction=IMAGE_SYSTEM_PROMPT,
        contents_desc=contents_desc,
    )
    image_data: bytes | None = None
    for attempt in range(MAX_RETRIES):
        last_exc: Exception
        try:
            response = await genai_client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except errors.APIError as e:
            if e.code != 429 and e.code < 500:
                log.append(f"  ERROR: image API request failed ({e.code}): {e}")
                return False
            last_exc = e
        except Exception as e:
            log.append(f"  ERROR: image API request failed: {e}")
            return False
        else:
            # 200 response — but it may carry no image part (safety/recitation
            # block, empty completion, finish_reason != STOP). Treat an empty
            # response as a transient, retryable condition rather than crashing
            # on None parts (PER-64).
            image_data = _extract_image_bytes(response)
            if image_data is not None:
                break
            reason = _empty_response_reason(response)
            if attempt == MAX_RETRIES - 1:
                log.append(f"  ERROR: API returned no image after {MAX_RETRIES} attempts ({reason}).")
                return False
            delay = _retry_delay(attempt, None)
            log.append(f"  Retry {attempt + 1}/{MAX_RETRIES - 1} after empty response ({reason}); waiting {delay:.1f}s...")
            await asyncio.sleep(delay)
            continue

        if attempt == MAX_RETRIES - 1:
            log.append(f"  ERROR: image API request failed after {MAX_RETRIES} attempts: {last_exc}")
            return False
        delay = _retry_delay(attempt, last_exc)
        log.append(f"  Retry {attempt + 1}/{MAX_RETRIES - 1} after transient error; waiting {delay:.1f}s...")
        await asyncio.sleep(delay)

    if not image_data:
        log.append("  ERROR: no images returned by the API.")
        return False

    try:
        raw_path.write_bytes(_ensure_png(image_data))
    except Exception as e:
        log.append(f"  ERROR: failed to write image: {e}")
        return False
    return True


async def run_overlay(
    raw_path: Path, text: str, placement: str, color: str, font: str,
    font_name: str | None, align: str, final_path: Path, log: list[str]
) -> bool:
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(raw_path),
        "--text", text,
        "--placement", placement,
        "--out", str(final_path),
        "--color", color,
        "--font", font,
        "--align", align,
    ]
    if font_name:
        cmd += ["--font-name", font_name]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        if stdout:
            log.append(stdout.decode().rstrip())
        if stderr:
            log.append(stderr.decode().rstrip())
    return proc.returncode == 0


async def run_text_page(
    image_path: Path,
    text: str,
    color: str,
    font: str,
    font_name: str | None,
    align: str,
    out_path: Path,
    canvas_from: Path | None,
    log: list[str],
) -> bool:
    """Shell overlay_text.py in text-page mode (long story mode body pages). No API cost."""
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(image_path),
        "--text", text,
        "--text-page",
        "--out", str(out_path),
        "--color", color,
        "--font", font,
        "--align", align,
    ]
    if font_name:
        cmd += ["--font-name", font_name]
    if canvas_from is not None:
        cmd += ["--canvas-from", str(canvas_from)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        if stdout:
            log.append(stdout.decode().rstrip())
        if stderr:
            log.append(stderr.decode().rstrip())
    return proc.returncode == 0


async def render_page(client, page: dict, story: dict, pages_dir: Path, resolution: str, cli_text_mode: str | None = None, aspect_ratio: str | None = None, cli_model: str | None = None, base_dir: Path | None = None) -> bool:
    """Render one page (nano-banana + optional overlay/text-page). Prints its own log atomically. Page-independent."""
    page_num = page["page_num"]
    # Resolve model once: CLI override > page field > story field > default flash.
    model = resolve_model(story, page, cli_model)
    # Resolve text mode once: CLI override > page field > story field > "native".
    text_mode = resolve_text_mode(story, page, cli_text_mode)
    log: list[str] = [f"=== Page {page_num} (text-mode: {text_mode}) ==="]
    nn = f"{page_num:02d}"

    # ---- Long story mode --------------------------------------------------------
    if text_mode == "long":
        is_cover = page_num == 1

        if is_cover:
            # Cover: single combined page rendered overlay-style (title on art, no split).
            final_path = pages_dir / "page-01-long.png"
            if not final_path.exists():
                raw_path = pages_dir / "raw-page-01-long.png"
                if not raw_path.exists():
                    prompt = build_image_prompt(page, story, "overlay")
                    ok = await run_nano_banana(client, prompt, raw_path, story, page, resolution, log, aspect_ratio, model=model, base_dir=base_dir)
                    if not ok or not raw_path.exists():
                        log.append("  ERROR: image generation failed for cover")
                        print("\n" + "\n".join(log))
                        return False
                text = page.get("text", "")
                placement = _overlay_placement(page.get("text_placement", "floating"))
                color = page.get("text_color_hint", "dark")
                font = page.get("font", "reader")
                font_name = (story.get("fonts") or {}).get(font)
                align = page.get("text_align", "left")
                ok = await run_overlay(raw_path, text, placement, color, font, font_name, align, final_path, log)
                if not ok or not final_path.exists():
                    log.append("  ERROR: text overlay failed for cover")
                    print("\n" + "\n".join(log))
                    return False
            log.append(f"  Done: {final_path}")
            log.append(f"MEDIA: {final_path}")
            print("\n" + "\n".join(log))
            return True

        # Body page: art page + optional text page.
        art_path = pages_dir / f"page-{nn}-long.png"
        if not art_path.exists():
            prompt = build_image_prompt(page, story, "long")
            ok = await run_nano_banana(client, prompt, art_path, story, page, resolution, log, aspect_ratio, model=model, base_dir=base_dir)
            if not ok or not art_path.exists():
                log.append(f"  ERROR: art image generation failed for page {page_num}")
                print("\n" + "\n".join(log))
                return False

        page_text = page.get("text", "").strip()
        if not page_text:
            text_bg_prompt = page.get("text_background_prompt", "")
            if text_bg_prompt:
                log.append(f"  Warning: text_background_prompt set but text is empty; skipping text page.")
            log.append(f"  Done: {art_path}")
            log.append(f"MEDIA: {art_path}")
            print("\n" + "\n".join(log))
            return True

        # Shared text-page rendering args.
        color = page.get("text_color_hint", "dark")
        font = page.get("font", "reader")
        font_name = (story.get("fonts") or {}).get(font)
        align = page.get("text_align", "left")
        text_path = pages_dir / f"page-{nn}-long-text.png"

        if not text_path.exists():
            if page.get("text_background_prompt", ""):
                # Per-page override: dedicated background (+1 paid call, idempotent on bg_path).
                bg_path = pages_dir / f"page-{nn}-long-bg.png"
                if not bg_path.exists():
                    bg_prompt_str = build_text_bg_prompt(story, page)
                    ok = await run_nano_banana(
                        client, bg_prompt_str, bg_path, story, {"cast": []},
                        resolution, log, aspect_ratio, model=model, base_dir=base_dir,
                    )
                    if not ok or not bg_path.exists():
                        log.append(f"  ERROR: text bg generation failed for page {page_num}")
                        print("\n" + "\n".join(log))
                        return False
            else:
                # Default: the book-wide shared background, generated once in render_all.
                bg_path = pages_dir / SHARED_TEXT_BG_NAME
                if not bg_path.exists():
                    log.append(f"  ERROR: shared text-page background missing ({bg_path}); its generation failed earlier — re-run to retry")
                    print("\n" + "\n".join(log))
                    return False
            # bg_path is the source image; canvas_from=art_path locks the dims.
            ok = await run_text_page(bg_path, page_text, color, font, font_name, align, text_path, art_path, log)
            if not ok or not text_path.exists():
                log.append(f"  ERROR: text page failed for page {page_num}")
                print("\n" + "\n".join(log))
                return False

        log.append(f"  Done: {art_path}, {text_path}")
        log.append(f"MEDIA: {art_path}")
        log.append(f"MEDIA: {text_path}")
        print("\n" + "\n".join(log))
        return True

    # ---- Native / overlay modes -------------------------------------------------
    suffix = "-native" if text_mode == "native" else ""
    final_path = pages_dir / f"page-{nn}{suffix}.png"

    prompt = build_image_prompt(page, story, text_mode)

    if text_mode == "native":
        # In native mode the model bakes text into the illustration — write directly
        # to final_path; no separate raw file needed.
        ok = await run_nano_banana(client, prompt, final_path, story, page, resolution, log, aspect_ratio, model=model, base_dir=base_dir)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False
    else:
        raw_path = pages_dir / f"raw-page-{nn}.png"
        # Generate only when the raw doesn't already exist — mirrors the long-cover guard
        # at :762.  Deleting page-NN.png alone (without its raw) triggers a free
        # re-composite; deleting both triggers a paid re-render.
        if not raw_path.exists():
            ok = await run_nano_banana(client, prompt, raw_path, story, page, resolution, log, aspect_ratio, model=model, base_dir=base_dir)
            if not ok or not raw_path.exists():
                log.append(f"  ERROR: image generation failed for page {page_num}")
                print("\n" + "\n".join(log))
                return False

        text = page.get("text", "")
        placement = _overlay_placement(page.get("text_placement", "floating"))
        color = page.get("text_color_hint", "dark")
        font = page.get("font", "reader")
        # Book-wide role -> family-name map; the resolved name (if any) overrides the
        # bundled role font. Omitted/unknown role -> None -> bundled font used.
        font_name = (story.get("fonts") or {}).get(font)
        align = page.get("text_align", "left")

        ok = await run_overlay(raw_path, text, placement, color, font, font_name, align, final_path, log)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: text overlay failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False

    log.append(f"  Done: {final_path}")
    log.append(f"MEDIA: {final_path}")
    print("\n" + "\n".join(log))
    return True


async def render_all(todo: list[dict], story: dict, pages_dir: Path, resolution: str, cli_text_mode: str | None = None, aspect_ratio: str | None = None, cli_model: str | None = None, composite_only: bool = False, base_dir: Path | None = None) -> int:
    """Fire every page concurrently. Returns the number of failures.

    The genai.Client is built lazily on the first actual paid API call via _LazyClient,
    so runs that only rebuild free artifacts (e.g. text pages in long mode, overlay
    composites when the raw already exists) need no GEMINI_API_KEY.

    When composite_only=True, any page that would require a paid Gemini call fails with
    a clear error; free pages (existing raw/art/bg → overlay only) succeed normally.
    """
    client = _LazyClient(composite_only=composite_only)

    # Long mode: the shared text-page background is one per book — generate it once,
    # sequentially, BEFORE the pages fire (pages only consume it; generating it inside
    # render_page would race across concurrent pages). Only when some todo page will
    # actually need it: a body (non-cover) page whose effective mode is long, with text,
    # no per-page bg override, and text page not yet on disk.
    long_todo = [p for p in todo if resolve_text_mode(story, p, cli_text_mode) == "long"]
    if long_todo:
        shared_bg = pages_dir / SHARED_TEXT_BG_NAME
        needs_shared_bg = not shared_bg.exists() and any(
            p["page_num"] != 1
            and p.get("text", "").strip()
            and not p.get("text_background_prompt", "")
            and not (pages_dir / f"page-{p['page_num']:02d}-long-text.png").exists()
            for p in long_todo
        )
        if needs_shared_bg:
            log = ["=== Shared text-page background ==="]
            prompt = build_text_bg_prompt(story)
            # Shared bg: use book-level model resolution (no page in scope).
            bg_model = resolve_model(story, None, cli_model)
            ok = await run_nano_banana(
                client, prompt, shared_bg, story, {"cast": []},
                resolution, log, aspect_ratio, model=bg_model, base_dir=base_dir,
            )
            if ok and shared_bg.exists():
                log.append(f"  Done: {shared_bg}")
                log.append(f"MEDIA: {shared_bg}")
            else:
                # Don't abort: art pages can still render; their text pages will fail
                # with a clear message, and a re-run retries the bg cheaply.
                log.append("  ERROR: shared text-page background generation failed; text pages will fail this run")
            print("\n" + "\n".join(log))

    modes = sorted({resolve_text_mode(story, p, cli_text_mode) for p in todo}) if todo else [resolve_text_mode(story, None, cli_text_mode)]
    print(f"\nRendering {len(todo)} page(s) concurrently ({', '.join(modes)} mode(s))...")
    results = await asyncio.gather(
        *(render_page(client, page, story, pages_dir, resolution, cli_text_mode, aspect_ratio, cli_model=cli_model, base_dir=base_dir) for page in todo)
    )
    return sum(1 for ok in results if not ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all pages of a storybook.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    parser.add_argument("--resolution", choices=["1K", "2K", "4K"], default=None,
                        help="Override the resolution from story.json (default: story.json 'resolution' field, or 2K if not set)")
    parser.add_argument(
        "--aspect-ratio",
        choices=["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        default=None,
        dest="aspect_ratio",
        help=(
            "Override the aspect ratio from story.json "
            "(default: story.json 'aspect_ratio' field, or unset — model chooses)."
        ),
    )
    parser.add_argument(
        "--model",
        choices=IMAGE_MODELS,
        default=None,
        help=(
            "Override the image model for every page this run "
            "(default: per-page 'model' field, then story.json top-level 'model', "
            "then gemini-3.1-flash-image). Page renders only; style sheets always use "
            "gemini-3-pro-image regardless of this setting."
        ),
    )
    parser.add_argument("--from", dest="from_page", type=int, default=1,
                        help="Start from this page number (1-indexed)")
    parser.add_argument("--only", dest="only_page", type=int, default=None,
                        help="Render only this page number")
    parser.add_argument(
        "--text-mode",
        dest="text_mode",
        choices=["overlay", "native", "long"],
        default=None,
        help=(
            "Override story.json's text_mode for this run. "
            "overlay: generate image with text-safe zone, then Pillow-composite text. "
            "native: ask the model to render story text directly into the illustration "
            "(output goes to page-NN-native.png). "
            "long: full-bleed art + separate text page per body page (page-NN-long.png + "
            "page-NN-long-text.png); cover stays combined (overlay-style). Text pages "
            "share one model-generated background per book (text-bg-long.png, +1 paid "
            "call total); a page with text_background_prompt gets a dedicated bg instead "
            "(+1 call for that page). "
            "If omitted, each page uses its own 'text_mode' field (if set), then "
            "story.json's top-level 'text_mode', then 'native' as the built-in default. "
            "This flag overrides all page-level and book-level fields for the entire run."
        ),
    )
    parser.add_argument(
        "--composite-only",
        dest="composite_only",
        action="store_true",
        default=False,
        help=(
            "Abort instead of making any paid Gemini call; only rebuild free Pillow "
            "composites (overlay text panels, long-mode text pages, long cover) from "
            "existing raw/art/bg files. Needs no GEMINI_API_KEY. "
            "Pages that would require a new image are reported as failures (exit 1) "
            "with a message naming the missing prerequisite file."
        ),
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)
    reject_legacy_keys(story)
    require_cast_ids(story)
    require_style_guide(story)

    # CLI flag > story.json field > built-in default (2K).
    resolution = args.resolution or story.get("resolution") or "2K"
    # CLI flag > story.json field > unset (model chooses framing).
    aspect_ratio = args.aspect_ratio or story.get("aspect_ratio") or None
    # text_mode: resolved per page via resolve_text_mode(story, page, args.text_mode).
    # CLI --text-mode overrides all pages; page field > story field > "native" otherwise.

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json", file=sys.stderr)
        sys.exit(1)

    # Warn if no style sheets have been generated yet (every cast entry is sheetable).
    cast = story.get("cast", [])
    if cast and not any(c.get("style_sheet") for c in cast):
        print("Warning: no style_sheet paths found in story.json's cast.")
        print("Run make_style_sheet.py first for better consistency.")
        print()

    # Select pages to render, skipping filtered-out and already-existing ones.
    # Text mode is resolved per page: CLI --text-mode > page field > story field > "native".
    # Long mode uses its own skip logic (two physical files per logical page);
    # native/overlay map 1-to-1 via a filename suffix.
    todo: list[dict] = []
    for page in pages:
        page_num = page["page_num"]
        if args.only_page is not None and page_num != args.only_page:
            continue
        if page_num < args.from_page:
            continue

        page_mode = resolve_text_mode(story, page, args.text_mode)
        if page_mode == "long":
            is_cover = page_num == 1
            art_path = pages_dir / ("page-01-long.png" if is_cover else f"page-{page_num:02d}-long.png")
            if is_cover:
                if art_path.exists():
                    print(f"Page {page_num} (cover): already exists, skipping. ({art_path})")
                    print(f"MEDIA: {art_path}")
                    continue
            else:
                page_text = page.get("text", "").strip()
                text_path = pages_dir / f"page-{page_num:02d}-long-text.png"
                art_done = art_path.exists()
                text_done = not page_text or text_path.exists()
                if art_done and text_done:
                    print(f"Page {page_num}: already exists, skipping. ({art_path})")
                    print(f"MEDIA: {art_path}")
                    if page_text and text_path.exists():
                        print(f"MEDIA: {text_path}")
                    continue
        else:
            suffix = "-native" if page_mode == "native" else ""
            final_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
            if final_path.exists():
                print(f"Page {page_num}: already exists, skipping. ({final_path})")
                print(f"MEDIA: {final_path}")
                continue

        todo.append(page)

    errors = asyncio.run(render_all(todo, story, pages_dir, resolution, cli_text_mode=args.text_mode, aspect_ratio=aspect_ratio, cli_model=args.model, composite_only=args.composite_only, base_dir=story_path.parent)) if todo else 0

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)

    if args.only_page is None:
        print(
            "Next: assemble the book with storybook-consolidate "
            "(merge_pdf.py / merge_epub.py) — free, no API cost."
        )


if __name__ == "__main__":
    main()
