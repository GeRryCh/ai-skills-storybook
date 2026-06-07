#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Package a rendered storybook into a single zip archive.

Collects from the output directory:
  - story.json
  - pages/*.png  (final pages only — excludes raw-page-*.png intermediates and the
                  pages/history/ generation archive created by the visual editor)
  - style-sheet*.png  (character/object style sheets in the book root)
  - *.pdf  and  *.epub  (assembled book files — glob-based so custom --out names
                          are captured automatically)
  - *.zip itself is never included

Run merge_pdf.py / merge_epub.py first so the book files are present in the zip.
If no finished page images are found, exits with an error; if no book files are
found, emits a warning (pages and story.json are still zipped).

Designed to run standalone — free, no image API cost.
Keep _slug() in sync with merge_pdf.py and merge_epub.py.

Usage:
  uv run package_book.py --story /path/to/story.json [--out-dir DIR] [--out path.zip]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


def _slug(title: str) -> str:
    """Convert a book title to a safe filename slug.

    Keep in sync with merge_pdf.py and merge_epub.py — all three share this helper.
    """
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "storybook"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package a rendered storybook into a zip archive."
    )
    parser.add_argument(
        "--story",
        required=True,
        metavar="STORY",
        help="Path to story.json.",
    )
    parser.add_argument(
        "--out-dir",
        dest="out_dir",
        default=None,
        metavar="DIR",
        help="Output directory containing rendered pages and assembled book files. "
             "Defaults to the directory containing story.json.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Explicit path for the output zip file. "
             "Default: {out_dir}/{slug(title)}-book.zip.",
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    if not story_path.is_file():
        print(f"ERROR: story.json not found: {story_path}", file=sys.stderr)
        sys.exit(1)

    with story_path.open(encoding="utf-8") as f:
        story = json.load(f)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    pages_dir = out_dir / "pages"
    title = story.get("title") or "Untitled"

    if args.out:
        zip_path = Path(args.out).resolve()
    else:
        zip_path = out_dir / f"{_slug(title)}-book.zip"

    # ── Collect files ─────────────────────────────────────────────────────────

    # story.json (stored at zip root regardless of its real location)
    entries: list[tuple[Path, str]] = [(story_path, "story.json")]

    # Final rendered pages:
    #   - Non-recursive glob (pages/*.png) deliberately excludes pages/history/
    #     (PER-41 generation archives) and any subdirectory content.
    #   - raw-page-*.png intermediates are also excluded — large, not needed for
    #     distribution.
    final_pages = sorted(
        p for p in pages_dir.glob("*.png")
        if not p.name.startswith("raw-page-")
    )
    for p in final_pages:
        entries.append((p, f"pages/{p.name}"))

    if not final_pages:
        print(
            f"ERROR: No rendered page images found in {pages_dir}. "
            "Render pages first (storybook-render skill, render_book.py), then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Style sheets (style-sheet*.png in out_dir root — glob without hyphen to
    # catch both "style-sheet.png" and "style-sheet-{name}.png" naming variants)
    for p in sorted(out_dir.glob("style-sheet*.png")):
        entries.append((p, p.name))

    # Assembled book files: PDF, EPUB (glob-based — captures --out custom names)
    book_files: list[Path] = []
    for p in sorted(out_dir.glob("*.pdf")):
        entries.append((p, p.name))
        book_files.append(p)
    for p in sorted(out_dir.glob("*.epub")):
        entries.append((p, p.name))
        book_files.append(p)

    if not book_files:
        print(
            "Warning: no assembled book files (*.pdf / *.epub) found in "
            f"{out_dir}.\n"
            "Run merge_pdf.py and/or merge_epub.py first to include them in the zip.",
            file=sys.stderr,
        )

    # ── Build zip ─────────────────────────────────────────────────────────────

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in entries:
            zf.write(src, arcname)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Packaged {len(entries)} entries → {zip_path} ({size_mb:.1f} MB)")
    print(f"MEDIA: {zip_path}")


if __name__ == "__main__":
    main()
