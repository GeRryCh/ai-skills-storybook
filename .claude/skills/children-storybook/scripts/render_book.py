#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Render all pages of a children's storybook.

For each page in story.json:
  1. Calls nano-banana to generate the illustration (with style sheet + character refs).
  2. Runs overlay_text.py to composite the story text.
  3. Prints MEDIA: <path> for each final page.

Skips pages whose final file already exists (safe to re-run after partial failure).

Usage:
  uv run render_book.py --story /path/to/story.json [--out-dir DIR]
                        [--from N] [--only N] [--resolution 1K|2K|4K]
"""

from __future__ import annotations
import argparse
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
NANO_BANANA = (
    SCRIPTS_DIR.parent.parent
    / "nano-banana-pro-openrouter"
    / "scripts"
    / "generate_image.py"
)
OVERLAY_SCRIPT = SCRIPTS_DIR / "overlay_text.py"

TEXT_SAFE_ZONE_DIRECTIVE = (
    "Leave the {placement} quarter of the image as a soft, "
    "low-detail, lightly-toned area suitable for overlaying text. "
    "Do not place any narrative text in the image."
)
STYLE_ANCHOR = (
    "Art style must match the provided character reference sheet exactly. "
    "Consistent character design, {style}."
)


def load_story(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def build_image_prompt(page: dict, story: dict) -> str:
    placement = page.get("text_placement", "bottom")
    style = story.get("style", "children's picture book illustration")
    base = page["image_prompt"].rstrip(". ")
    safe_zone = TEXT_SAFE_ZONE_DIRECTIVE.format(placement=placement)
    anchor = STYLE_ANCHOR.format(style=style)
    return f"{base}. {safe_zone}. {anchor}"


def run_nano_banana(
    prompt: str,
    raw_path: Path,
    story: dict,
    resolution: str,
    log: list[str],
) -> bool:
    if not NANO_BANANA.exists():
        log.append(f"ERROR: nano-banana not found at {NANO_BANANA}")
        return False

    cmd = [
        "uv", "run", str(NANO_BANANA),
        "--prompt", prompt,
        "--filename", str(raw_path),
        "--resolution", resolution,
    ]

    # Input images: style sheet first, then up to 2 character refs (max 3 total)
    style_sheet = story.get("style_sheet_path")
    refs = story.get("character_refs", [])

    input_images: list[str] = []
    if style_sheet and Path(style_sheet).exists():
        input_images.append(style_sheet)
    for ref in refs:
        if Path(ref).exists() and len(input_images) < 3:
            input_images.append(ref)

    for img in input_images:
        cmd += ["--input-image", img]

    log.append(f"  Generating: {raw_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if result.stdout:
            log.append(result.stdout.rstrip())
        if result.stderr:
            log.append(result.stderr.rstrip())
    return result.returncode == 0


def run_overlay(
    raw_path: Path, text: str, placement: str, color: str, final_path: Path, log: list[str]
) -> bool:
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(raw_path),
        "--text", text,
        "--placement", placement,
        "--out", str(final_path),
        "--color", color,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if result.stdout:
            log.append(result.stdout.rstrip())
        if result.stderr:
            log.append(result.stderr.rstrip())
    return result.returncode == 0


def render_page(page: dict, story: dict, pages_dir: Path, resolution: str) -> tuple[bool, list[str]]:
    """Render one page (nano-banana + overlay). Returns (ok, log_lines). Page-independent."""
    page_num = page["page_num"]
    log: list[str] = [f"=== Page {page_num} ==="]
    nn = f"{page_num:02d}"
    final_path = pages_dir / f"page-{nn}.png"
    raw_path = pages_dir / f"raw-page-{nn}.png"

    prompt = build_image_prompt(page, story)

    ok = run_nano_banana(prompt, raw_path, story, resolution, log)
    if not ok or not raw_path.exists():
        log.append(f"  ERROR: image generation failed for page {page_num}")
        return False, log

    text = page.get("text", "")
    placement = page.get("text_placement", "bottom")
    color = page.get("text_color_hint", "dark")

    ok = run_overlay(raw_path, text, placement, color, final_path, log)
    if not ok or not final_path.exists():
        log.append(f"  ERROR: text overlay failed for page {page_num}")
        return False, log

    log.append(f"  Done: {final_path}")
    log.append(f"MEDIA: {final_path}")
    return True, log


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all pages of a storybook.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    parser.add_argument("--resolution", choices=["1K", "2K", "4K"], default="2K")
    parser.add_argument("--from", dest="from_page", type=int, default=1,
                        help="Start from this page number (1-indexed)")
    parser.add_argument("--only", dest="only_page", type=int, default=None,
                        help="Render only this page number")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Number of pages to render in parallel (default: 4, 1 = serial)")
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json", file=sys.stderr)
        sys.exit(1)

    # Warn if no style sheet
    if not story.get("style_sheet_path"):
        print("Warning: style_sheet_path not set in story.json.")
        print("Run make_style_sheet.py first for better character consistency.")
        print()

    # Select pages to render, skipping filtered-out and already-existing ones.
    todo: list[dict] = []
    for page in pages:
        page_num = page["page_num"]
        if args.only_page is not None and page_num != args.only_page:
            continue
        if page_num < args.from_page:
            continue
        final_path = pages_dir / f"page-{page_num:02d}.png"
        if final_path.exists():
            print(f"Page {page_num}: already exists, skipping. ({final_path})")
            print(f"MEDIA: {final_path}")
            continue
        todo.append(page)

    errors = 0
    workers = max(1, min(args.concurrency, len(todo)))

    if workers <= 1:
        for page in todo:
            ok, log = render_page(page, story, pages_dir, args.resolution)
            print("\n" + "\n".join(log))
            if not ok:
                errors += 1
    elif todo:
        print(f"\nRendering {len(todo)} page(s) with concurrency {workers}...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(render_page, page, story, pages_dir, args.resolution): page
                for page in todo
            }
            for future in as_completed(futures):
                ok, log = future.result()
                # Print each page's full log atomically so parallel output stays grouped.
                print("\n" + "\n".join(log))
                if not ok:
                    errors += 1

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
