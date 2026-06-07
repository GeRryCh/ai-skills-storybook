#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
#     "Pillow",
# ]
# ///
"""
Generate per-cast-entry reference sheets for a storybook.

Reads story.json, calls the Gemini image API once per eligible cast entry to
produce individual PNGs (style-sheet-{slug}.png), then writes each entry's
style_sheet path back into story.json.

Eligible entries (every cast entry gets a sheet):
  kind=character — outfit-locked, face/hair likeness from ref_image photos.
  kind=object    — multi-angle, distinguishing features.
  kind=location  — generated from the entry's real-place ref_image photo(s)
                   (downloaded in Stage 1, PER-50) and/or 'appearance'. The
                   sheet is the render reference; the raw photo remains the
                   render-time fallback when no sheet exists.

Idempotent: skips entries whose style-sheet-{slug}.png already exists. To force
a regenerate for one entry, delete that entry's file and re-run.

Requires GEMINI_API_KEY in the environment.

Usage:
  uv run make_style_sheet.py --story /path/to/story.json [--out-dir /path/to/outdir]
                              [--resolution 1K|2K|4K] [--aspect-ratio RATIO]
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

# Gemini image-generation config.
IMAGE_MODEL = "gemini-3-pro-image"
MAX_INPUT_IMAGES = 5  # Gemini 3 Pro Image: up to 5 reference images per call

# Per-kind system prompts: common prefix + kind-specific likeness sentence + tail.
_IMAGE_SYSTEM_PROMPT_PREFIX = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. "
)
_IMAGE_SYSTEM_PROMPT_TAIL = "Output only the generated image without additional commentary."
_KIND_LIKENESS = {
    "character": (
        "Preserve the character's facial identity and likeness from the provided "
        "reference photographs; take the outfit and styling from the text prompt, "
        "never from the photographs. "
    ),
    "object": (
        "Preserve the subject's recognizable shape, structure, materials, and "
        "distinguishing features from any provided reference photographs, but render "
        "fully in the requested illustration style — never photographic. "
    ),
    "location": (
        "Preserve the place's recognizable architecture, landmarks, and geography "
        "from any provided reference photographs, but render fully in the requested "
        "illustration style — never photographic. "
    ),
}


def image_system_prompt(kind: str) -> str:
    """Return the system prompt for a given cast-entry kind."""
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


def build_sheet_prompt(story: dict, entry: dict) -> str:
    """Prompt for one cast entry's individual reference sheet, branched on kind."""
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
            f"Object reference sheet for a children's picture book. "
            f"Show this one object only: {subject}. "
            f"Show the object from multiple angles, plus a detail close-up of its most "
            f"distinguishing features, consistent design across the sheet. "
            f"If reference photo(s) are provided, match the object's shape, proportions, "
            f"colours, and distinguishing details as closely as possible — keep it clearly "
            f"recognisable. "
            f"Render in the illustration style (do not composite, paste, trace, or "
            f"reproduce the photo itself; no photographic elements). "
            f"Art style: {style}. "
            f"Background must be a single flat, plain, neutral light colour — empty, "
            f"no scenery, no characters, no people. "
            f"No text, no labels, no speech bubbles. "
            f"Clear consistent visual design so this object is recognisable across many pages."
        )
    elif kind == "location":
        return (
            f"Location reference sheet for a children's picture book. "
            f"Show this one place only: {subject}. "
            f"Show a wide establishing view and one or two closer views from different "
            f"angles, plus a detail close-up of its most distinguishing features, "
            f"consistent design across the sheet. "
            f"If reference photo(s) are provided, match the place's recognisable "
            f"architecture, landmarks, and geography as closely as possible. "
            f"Render in the illustration style (do not composite, paste, trace, or "
            f"reproduce the photo itself; no photographic elements). "
            f"Art style: {style}. "
            f"No people and no characters anywhere in the scene. "
            f"No text, no labels, no speech bubbles. "
            f"Clear consistent visual design so this place is recognisable across many pages."
        )
    else:  # character (default)
        return (
            f"Character reference sheet for a children's picture book. "
            f"Show this one character only: {subject}. "
            f"The sheet must contain exactly four views: "
            f"(1) full-body front view facing the viewer (анфас), "
            f"(2) full-body left profile view, "
            f"(3) full-body right profile view, "
            f"(4) a close-up of the face. "
            f"Same character at the same scale and with identical design in every view. "
            f"If reference photo(s) are provided, match this character's facial features "
            f"and hair as closely as possible — keep the likeness clearly recognisable. "
            f"Render in the illustration style (do not composite, paste, trace, or "
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


def collect_ref_images_for_entry(entry: dict) -> list[str]:
    """This cast entry's own reference photos, in order, capped at MAX_INPUT_IMAGES.

    `ref_image` accepts a single path (string) or a list of paths.

    For kind=character: every path must be a single-person image — a solo photo or a
    per-person crop produced in Stage 1 by storybook-story's crop_character.py.
    Multi-person group photos should have been cropped before story.json was written;
    if a group photo slips through here the model cannot know which person's likeness
    to anchor.

    Only photos mapped to THIS entry are used — there is no shared global pool, so
    one entry's reference photo never bleeds into another's sheet. The cast-to-photo
    mapping is fixed in Stage 1 (storybook-story).

    Normalize -> dedup (keep order) -> drop missing files -> cap at MAX_INPUT_IMAGES (5
    for pro), logging any refs dropped to the cap.
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

    existing = [r for r in ordered if Path(r).exists()]
    for r in ordered:
        if not Path(r).exists():
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


def generate_image(
    prompt: str,
    input_images: list[str],
    out_path: Path,
    resolution: str,
    aspect_ratio: str | None = None,
    system_prompt: str | None = None,
) -> bool:
    """Generate a single image via the Gemini API and write it to out_path."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set in the environment.", file=sys.stderr)
        return False

    if system_prompt is None:
        system_prompt = image_system_prompt("character")

    # Build contents: text prompt + one Part.from_bytes per input image.
    # No interleaved label strings (stylesheet sends bare image parts).
    contents: list = [prompt]
    contents_desc: list[tuple] = [("text", prompt)]  # mirrors contents for audit log
    for img in input_images:
        p = Path(img)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        data = p.read_bytes()
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
        contents_desc.append(("image", str(p), mime, len(data)))

    response_modalities = ["TEXT", "IMAGE"]
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_modalities=response_modalities,
        image_config=types.ImageConfig(image_size=resolution, aspect_ratio=aspect_ratio),
    )
    append_api_log(
        out_path.parent / "log.txt",  # out_path is out_dir/style-sheet-*.png
        script="make_style_sheet.py",
        target=out_path,
        model=IMAGE_MODEL,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        response_modalities=response_modalities,
        system_instruction=system_prompt,
        contents_desc=contents_desc,
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=contents,
            config=config,
        )
    except Exception as e:
        print(f"ERROR: image API request failed: {e}", file=sys.stderr)
        return False

    # Extract image bytes from the response parts.
    image_data: bytes | None = None
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            image_data = part.inline_data.data
            break
    if not image_data:
        print("ERROR: no images returned by the API.", file=sys.stderr)
        return False

    try:
        out_path.write_bytes(_ensure_png(image_data))
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
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)
    reject_legacy_keys(story)
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

    used_slugs: set[str] = set()
    any_failed = False

    for entry in cast:
        name = (entry.get("name") or "").strip()
        kind = (entry.get("kind") or "character").strip() or "character"

        if kind not in ("character", "object", "location"):
            print(
                f"ERROR: cast entry {name!r} has unknown kind {kind!r} "
                f"(must be 'character', 'object', or 'location').",
                file=sys.stderr,
            )
            sys.exit(2)

        slug = char_slug(name or "entry", used_slugs)
        target = out_dir / f"style-sheet-{slug}.png"

        if target.exists():
            print(f"Skipping {name!r} — sheet already exists: {target}")
            entry["style_sheet"] = str(target)
            print(f"MEDIA: {target}")
            continue

        prompt = build_sheet_prompt(story, entry)
        input_images = collect_ref_images_for_entry(entry)
        print(f"\nGenerating sheet for {name!r} (kind={kind}) -> {target}")
        print(f"Prompt: {prompt}")

        ok = generate_image(
            prompt, input_images, target, resolution, aspect_ratio,
            system_prompt=image_system_prompt(kind),
        )
        if not ok or not target.exists():
            print(f"ERROR: style sheet PNG not produced for {name!r}.", file=sys.stderr)
            any_failed = True
            continue

        entry["style_sheet"] = str(target)
        print(f"MEDIA: {target}")

    save_story(story, story_path)
    print("\nstory.json updated with per-entry style_sheet paths.")

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
