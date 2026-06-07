#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Validate story.json against story_schema.json.

Run this as the last step of storybook-story (Stage 1), after writing story.json
and before presenting the approval gate. Exits 0 when clean (warnings shown but
never block). Exits 2 when validation errors are found — fix and re-run.

Usage:
  uv run validate_story.py --story /path/to/story.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Import the hand-rolled validator from edit_story.py (sibling script).
# edit_story.py is side-effect-free at module level — the HTTP server only
# starts under `if __name__ == "__main__"`, so importing it is safe.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from edit_story import load_schema, validate_story  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate story.json against story_schema.json."
    )
    parser.add_argument(
        "--story",
        required=True,
        metavar="PATH",
        help="Path to story.json",
    )
    args = parser.parse_args()

    story_path = Path(args.story)

    # --- load -----------------------------------------------------------------
    if not story_path.exists():
        print(f"ERROR: file not found: {story_path}", file=sys.stderr)
        sys.exit(2)

    try:
        story = json.loads(story_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse story.json: {exc}", file=sys.stderr)
        sys.exit(2)

    # --- validate -------------------------------------------------------------
    schema = load_schema()
    errors, warnings = validate_story(story, story_path.parent, schema)

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        count = len(errors)
        print(f"\nstory.json has {count} error{'s' if count != 1 else ''} — fix and re-run.")
        sys.stdout.flush()
        sys.exit(2)

    warn_note = f" ({len(warnings)} warning{'s' if len(warnings) != 1 else ''})" if warnings else ""
    print(f"story.json valid against story_schema.json{warn_note}.")


if __name__ == "__main__":
    main()
