#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai",
# ]
# ///
"""
Generate per-character style sheets for a storybook.

Reads story.json, calls the OpenRouter image API once per character to produce
individual PNGs (style-sheet-{slug}.png), then writes each character's style_sheet
path back into story.json.

Idempotent: skips characters whose style-sheet-{slug}.png already exists. To force
a regenerate for one character, delete that character's file and re-run.

Requires OPENROUTER_API_KEY in the environment.

Usage:
  uv run make_style_sheet.py --story /path/to/story.json [--out-dir /path/to/outdir]
"""

from __future__ import annotations
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

# OpenRouter image-generation config (mirrors the nano-banana-pro-openrouter skill).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
IMAGE_MODEL = "google/gemini-3.1-flash-image-preview"
MAX_INPUT_IMAGES = 3
IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. Preserve the provided reference images' "
    "character design and art style. Output only the generated image without "
    "additional commentary."
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


def build_char_prompt(story: dict, character: dict) -> str:
    """Prompt for one character's individual style sheet."""
    style = story.get("style", "children's picture book illustration")
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
        f"Use any reference photo ONLY as guidance for that character's face, "
        f"hair, and clothing — redraw it fully in the illustration style. Never "
        f"composite, paste, trace, or show the reference photo itself anywhere in "
        f"the output. No photographic elements. "
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

    Normalize -> dedup (keep order) -> drop missing files -> cap, logging any
    refs dropped to the cap (mirrors render_book.py's per-page selection log).
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
            f"{MAX_INPUT_IMAGES} (API limit). Dropping: {', '.join(dropped)}",
            file=sys.stderr,
        )
    return existing[:MAX_INPUT_IMAGES]


def encode_image_to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def generate_image(prompt: str, input_images: list[str], out_path: Path, resolution: str) -> bool:
    """Generate a single image via OpenRouter and write it to out_path."""
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in the environment.", file=sys.stderr)
        return False

    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in input_images:
        content.append(
            {"type": "image_url", "image_url": {"url": encode_image_to_data_url(Path(img))}}
        )

    messages = [
        {"role": "system", "content": IMAGE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    try:
        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        response = client.chat.completions.create(
            model=IMAGE_MODEL,
            messages=messages,
            extra_body={
                "modalities": ["image", "text"],
                "image_config": {"image_size": resolution},
            },
        )
    except Exception as e:
        print(f"ERROR: image API request failed: {e}", file=sys.stderr)
        return False

    images = getattr(response.choices[0].message, "images", None)
    if not images:
        print("ERROR: no images returned by the API.", file=sys.stderr)
        return False

    image_url = None
    first = images[0]
    if isinstance(first, dict):
        image_url = first.get("image_url", {}).get("url") or first.get("url")
    if not image_url or not image_url.startswith("data:") or ";base64," not in image_url:
        print("ERROR: image payload missing base64 data URL.", file=sys.stderr)
        return False

    _, encoded = image_url.split(",", 1)
    try:
        out_path.write_bytes(base64.b64decode(encoded))
    except Exception as e:
        print(f"ERROR: failed to decode/write image: {e}", file=sys.stderr)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-character style sheets.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)

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

        ok = generate_image(prompt, input_images, target, "2K")
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
