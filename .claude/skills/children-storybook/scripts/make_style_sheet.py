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


def extract_characters(story: dict) -> list[str]:
    """Collect unique character names mentioned across all image prompts."""
    # Heuristic: look for words starting with uppercase that appear in prompts
    import re
    all_text = " ".join(p["image_prompt"] for p in story["pages"])
    candidates = re.findall(r'\b([A-Z][a-z]{2,})\b', all_text)
    # Filter out common style words
    skip = {"Leave", "Square", "Soft", "Full", "Children", "The", "A"}
    seen: set[str] = set()
    chars: list[str] = []
    for c in candidates:
        if c not in skip and c not in seen:
            seen.add(c)
            chars.append(c)
    return chars[:6]  # cap at 6 character names


def build_prompt(story: dict, characters: list[str]) -> str:
    style = story.get("style", "children's picture book illustration")
    char_list = ", ".join(characters) if characters else "the main characters"
    return (
        f"Character reference sheet for a children's picture book. "
        f"Show {char_list} side by side: full-body view and close-up face, "
        f"multiple angles, consistent character design. "
        f"Art style: {style}. "
        f"Neutral light background, no text, no speech bubbles. "
        f"Clear consistent visual design so every character is recognisable across many pages."
    )


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

    characters = extract_characters(story)
    prompt = build_prompt(story, characters)
    print(f"Characters detected: {characters}")
    print(f"Prompt: {prompt}")

    cmd = [
        "uv", "run", str(NANO_BANANA),
        "--prompt", prompt,
        "--filename", str(style_sheet_path),
        "--resolution", "2K",
    ]

    # Add user-supplied character reference images (nano-banana caps at 3)
    for ref in story.get("character_refs", [])[:3]:
        ref_path = Path(ref)
        if ref_path.exists():
            cmd += ["--input-image", str(ref_path)]
        else:
            print(f"Warning: character ref not found, skipping: {ref}", file=sys.stderr)

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
