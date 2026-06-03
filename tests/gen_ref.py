#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
# ]
# ///
"""
Generate the Pip reference character image for the pip-storm fixture.

Reuses generate_image() from make_style_sheet.py to keep the Gemini API call
in one place. Run from the repo root or any directory.

Usage:
  uv run tests/gen_ref.py

Writes: tests/fixtures/pip-storm/refs/pip-ref.png
Requires: GEMINI_API_KEY in the environment.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Import generate_image from make_style_sheet.py without running its main().
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "skills" / "storybook-stylesheet" / "scripts"))
from make_style_sheet import generate_image  # noqa: E402

OUT_PATH = Path(__file__).parent / "fixtures" / "pip-storm" / "refs" / "pip-ref.png"

PROMPT = (
    "Character reference sheet: a single hedgehog named Pip. "
    "Small brave hedgehog, round body, soft brown spines, big curious dark eyes, "
    "tiny pink nose, wears a tiny red scarf. "
    "Shown from the front on a plain white background with no scene elements. "
    "Soft watercolor, gentle pastel palette, children's picture book illustration style. "
    "High clarity, suitable as a visual reference for consistent rendering across pages."
)

if __name__ == "__main__":
    if OUT_PATH.exists():
        print(f"Already exists: {OUT_PATH}")
        sys.exit(0)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating reference image → {OUT_PATH}")
    ok = generate_image(PROMPT, [], OUT_PATH, "1K")
    if not ok:
        print("ERROR: generation failed.", file=sys.stderr)
        sys.exit(1)
    print(f"Done: {OUT_PATH}")
