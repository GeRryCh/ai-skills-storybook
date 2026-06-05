#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
# ]
# ///
"""
Generate per-character style sheets for a storybook.

Reads story.json, calls the Gemini image API once per character to produce
individual PNGs (style-sheet-{slug}.png), then writes each character's style_sheet
path back into story.json.

Idempotent: skips characters whose style-sheet-{slug}.png already exists. To force
a regenerate for one character, delete that character's file and re-run.

Requires GEMINI_API_KEY in the environment.

Usage:
  uv run make_style_sheet.py --story /path/to/story.json [--out-dir /path/to/outdir]
                              [--resolution 1K|2K|4K] [--aspect-ratio RATIO]
"""

from __future__ import annotations
import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

# Gemini image-generation config.
IMAGE_MODEL = "gemini-3-pro-image"
MAX_INPUT_IMAGES = 5  # Gemini 3 Pro Image: up to 5 character reference images per call
IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. Preserve the character's facial "
    "identity and likeness from the provided reference photographs; take the "
    "outfit and styling from the text prompt, never from the photographs. "
    "Output only the generated image without additional commentary."
)


def load_story(story_path: Path) -> dict:
    with open(story_path) as f:
        return json.load(f)


def save_story(story: dict, story_path: Path) -> None:
    with open(story_path, "w") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)


def get_characters(story: dict) -> list[dict]:
    """Return the explicit cast from story['characters'].

    No prose scraping: an earlier regex heuristic minted phantom characters
    (e.g. a fish 'Deep' from 'deep twilight sky', a second girl 'She' from
    'She holds a rabbit') and poisoned every page. The cast must be authored
    explicitly in story.json as [{name, appearance, ref_image?}], where
    ref_image is one path or a list of paths mapped to that character.
    """
    chars = story.get("characters")
    if isinstance(chars, list) and chars:
        return chars
    return []


def char_slug(name: str, used: set[str]) -> str:
    """Filesystem-safe slug from a character name. Dedupes with an index suffix."""
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


def build_char_prompt(story: dict, character: dict) -> str:
    """Prompt for one character's individual style sheet."""
    style = build_style_block(story)
    name = (character.get("name") or "").strip()
    appearance = (character.get("appearance") or "").strip()
    if name and appearance:
        subject = f"{name} ({appearance})"
    elif name:
        subject = name
    else:
        subject = "the main character"
    return (
        f"Character reference sheet for a children's picture book. "
        f"Show this one character only: {subject}. "
        f"Show a full-body view and a close-up of the face, multiple angles, "
        f"consistent character design across the sheet. "
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


def collect_ref_images_for_char(character: dict) -> list[str]:
    """This character's own reference photos, in order, capped at MAX_INPUT_IMAGES.

    `ref_image` accepts a single path (string) or a list of paths. Only photos
    mapped to THIS character are used — there is no shared global pool, so one
    character's reference photo never bleeds into another character's sheet.
    The cast-to-photo mapping is fixed in Stage 1 (storybook-story).

    Normalize -> dedup (keep order) -> drop missing files -> cap at MAX_INPUT_IMAGES (5
    for pro), logging any refs dropped to the cap (mirrors render_book.py's per-page
    selection log).
    """
    raw = character.get("ref_image")
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
            print(f"Warning: character ref not found, skipping: {r}", file=sys.stderr)

    if len(existing) > MAX_INPUT_IMAGES:
        dropped = existing[MAX_INPUT_IMAGES:]
        name = (character.get("name") or "character").strip()
        print(
            f"Warning: {name!r} has {len(existing)} refs; capping at "
            f"{MAX_INPUT_IMAGES} (character-lane limit). Dropping: {', '.join(dropped)}",
            file=sys.stderr,
        )
    return existing[:MAX_INPUT_IMAGES]


def generate_image(
    prompt: str,
    input_images: list[str],
    out_path: Path,
    resolution: str,
    aspect_ratio: str | None = None,
) -> bool:
    """Generate a single image via the Gemini API and write it to out_path."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set in the environment.", file=sys.stderr)
        return False

    # Build contents: text prompt + one Part.from_bytes per input image.
    contents: list = [prompt]
    for img in input_images:
        p = Path(img)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        contents.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))

    config = types.GenerateContentConfig(
        system_instruction=IMAGE_SYSTEM_PROMPT,
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(image_size=resolution, aspect_ratio=aspect_ratio),
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
        out_path.write_bytes(image_data)
    except Exception as e:
        print(f"ERROR: failed to write image: {e}", file=sys.stderr)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-character style sheets.")
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
    require_style_guide(story)

    # CLI flag > story.json field > built-in default (2K).
    resolution = args.resolution or story.get("resolution") or "2K"
    # CLI flag > story.json field > unset (model chooses framing).
    aspect_ratio = args.aspect_ratio or story.get("aspect_ratio") or None

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    characters = get_characters(story)
    if not characters:
        print(
            "ERROR: story.json has no 'characters' array. "
            "Add an explicit characters list before running make_style_sheet.py.",
            file=sys.stderr,
        )
        sys.exit(1)

    used_slugs: set[str] = set()
    any_failed = False

    for char in characters:
        name = (char.get("name") or "").strip()
        slug = char_slug(name or "character", used_slugs)
        target = out_dir / f"style-sheet-{slug}.png"

        if target.exists():
            print(f"Skipping {name!r} — sheet already exists: {target}")
            char["style_sheet"] = str(target)
            print(f"MEDIA: {target}")
            continue

        prompt = build_char_prompt(story, char)
        input_images = collect_ref_images_for_char(char)
        print(f"\nGenerating sheet for {name!r} -> {target}")
        print(f"Prompt: {prompt}")

        ok = generate_image(prompt, input_images, target, resolution, aspect_ratio)
        if not ok or not target.exists():
            print(f"ERROR: style sheet PNG not produced for {name!r}.", file=sys.stderr)
            any_failed = True
            continue

        char["style_sheet"] = str(target)
        print(f"MEDIA: {target}")

    save_story(story, story_path)
    print("\nstory.json updated with per-character style_sheet paths.")

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
