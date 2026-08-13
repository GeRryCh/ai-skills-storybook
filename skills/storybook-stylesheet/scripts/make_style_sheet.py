#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.40",
#     "Pillow",
# ]
# ///
"""
Generate per-cast-entry reference sheets for a storybook.

Reads story.json, calls the OpenAI gpt-image-2 image API (images.edit) once per
eligible cast entry to produce individual PNGs (style-sheet-{slug}.png), then
writes each entry's style_sheet path back into story.json.

gpt-image-2 (edit endpoint) is the style-sheet standard: it processes every
reference photo at high fidelity for stronger likeness than Gemini. A baked-in
"flat 2D cartoon, not a photo" style directive (STYLE_BOOST_*) counters the edit
endpoint's photoreal bias so sheets stay in the book's illustration style.
NOTE: render_book.py (Stage 3) still uses Gemini — a deliberate split.

Eligible entries (every cast entry gets a sheet):
  kind=character — outfit-locked, face/hair likeness from ref_image photos.
  kind=object    — multi-angle, distinguishing features.
  kind=location  — generated from the entry's real-place ref_image photo(s)
                   (downloaded in Stage 1, PER-50) and/or 'appearance'. The
                   sheet is the render reference; the raw photo remains the
                   render-time fallback when no sheet exists.

Idempotent: skips entries whose style-sheet-{slug}.png already exists. To force
a regenerate for one entry, delete that entry's file and re-run.

Also generates ONE book-wide style-frame.png (PER-82, "Lever B") — an abstract
style board (palette swatches, a line/texture sample, a lighting study; no
characters, no places, no scenery) written to the top-level 'style_frame' field.
render_book.py sends it as the lowest-priority reference on every page call to
anchor the look of everything that isn't cast. Idempotent (skips if it already
exists). Skipped under --only (a full run generates it) — --only's contract is
exactly one top-level diff (that entry's style_sheet), which the editor's
per-cast regenerate flow depends on to resync safely without a full reload.

Requires STORYBOOK_SKILL_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment.

Usage:
  uv run make_style_sheet.py --story /path/to/story.json [--out-dir /path/to/outdir]
                              [--resolution 1K|2K|4K] [--aspect-ratio RATIO]
                              [--only NAME]
"""

from __future__ import annotations
import argparse
from datetime import datetime
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

# OpenAI image-generation config (gpt-image-2, images.edit endpoint).
IMAGE_MODEL = "gpt-image-2"
MAX_INPUT_IMAGES = 5  # cap reference photos per call (per-character refs are few)
IMAGE_QUALITY = "medium"  # validated look for these sheets; "high" is slower/costlier
IMAGE_MODERATION = "low"  # reduce false-refusals; gpt-image-2 did not block child faces

# Per-1M-token USD rates (PER-35). Both Gemini image models are listed even though this
# script only ever bills gpt-image-2 — keep the table identical to render_book.py's copy
# (script-authoring.md keep-in-sync list) so the two files never drift on a shared price.
# Source: OpenAI + Google's published per-1M-token image-generation pricing.
PRICING = {
    "gemini-3.1-flash-image": {"input": 0.50, "output_text": 3.00, "output_image": 60.00},
    "gemini-3-pro-image": {"input": 2.00, "output_text": 12.00, "output_image": 120.00},
    "gpt-image-2": {"text_input": 5.00, "image_input": 8.00, "output_image": 30.00},
}
PRICING_AS_OF = "2026-08-13"

# Cost records for every API response received this run (module-level: single-process,
# single-threaded main loop — safe to accumulate via plain list.append). Summarized at
# the end of main() for the "Cost this run" stdout line; the full out_dir/costs.jsonl
# ledger (this run's records plus every prior run's) backs the "Book total" line.
_RUN_COST_RECORDS: list[dict] = []

# gpt-image-2 takes a pixel `size`, not an aspect enum. Map the book aspect_ratio.
_PORTRAIT_RATIOS = {"2:3", "3:4", "4:5", "9:16"}
_LANDSCAPE_RATIOS = {"3:2", "4:3", "5:4", "16:9", "21:9"}


def aspect_to_size(aspect: str | None) -> str:
    if not aspect:
        return "auto"
    a = aspect.strip()
    if a == "1:1":
        return "1024x1024"
    if a in _PORTRAIT_RATIOS:
        return "1024x1536"
    if a in _LANDSCAPE_RATIOS:
        return "1536x1024"
    return "auto"


def get_api_key() -> str | None:
    """OpenAI key: project-specific var first, then the standard OPENAI_API_KEY."""
    return os.environ.get("STORYBOOK_SKILL_OPENAI_API_KEY") or os.environ.get(
        "OPENAI_API_KEY"
    )


# Baked-in anti-photoreal directive. The images.edit endpoint forces input_fidelity HIGH
# and hugs the reference photos, which biases the output toward photographic rendering.
# This front- and back-loads a hard "original illustration, NOT a photo" instruction so
# sheets stay in the book's defined art style while preserving the photo's identity cues.
STYLE_BOOST_HEAD = (
    "Render this as an original illustration in the book's defined art style — NOT a "
    "photograph. Treat any attached photographs ONLY as a reference for identity and "
    "structure (a person's face shape, features, hair; an object's shape; a place's "
    "architecture): copy that identity, then REDRAW the whole subject from scratch in "
    "the book's art style. Do not paste, trace, or reproduce photographic detail, "
    "texture, or lighting.\n\n"
)
STYLE_BOOST_TAIL = (
    "\n\nFINAL REMINDER: the output is an original illustration in the book's art style "
    "— never a photograph. Likeness comes through the redrawn artwork, not photographic "
    "rendering."
)

# Per-kind system prompts: common prefix + kind-specific likeness sentence + tail.
_IMAGE_SYSTEM_PROMPT_PREFIX = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. "
)
_IMAGE_SYSTEM_PROMPT_TAIL = "Output only the generated image without additional commentary."
_KIND_LIKENESS = {
    "character": (
        "Any attached reference photographs all depict one real, specific person. "
        "Your illustration must be a faithful, instantly recognisable portrait of "
        "that exact person rendered in the requested art style — never a generic "
        "character merely inspired by the photographs. Take the outfit and styling "
        "from the text prompt, never from the photographs. "
    ),
    "object": (
        "Preserve the subject's recognizable shape, structure, materials, and "
        "distinguishing features from any provided reference photographs, but redraw "
        "the subject as an original illustration in the book's art style — never "
        "reproduce photographic detail. "
    ),
    "location": (
        "Preserve the place's recognizable architecture, landmarks, and geography "
        "from any provided reference photographs, but redraw the place as an original "
        "illustration in the book's art style — never reproduce photographic detail. "
    ),
    # PER-82 (Lever B): not a cast kind, used only for the one book-wide style-frame
    # call. No likeness subject at all — this is an abstract style board, and the
    # likeness clause is replaced with an explicit "no scene" directive instead.
    "style": (
        "This is an abstract style reference board only, not a scene or a page of the "
        "book. It must contain no characters, no people, no animals, no named or "
        "recognisable places, no scenery, and no background environment of any kind — "
        "only palette swatches, a texture/line-treatment sample, and a small lighting "
        "study on a flat neutral background. "
    ),
}


def image_system_prompt(kind: str) -> str:
    """Return the system prompt for a given cast-entry kind (or "style", PER-82's
    book-wide style-frame kind — not a cast kind, see _KIND_LIKENESS)."""
    likeness = _KIND_LIKENESS.get(kind, _KIND_LIKENESS["character"])
    return _IMAGE_SYSTEM_PROMPT_PREFIX + likeness + _IMAGE_SYSTEM_PROMPT_TAIL


# Keep in sync with the copy in render_book.py
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


def load_story(story_path: Path) -> dict:
    with open(story_path) as f:
        return json.load(f)


def save_story(story: dict, story_path: Path) -> None:
    with open(story_path, "w") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)


def get_cast(story: dict) -> list[dict]:
    """Return the explicit cast from story['cast'].

    No prose scraping: an earlier regex heuristic minted phantom characters
    (e.g. a fish 'Deep' from 'deep twilight sky', a second girl 'She' from
    'She holds a rabbit') and poisoned every page. The cast must be authored
    explicitly in story.json as [{name, appearance, kind?, ref_image?}], where
    ref_image is one path or a list of paths mapped to that entry.
    """
    cast = story.get("cast")
    if isinstance(cast, list) and cast:
        return cast
    return []


def char_slug(name: str, used: set[str]) -> str:
    """Filesystem-safe slug from a cast entry name. Dedupes with an index suffix."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "character"
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}-{i}"
        i += 1
    used.add(candidate)
    return candidate


# Keep in sync with the copies in render_book.py
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


# Keep in sync with the copy in render_book.py
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


def build_sheet_prompt(story: dict, entry: dict, has_refs: bool = False) -> str:
    """Prompt for one cast entry's individual reference sheet, branched on kind.

    has_refs: True when this entry has reference photos that will be attached to
    the call. Switches the likeness sentence from a weak conditional ("If
    reference photo(s) are provided...") to an assertive instruction naming the
    concrete subject — the conditional phrasing let the model treat the photos
    as loose style hints and draw a generic person from the appearance prose.
    """
    style = build_style_block(story)
    kind = (entry.get("kind") or "character").strip() or "character"
    name = (entry.get("name") or "").strip()
    appearance = (entry.get("appearance") or "").strip()
    if name and appearance:
        subject = f"{name} ({appearance})"
    elif name:
        subject = name
    else:
        subject = "the main character" if kind == "character" else "the subject"

    if kind == "object":
        return (
            f"Object reference sheet for the book. "
            f"Show this one object only: {subject}. "
            f"Show the object from multiple angles, plus a detail close-up of its most "
            f"distinguishing features, consistent design across the sheet. "
            + (
                f"The attached reference photographs show the real object: {name or 'the subject'}. "
                f"Match its shape, proportions, colours, and distinguishing details from the "
                f"photographs exactly — it must read as the very same object. "
                if has_refs
                else ""
            )
            +
            f"Redraw in the book's art style (do not composite, paste, trace, or "
            f"reproduce the photo itself; no photographic elements). "
            f"Art style: {style}. "
            f"Background must be a single flat, plain, neutral light colour — empty, "
            f"no scenery, no characters, no people. "
            f"No text, no labels, no speech bubbles. "
            f"Clear consistent visual design so this object is recognisable across many pages."
        )
    elif kind == "location":
        return (
            f"Location reference sheet for the book. "
            f"Show this one place only: {subject}. "
            f"Show a wide establishing view and one or two closer views from different "
            f"angles, plus a detail close-up of its most distinguishing features, "
            f"consistent design across the sheet. "
            + (
                # PER-84: Gemini-style style-transfer framing — style ranks above
                # photographic fidelity, while still preserving the place's identity
                # (the documented exception to the no-scenery rule: geography IS the
                # subject on a location sheet).
                f"Transform the attached reference photograph(s) of {name or 'the subject'} "
                f"into the book's art style: preserve the original composition and the "
                f"place's recognisable architecture, landmarks, and geography, but render "
                f"everything fully in the book's illustration style — the art style takes "
                f"priority over photographic fidelity. "
                if has_refs
                else ""
            )
            +
            f"Redraw in the book's art style (do not composite, paste, trace, or "
            f"reproduce the photo itself; no photographic elements). "
            f"Art style: {style}. "
            f"No people and no characters anywhere in the scene. "
            f"No text, no labels, no speech bubbles. "
            f"Clear consistent visual design so this place is recognisable across many pages."
        )
    else:  # character (default)
        return (
            f"Character reference sheet for the book. "
            f"Show this one character only: {subject}. "
            f"The sheet must contain exactly four views: "
            f"(1) full-body front view facing the viewer, "
            f"(2) full-body left profile view, "
            f"(3) full-body right profile view, "
            f"(4) a close-up of the face. "
            f"Same character at the same scale and with identical design in every view. "
            + (
                f"The attached reference photographs all show the same real person: "
                f"{name or 'the main character'}. Draw exactly this person — match the face "
                f"shape, eyes, eyebrows, nose, mouth, skin tone, and hair from the "
                f"photographs as closely as the art style allows. The sheet must be an "
                f"unmistakable portrait of {name or 'this person'}, instantly recognisable "
                f"to people who know them — never a generic character merely inspired by "
                f"the photographs. "
                if has_refs
                else ""
            )
            +
            f"Redraw in the book's art style (do not composite, paste, trace, or "
            f"reproduce the photo itself; no photographic elements). "
            f"Outfit and clothing: use exactly the outfit described above in the character "
            f"description. If no outfit is described, invent one simple, distinctive outfit "
            f"that suits the character and book style. "
            f"IMPORTANT: ignore any clothing or outfit visible in the reference photo(s) — "
            f"the character must wear the same single canonical outfit on every view of this "
            f"sheet and on every page of the book. Never copy an outfit from a photograph. "
            f"Art style: {style}. "
            f"Background must be a single flat, plain, neutral light colour — empty, "
            f"no scenery, no objects, no other characters. "
            f"No text, no labels, no speech bubbles. "
            f"Clear consistent visual design so this character is recognisable across many pages."
        )


def build_style_frame_prompt(story: dict) -> str:
    """Prompt for the ONE book-wide 'style frame' image (PER-82, "Lever B").

    NOT a scene, NOT a page of the book, NOT tied to any story location — it
    depicts no cast member and no environment at all. This is deliberately
    stricter than the ticket's one-line summary ("sample environment + palette
    swatches"): the PER-33 lesson is that any book-wide scenery reference bleeds
    into every page, including pages set elsewhere, so this board contains only
    abstract style samples (palette, texture/line treatment, lighting), never a
    depicted place. It anchors the look of everything that isn't cast —
    backgrounds, crowds, lighting, props — which today is generated from text
    alone and drifts toward the model's world-knowledge default.
    """
    style = build_style_block(story)
    return (
        "Book style reference board — NOT a scene, NOT a page of the book, and NOT "
        "tied to any place in the story. "
        "Show only: a palette swatch strip of 5-8 solid colour blocks, a patch of "
        "representative line/texture work (brushwork, linework, or shading technique "
        "sample), and a small lighting study (a simple sphere or gradient showing how "
        "light and shadow render in this style). "
        f"Art style: {style}. "
        "Absolutely no characters, no people, no animals, no named or recognisable "
        "story location, no scenery, no background environment, and no text, labels, "
        "or captions anywhere in the image. "
        "Flat, plain, neutral background behind the swatches and samples. "
        "This board exists only to document the book's rendering technique, palette, "
        "and line treatment for reference on every page — it is never itself a scene."
    )


def resolve_story_rel(path_str: str, base_dir: Path) -> Path:
    """Resolve a story-data path: absolute as-is, relative against the story.json
    directory (base_dir). Mirrors edit_story.py's resolve_story_rel so the paid
    scripts and the editor agree on relative-path semantics — a relative
    `ref_image`/`style_sheet` means "relative to story.json", not to cwd.
    Keep in sync with the copy in render_book.py.
    """
    p = Path(path_str)
    return p if p.is_absolute() else base_dir / p


def collect_ref_images_for_entry(entry: dict, base_dir: Path) -> list[str]:
    """This cast entry's own reference photos, in order, capped at MAX_INPUT_IMAGES.

    `ref_image` accepts a single path (string) or a list of paths.

    Only photos mapped to THIS entry are used — there is no shared global pool, so
    one entry's reference photo never bleeds into another's sheet. The cast-to-photo
    mapping is fixed in Stage 1 (storybook-story).

    Normalize -> dedup (keep order) -> resolve each against base_dir (the story.json
    dir) -> drop missing files -> cap at MAX_INPUT_IMAGES (5 for pro), logging any
    refs dropped to the cap. Returns resolved (absolute) path strings so byte-reads
    and the audit log work from any cwd.
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
        r = (r or "").strip() if isinstance(r, str) else ""
        if r and r not in seen:
            seen.add(r)
            ordered.append(r)

    existing: list[str] = []
    for r in ordered:
        resolved = resolve_story_rel(r, base_dir)
        if resolved.exists():
            existing.append(str(resolved))
        else:
            print(f"Warning: ref not found, skipping: {r}", file=sys.stderr)

    if len(existing) > MAX_INPUT_IMAGES:
        dropped = existing[MAX_INPUT_IMAGES:]
        name = (entry.get("name") or "entry").strip()
        print(
            f"Warning: {name!r} has {len(existing)} refs; capping at "
            f"{MAX_INPUT_IMAGES} (Gemini reference-image limit). Dropping: {', '.join(dropped)}",
            file=sys.stderr,
        )
    return existing[:MAX_INPUT_IMAGES]


def _ensure_png(data: bytes) -> bytes:
    """Transcode image bytes to PNG when they aren't already.

    The Gemini API may return JPEG inline data; the .png file contract (and
    every downstream consumer) requires real PNG bytes, so convert at the
    save site. Keep in sync with the copy in render_book.py.
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
    Keep in sync with the copy in render_book.py.
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


def openai_call_cost(usage, model: str) -> tuple[float | None, dict, bool]:
    """Compute USD cost from an OpenAI images.edit/generate response's `usage` object.

    Returns (usd, tokens, estimated). `usd` is None when `model` has no PRICING
    entry — never guess a price for an unrecognised model. `estimated` is always
    False here: OpenAI's usage object always separates input text/image tokens
    and reports total output tokens directly, so no inference is ever needed
    (contrast render_book.py's gemini_call_cost, whose modality breakdown is
    optional). Keep in sync with the copy in render_book.py.
    """
    if usage is None:
        return None, {}, False
    details = getattr(usage, "input_tokens_details", None)
    text_in = getattr(details, "text_tokens", 0) or 0
    image_in = getattr(details, "image_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    tokens = {"input_text": text_in, "input_image": image_in, "output": output}
    rates = PRICING.get(model)
    if not rates:
        return None, tokens, False
    usd = (
        text_in * rates["text_input"]
        + image_in * rates["image_input"]
        + output * rates["output_image"]
    ) / 1_000_000
    return round(usd, 6), tokens, False


def append_cost_record(
    ledger_path: Path,
    *,
    script: str,
    vendor: str,
    model: str,
    target: Path,
    ok: bool,
    usd: float | None,
    estimated: bool,
    tokens: dict,
) -> None:
    """Append one cost record to out_dir/costs.jsonl and this run's in-memory tally.

    One record per API response actually received — not per page/sheet. A
    retried Gemini call that came back empty (SAFETY/RECITATION) or blocked
    (PROHIBITED_CONTENT) still spent tokens and gets its own record (`ok=False`);
    only network/HTTP failures that never bound a response are unrecorded (no
    usage data exists for them). Best-effort — a logging failure must never
    fail a paid call. Same atomic single-os.write-to-O_APPEND-fd idiom as
    append_api_log(). Keep in sync with the copy in render_book.py.
    """
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": script,
        "vendor": vendor,
        "model": model,
        "target": target.name,
        "ok": ok,
        "usd": usd,
        "estimated": estimated,
        "pricing_as_of": PRICING_AS_OF,
        "tokens": tokens,
    }
    _RUN_COST_RECORDS.append(record)
    try:
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(ledger_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as e:
        print(f"WARNING: failed to write cost ledger {ledger_path}: {e}", file=sys.stderr)


def read_cost_ledger(ledger_path: Path) -> list[dict]:
    """Read costs.jsonl into a list of records, skipping unparseable lines.

    Missing file -> []. A malformed line (partial write, hand-edit) is skipped
    rather than raising — the ledger is a best-effort read side, never a
    correctness gate. Keep in sync with the copy in render_book.py.
    """
    if not ledger_path.exists():
        return []
    records: list[dict] = []
    try:
        with ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return records


def summarize_cost_records(records: list[dict]) -> tuple[float, int, int, bool]:
    """Reduce a list of cost records to (total_usd, calls, unpriced, estimated).

    `unpriced` counts records whose `usd` is None (unrecognised model — never
    silently folded into the total). `estimated` is True if any record used a
    fallback token split. Keep in sync with the copy in render_book.py.
    """
    total = 0.0
    unpriced = 0
    estimated = False
    for r in records:
        usd = r.get("usd")
        if usd is None:
            unpriced += 1
        else:
            total += usd
        if r.get("estimated"):
            estimated = True
    return round(total, 4), len(records), unpriced, estimated


def format_cost_line(
    label: str, total_usd: float, calls: int, unpriced: int, estimated: bool, suffix: str = ""
) -> str:
    """Render one 'Cost this run' / 'Book total' stdout line.

    `~$` prefix when any summed record was estimated; explicit ', N unpriced'
    when any record had no priced model — a total must never silently omit
    calls. Keep in sync with the copy in render_book.py.
    """
    prefix = "~" if estimated else ""
    parts = [f"{calls} call{'s' if calls != 1 else ''}"]
    if unpriced:
        parts.append(f"{unpriced} unpriced")
    return f"{label}: {prefix}${total_usd:.2f}  ({', '.join(parts)}){suffix}"


def generate_image(
    prompt: str,
    input_images: list[str],
    out_path: Path,
    resolution: str,
    aspect_ratio: str | None = None,
    system_prompt: str | None = None,
    ref_label: str | None = None,
) -> bool:
    """Generate a single sheet via OpenAI gpt-image-2 and write it to out_path.

    Uses images.edit when reference photos exist (the documented multi-image
    likeness path); falls back to images.generate for entries with no photos
    (edit requires >=1 input image). The images.edit endpoint has no system role
    and no per-image label parts, so the system prompt, the reference label, and
    the sheet prompt are folded into one prompt string, wrapped in STYLE_BOOST_*.
    `resolution` (1K/2K/4K) is Gemini-era and unused by gpt-image-2 (logged only);
    the call size comes from aspect_ratio via aspect_to_size().
    """
    import base64

    from openai import OpenAI

    api_key = get_api_key()
    if not api_key:
        print(
            "ERROR: no OpenAI key. Set STORYBOOK_SKILL_OPENAI_API_KEY (or OPENAI_API_KEY).",
            file=sys.stderr,
        )
        return False

    if system_prompt is None:
        system_prompt = image_system_prompt("character")

    # Fold system + reference label + sheet prompt into one prompt, wrapped in the
    # baked-in cartoon style directive (edit has no separate system channel).
    label_note = (
        f"About the attached reference photograph(s): {ref_label}.\n\n"
        if (ref_label and input_images)
        else ""
    )
    full_prompt = (
        STYLE_BOOST_HEAD
        + system_prompt.strip()
        + "\n\n"
        + label_note
        + prompt.strip()
        + STYLE_BOOST_TAIL
    )

    size = aspect_to_size(aspect_ratio)

    # Audit log (book log.txt): mirror the gemini-era contents order — folded
    # prompt as contents[0], then each reference image's metadata.
    contents_desc: list[tuple] = [("text", full_prompt)]
    for img in input_images:
        p = Path(img)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        try:
            n = p.stat().st_size
        except OSError:
            n = 0
        contents_desc.append(("image", str(p), mime, n))
    append_api_log(
        out_path.parent / "log.txt",  # out_path is out_dir/style-sheet-*.png
        script="make_style_sheet.py",
        target=out_path,
        model=IMAGE_MODEL,
        resolution=f"{size} (q={IMAGE_QUALITY}; --resolution {resolution} ignored)",
        aspect_ratio=aspect_ratio,
        response_modalities=["IMAGE"],
        system_instruction=system_prompt,
        contents_desc=contents_desc,
    )

    client = OpenAI(api_key=api_key, timeout=300.0, max_retries=2)
    handles = []
    try:
        for img in input_images:
            handles.append(open(Path(img), "rb"))
        try:
            if handles:
                response = client.images.edit(
                    model=IMAGE_MODEL,
                    image=handles,
                    prompt=full_prompt,
                    size=size,
                    quality=IMAGE_QUALITY,
                    n=1,
                    # moderation isn't in older SDK-typed edit signatures; route via
                    # extra_body so it reaches the API regardless of SDK version.
                    extra_body={"moderation": IMAGE_MODERATION},
                )
            else:
                response = client.images.generate(
                    model=IMAGE_MODEL,
                    prompt=full_prompt,
                    size=size,
                    quality=IMAGE_QUALITY,
                    n=1,
                    extra_body={"moderation": IMAGE_MODERATION},
                )
        except Exception as e:  # surfaces moderation_blocked / bad-request reasons
            print(f"ERROR: image API request failed: {e}", file=sys.stderr)
            return False
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass

    # PER-35: record cost as soon as a response is in hand — the call is billed
    # whether or not the b64 extraction below succeeds. Unlike Gemini (which can
    # return 200 with no image on a safety block), a 200 from images.edit/
    # images.generate always carries image data — `ok` isn't derived from the
    # response here, it's just always True for a successfully bound response.
    usd, tokens, estimated = openai_call_cost(getattr(response, "usage", None), IMAGE_MODEL)
    append_cost_record(
        out_path.parent / "costs.jsonl",
        script="make_style_sheet.py",
        vendor="openai",
        model=IMAGE_MODEL,
        target=out_path,
        ok=True,
        usd=usd,
        estimated=estimated,
        tokens=tokens,
    )

    try:
        b64 = response.data[0].b64_json
    except Exception:
        print("ERROR: no images returned by the API.", file=sys.stderr)
        return False
    if not b64:
        print("ERROR: no images returned by the API.", file=sys.stderr)
        return False

    try:
        out_path.write_bytes(_ensure_png(base64.b64decode(b64)))
    except Exception as e:
        print(f"ERROR: failed to write image: {e}", file=sys.stderr)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-cast-entry reference sheets.")
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
        "--only",
        metavar="ID",
        default=None,
        help=(
            "Process only the cast entry whose id matches ID exactly "
            "(exit 2 if not found). Every entry's slug is still computed "
            "so filenames remain stable even when only one entry is processed. "
            "Delete the entry's PNG first to force regeneration past the skip-if-exists guard."
        ),
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    only_id: str | None = args.only
    story = load_story(story_path)
    reject_legacy_keys(story)
    require_cast_ids(story)
    require_style_guide(story)

    # CLI flag > story.json field > built-in default (2K).
    resolution = args.resolution or story.get("resolution") or "2K"
    # CLI flag > story.json field > unset (model chooses framing).
    aspect_ratio = args.aspect_ratio or story.get("aspect_ratio") or None

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    cast = get_cast(story)
    if not cast:
        print(
            "ERROR: story.json has no 'cast' array. "
            "Add an explicit cast list (characters, objects, locations) before "
            "running make_style_sheet.py.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --only: validate the target id exists before starting the main loop.
    if only_id is not None:
        cast_ids = [(e.get("id") or "").strip() for e in cast]
        if only_id not in cast_ids:
            available = [i for i in cast_ids if i]
            print(
                f"ERROR: no cast entry with id {only_id!r}. "
                f"Available: {', '.join(repr(i) for i in available) if available else '(none)'}",
                file=sys.stderr,
            )
            sys.exit(2)

    any_failed = False

    for entry in cast:
        cid = (entry.get("id") or "").strip()
        name = (entry.get("name") or "").strip()
        kind = (entry.get("kind") or "character").strip() or "character"

        if kind not in ("character", "object", "location"):
            print(
                f"ERROR: cast entry {name!r} (id={cid!r}) has unknown kind {kind!r} "
                f"(must be 'character', 'object', or 'location').",
                file=sys.stderr,
            )
            sys.exit(2)

        # Filename slug is the entry id (PER-56: id is already slug-safe).
        # Guard against blank id (require_cast_ids should have caught it already).
        slug = cid or "entry"
        target = out_dir / f"style-sheet-{slug}.png"

        # --only: skip non-matching entries so they get zero side effects.
        # Every entry's slug/target is still computed above to keep the loop
        # structure consistent (even though slugs are now ids, not derived).
        if only_id is not None and cid != only_id:
            continue

        if target.exists():
            print(f"Skipping {name!r} (id={cid!r}) — sheet already exists: {target}")
            entry["style_sheet"] = str(target)
            print(f"MEDIA: {target}")
            continue

        input_images = collect_ref_images_for_entry(entry, story_path.parent)
        prompt = build_sheet_prompt(story, entry, has_refs=bool(input_images))
        print(f"\nGenerating sheet for {name!r} (id={cid!r}, kind={kind}) -> {target}")
        print(f"Prompt: {prompt}")

        # Keep label wording in sync with render_book.py's IMAGE_SYSTEM_PROMPT
        # "kind" vocabulary (real photograph of the character/object/location).
        # Labels use display name, not id — model-facing strings always use names.
        ref_labels = {
            "character": f"real photograph of the character {name} (facial likeness reference)",
            "object": f"real photograph of the object {name} (appearance reference)",
            "location": f"real photograph of the location {name} (setting reference)",
        }
        ok = generate_image(
            prompt, input_images, target, resolution, aspect_ratio,
            system_prompt=image_system_prompt(kind),
            ref_label=ref_labels[kind] if input_images else None,
        )
        if not ok or not target.exists():
            print(f"ERROR: style sheet PNG not produced for {name!r} (id={cid!r}).", file=sys.stderr)
            any_failed = True
            continue

        entry["style_sheet"] = str(target)
        print(f"MEDIA: {target}")

    # Book-wide style frame (PER-82, "Lever B") — one per book. Skipped under
    # --only: the editor's per-cast-entry regenerate flow (edit_story.py's
    # _run_sheet_regen -> editor.html's fetchSheetVersions) resyncs only that
    # one entry's style_sheet field after an --only run, on the documented
    # assumption that --only touches exactly one top-level diff (see
    # CLAUDE.md's "--only NAME" note). Generating the frame here too would add
    # a second, unsynced top-level field write — the editor's in-memory story
    # wouldn't pick it up, and a subsequent save would silently drop it from
    # disk. The frame is a book-level asset generated by full (non---only)
    # runs, same as Stage 2's normal first-pass workflow.
    if only_id is None:
        frame_target = out_dir / "style-frame.png"
        if frame_target.exists():
            print(f"\nSkipping style frame — already exists: {frame_target}")
            story["style_frame"] = str(frame_target)
            print(f"MEDIA: {frame_target}")
        else:
            frame_prompt = build_style_frame_prompt(story)
            print(f"\nGenerating book-wide style frame -> {frame_target}")
            print(f"Prompt: {frame_prompt}")
            ok = generate_image(
                frame_prompt, [], frame_target, resolution, aspect_ratio,
                system_prompt=image_system_prompt("style"),
                ref_label=None,
            )
            if not ok or not frame_target.exists():
                print("ERROR: style frame PNG not produced.", file=sys.stderr)
                any_failed = True
            else:
                story["style_frame"] = str(frame_target)
                print(f"MEDIA: {frame_target}")

    save_story(story, story_path)
    print("\nstory.json updated with per-entry style_sheet paths and the style_frame path.")

    # PER-35: print a cost summary whenever this run actually spent anything.
    run_total, run_calls, run_unpriced, run_estimated = summarize_cost_records(_RUN_COST_RECORDS)
    if run_calls:
        ledger_path = out_dir / "costs.jsonl"
        print(format_cost_line("Cost this run", run_total, run_calls, run_unpriced, run_estimated))
        book_total, book_calls, book_unpriced, book_estimated = summarize_cost_records(
            read_cost_ledger(ledger_path)
        )
        print(
            format_cost_line(
                "Book total", book_total, book_calls, book_unpriced, book_estimated,
                suffix=f"      [{ledger_path}]",
            )
        )

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
