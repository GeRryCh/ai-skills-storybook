#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Generate a character/style reference sheet for a storybook.

Reads story.json, calls nano-banana-pro-openrouter to produce a single PNG
showing all named characters in the book's art style, then writes the path
back into story.json as style_sheet_path.

Usage:
  uv run make_style_sheet.py --story /path/to/story.json [--out-dir /path/to/outdir]
"""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

NANO_BANANA = (
    Path(__file__).parent.parent.parent
    / "nano-banana-pro-openrouter"
    / "scripts"
    / "generate_image.py"
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
    explicitly in story.json as [{name, appearance, ref_image?}].
    """
    chars = story.get("characters")
    if isinstance(chars, list) and chars:
        return chars
    return []


def build_prompt(story: dict, characters: list[dict]) -> str:
    style = story.get("style", "children's picture book illustration")
    if characters:
        cast_lines = []
        for c in characters:
            name = (c.get("name") or "").strip()
            appearance = (c.get("appearance") or "").strip()
            if name and appearance:
                cast_lines.append(f"{name} ({appearance})")
            elif name:
                cast_lines.append(name)
        cast = "; ".join(cast_lines)
        char_clause = (
            f"Show each of these characters and ONLY these characters, "
            f"one per column: {cast}. Do not invent any extra characters. "
        )
    else:
        char_clause = "Show the main characters of the story. "
    return (
        f"Character reference sheet for a children's picture book. "
        f"{char_clause}"
        f"For each character show a full-body view and a close-up of the face, "
        f"multiple angles, consistent character design across the row. "
        f"Use any reference photo ONLY as guidance for that character's face, "
        f"hair, and clothing — redraw it fully in the illustration style. Never "
        f"composite, paste, trace, or show the reference photo itself anywhere in "
        f"the output. No photographic elements. "
        f"Art style: {style}. "
        f"Background must be a single flat, plain, neutral light colour — empty, "
        f"no scenery, no objects, no people other than the listed characters. "
        f"No text, no labels, no speech bubbles. "
        f"Clear consistent visual design so every character is recognisable across many pages."
    )


def collect_ref_images(story: dict, characters: list[dict]) -> list[str]:
    """Per-character ref_image first, then the global character_refs pool.

    Dedup, keep order, cap at 3 (nano-banana input-image limit).
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for c in characters:
        ref = (c.get("ref_image") or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    for ref in story.get("character_refs", []):
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)

    valid: list[str] = []
    for ref in ordered:
        if Path(ref).exists():
            valid.append(ref)
            if len(valid) >= 3:
                break
        else:
            print(f"Warning: character ref not found, skipping: {ref}", file=sys.stderr)
    return valid


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate character style sheet.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    style_sheet_path = out_dir / "style-sheet.png"

    if style_sheet_path.exists():
        print(f"Style sheet already exists: {style_sheet_path}")
        story["style_sheet_path"] = str(style_sheet_path)
        save_story(story, story_path)
        return

    if not NANO_BANANA.exists():
        print(f"ERROR: nano-banana script not found at {NANO_BANANA}", file=sys.stderr)
        print("Ensure nano-banana-pro-openrouter skill is installed in the same skills directory.", file=sys.stderr)
        sys.exit(1)

    characters = get_characters(story)
    if not characters:
        print(
            "Warning: story.json has no 'characters' array. Falling back to a "
            "generic prompt. Add an explicit characters list for reliable, "
            "phantom-free style sheets.",
            file=sys.stderr,
        )
    prompt = build_prompt(story, characters)
    print(f"Cast: {[c.get('name') for c in characters]}")
    print(f"Prompt: {prompt}")

    cmd = [
        "uv", "run", str(NANO_BANANA),
        "--prompt", prompt,
        "--filename", str(style_sheet_path),
        "--resolution", "2K",
    ]

    # Reference images: per-character first, then global pool (nano-banana caps at 3)
    for ref in collect_ref_images(story, characters):
        cmd += ["--input-image", ref]

    print(f"\nRunning: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print(f"ERROR: nano-banana exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    if not style_sheet_path.exists():
        print("ERROR: style sheet PNG not produced. Check nano-banana output above.", file=sys.stderr)
        sys.exit(1)

    story["style_sheet_path"] = str(style_sheet_path)
    save_story(story, story_path)
    print(f"\nStyle sheet saved: {style_sheet_path}")
    print(f"story.json updated with style_sheet_path.")


if __name__ == "__main__":
    main()
