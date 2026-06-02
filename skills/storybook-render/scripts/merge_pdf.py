#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow", "img2pdf"]
# ///
"""
Merge all rendered page images for a storybook into a single PDF.

Reads the ordered page list from story.json and collects the final rendered
PNGs from {out_dir}/pages/. Works for both text modes:
  - overlay (default): reads pages/page-NN.png
  - native:            reads pages/page-NN-native.png

Output: {out_dir}/{slug(title)}.pdf   (overlay mode)
        {out_dir}/{slug(title)}-native.pdf  (native mode)
or whatever path is given via --out.

Emits MEDIA: <pdf_path> on success (consistent with render_book.py convention).
Designed to run standalone — no image API cost, no OpenRouter calls.

Usage:
  uv run merge_pdf.py --story /path/to/story.json [--text-mode overlay|native]
                      [--out-dir DIR] [--out my-book.pdf]
"""

from __future__ import annotations
import argparse
import io
import json
import re
import sys
from pathlib import Path


def _load_story(story_path: Path) -> dict:
    with story_path.open() as f:
        return json.load(f)


def _slug(title: str) -> str:
    """Convert a book title to a safe filename slug."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "storybook"


def _to_rgb_png_bytes(png_path: Path) -> bytes:
    """
    Return PNG bytes suitable for img2pdf.

    img2pdf embeds images as-is (FlateDecode / lossless) which means it does not
    require a JPEG encoder — unlike Pillow's own PDF save which forces DCTDecode
    for RGB images. We only need Pillow here to handle the rare case where the
    final page was saved with an alpha channel (RGBA) that PDF cannot carry; for
    those we flatten to RGB and re-encode to PNG in memory. For standard RGB PNGs
    (the vast majority of rendered pages) we read the bytes directly from disk and
    never touch Pillow.
    """
    from PIL import Image

    img = Image.open(png_path)
    if img.mode == "RGB":
        # Fast path: read raw bytes; no recompression needed.
        return png_path.read_bytes()

    # Flatten alpha-carrying modes to opaque RGB.
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


def merge_pdf(
    story_path: Path,
    text_mode: str | None,
    out_dir: Path | None,
    out: Path | None,
) -> Path:
    """
    Build a multi-page PDF from the rendered page PNGs described by story.json.

    Args:
        story_path: Absolute path to story.json.
        text_mode:  Resolved text mode ("overlay" or "native"), or None to auto-detect.
        out_dir:    Directory that contains pages/ and where the PDF is written.
        out:        Explicit PDF output path (overrides default naming).

    Returns:
        Path to the written PDF.
    """
    import img2pdf

    story = _load_story(story_path)

    # Resolve text_mode: arg > story.json field > "native".
    resolved_mode = text_mode or story.get("text_mode", "native")
    suffix = "-native" if resolved_mode == "native" else ""

    # Output directory mirrors render_book.py:395 logic.
    resolved_out_dir = out_dir if out_dir is not None else story_path.parent
    pages_dir = resolved_out_dir / "pages"

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json.", file=sys.stderr)
        sys.exit(1)

    # Collect pages in story.json array order — do NOT glob (glob sweeps in
    # raw-page-NN.png intermediates and misorders past 99 pages).
    page_bytes_list: list[bytes] = []
    missing: list[int] = []
    for page in pages:
        page_num = page["page_num"]
        png_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
        if not png_path.exists():
            missing.append(page_num)
            continue
        page_bytes_list.append(_to_rgb_png_bytes(png_path))

    if missing:
        print(
            f"Warning: {len(missing)} page(s) not found and skipped: {missing}",
            file=sys.stderr,
        )

    if not page_bytes_list:
        print(
            f"ERROR: No rendered pages found in {pages_dir}. "
            "Run render_book.py first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine output PDF path.
    if out is not None:
        pdf_path = out
    else:
        title = story.get("title", "")
        filename = f"{_slug(title)}{suffix}.pdf"
        pdf_path = resolved_out_dir / filename

    # img2pdf embeds each PNG as FlateDecode (lossless) — no JPEG encoder needed.
    # Always rebuild: a stale PDF after a re-render is worse than a fresh one.
    pdf_bytes = img2pdf.convert(page_bytes_list)
    pdf_path.write_bytes(pdf_bytes)

    print(f"MEDIA: {pdf_path}")
    return pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge rendered storybook pages into a single PDF."
    )
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument(
        "--text-mode",
        dest="text_mode",
        choices=["overlay", "native"],
        default=None,
        help=(
            "Which rendered files to collect. "
            "overlay: pages/page-NN.png (default). "
            "native: pages/page-NN-native.png. "
            "If omitted, uses story.json's top-level 'text_mode' (default native)."
        ),
    )
    parser.add_argument(
        "--out-dir",
        help="Directory containing pages/ (default: same dir as story.json)",
    )
    parser.add_argument(
        "--out",
        help="Explicit output PDF path (overrides default slug naming)",
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    if not story_path.exists():
        print(f"ERROR: story.json not found: {story_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else None
    out = Path(args.out).resolve() if args.out else None

    pdf_path = merge_pdf(story_path, args.text_mode, out_dir, out)

    n = len(list(pdf_path.read_bytes()))  # size sanity only; no page-count without extra dep
    # Report completion; merge_pdf() already printed MEDIA: line.
    print(f"PDF written: {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
