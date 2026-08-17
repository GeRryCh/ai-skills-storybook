#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow", "img2pdf"]
# ///
"""
Merge all rendered page images for a storybook into a single PDF.

Reads the ordered page list from story.json and collects the final rendered
PNGs from {out_dir}/pages/. Works for all three text modes:
  - overlay: reads pages/page-NN.png
  - native:  reads pages/page-NN-native.png
  - long:    interleaves art and text pages — pages/page-NN-long.png followed by
             pages/page-NN-long-text.png (when the page has text); cover (page 1)
             is a single page/page-01-long.png.

Output: {out_dir}/{slug(title)}.pdf         (overlay mode)
        {out_dir}/{slug(title)}-native.pdf  (native mode)
        {out_dir}/{slug(title)}-long.pdf    (long mode)
or whatever path is given via --out.

Emits MEDIA: <pdf_path> on success (consistent with render_book.py convention).
Designed to run standalone — no image API cost, no OpenRouter calls.

Keep collection logic in sync with merge_epub.py.

Usage:
  uv run merge_pdf.py --story /path/to/story.json [--text-mode overlay|native|long]
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


def _to_rgb_png_bytes(png_path: Path) -> tuple[bytes, tuple[int, int]]:
    """
    Return (PNG bytes suitable for img2pdf, (width, height)).

    img2pdf embeds images as-is (FlateDecode / lossless) which means it does not
    require a JPEG encoder — unlike Pillow's own PDF save which forces DCTDecode
    for RGB images. We only need Pillow here to handle the rare case where the
    final page was saved with an alpha channel (RGBA) that PDF cannot carry; for
    those we flatten to RGB and re-encode to PNG in memory. For standard RGB PNGs
    (the vast majority of rendered pages) we read the bytes directly from disk and
    never touch Pillow. The size is read from the same Image.open() call either
    way (PER-88 — feeds the mixed-page-size warning in merge_pdf()).
    """
    from PIL import Image

    img = Image.open(png_path)
    size = img.size
    if img.mode == "RGB":
        # Fast path: read raw bytes; no recompression needed.
        return png_path.read_bytes(), size

    # Flatten alpha-carrying modes to opaque RGB.
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue(), size


def _warn_mixed_page_sizes(page_sizes: list[tuple[str, tuple[int, int]]]) -> None:
    """Warn (never fail) when rendered pages don't share one pixel size (PER-88).

    A bound PDF with mixed page dimensions looks inconsistent page to page — the
    unset-aspect_ratio default used to let the model pick framing per call, which
    produced exactly this. Compares raw (width, height), not reduced aspect ratio,
    so it also catches same-ratio-different-resolution mixes.
    """
    by_size: dict[tuple[int, int], list[str]] = {}
    for label, size in page_sizes:
        by_size.setdefault(size, []).append(label)
    if len(by_size) <= 1:
        return
    lines = [
        f"  {w}x{h}: page(s) {', '.join(labels)}"
        for (w, h), labels in by_size.items()
    ]
    print(
        "Warning: rendered pages have mixed image dimensions — the PDF will not "
        "have a consistent page shape:\n" + "\n".join(lines),
        file=sys.stderr,
    )


def _page_text_mode(story: dict, page: dict, cli_mode: str | None) -> str:
    """Resolve the effective text mode for one page.

    Precedence: CLI --text-mode > page 'text_mode' field > story top-level 'text_mode' > "native".
    Keep in sync with resolve_text_mode() in render_book.py and _page_text_mode() in merge_epub.py
    (the skills share no module; all three copies must stay identical).
    """
    page_mode = page.get("text_mode")
    return cli_mode or page_mode or story.get("text_mode") or "native"


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
        text_mode:  CLI override applied to every page, or None to use per-page/book-level story fields.
        out_dir:    Directory that contains pages/ and where the PDF is written.
        out:        Explicit PDF output path (overrides default naming).

    Returns:
        Path to the written PDF.
    """
    import img2pdf

    story = _load_story(story_path)

    # Output filename suffix reflects the book-level mode (CLI > story field > "native").
    # Per-page text_mode fields are honored for file selection below, but the output
    # filename uses the book-wide resolved mode so it stays predictable and stable.
    resolved_mode = text_mode or story.get("text_mode", "native")
    out_suffix = {"native": "-native", "long": "-long"}.get(resolved_mode, "")

    # Output directory mirrors render_book.py logic.
    resolved_out_dir = out_dir if out_dir is not None else story_path.parent
    pages_dir = resolved_out_dir / "pages"

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json.", file=sys.stderr)
        sys.exit(1)

    # Collect physical pages in reading order — do NOT glob (glob sweeps in
    # raw-page-NN.png intermediates and misorders past 99 pages).
    # Keep collection logic in sync with merge_epub.py.
    # Each page's effective mode is resolved individually (CLI > page field > story field > native).
    page_bytes_list: list[bytes] = []
    page_sizes: list[tuple[str, tuple[int, int]]] = []
    missing: list[str] = []

    for page in pages:
        page_num = page["page_num"]
        nn = f"{page_num:02d}"
        page_mode = _page_text_mode(story, page, text_mode)

        if page_mode == "long":
            # Long mode: art page + optional text page; cover (page 1) is a single combined page.
            if page_num == 1:
                png_path = pages_dir / "page-01-long.png"
                if not png_path.exists():
                    missing.append("1 (cover)")
                    continue
                data, size = _to_rgb_png_bytes(png_path)
                page_bytes_list.append(data)
                page_sizes.append(("1 (cover)", size))
            else:
                art_path = pages_dir / f"page-{nn}-long.png"
                if not art_path.exists():
                    missing.append(str(page_num))
                    continue
                data, size = _to_rgb_png_bytes(art_path)
                page_bytes_list.append(data)
                page_sizes.append((str(page_num), size))
                if page.get("text", "").strip():
                    text_path = pages_dir / f"page-{nn}-long-text.png"
                    if text_path.exists():
                        data, size = _to_rgb_png_bytes(text_path)
                        page_bytes_list.append(data)
                        page_sizes.append((f"{page_num}-text", size))
                    else:
                        missing.append(f"{page_num}-text")
        else:
            # Overlay / native: one physical page per logical page.
            suffix = "-native" if page_mode == "native" else ""
            png_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
            if not png_path.exists():
                missing.append(str(page_num))
                continue
            data, size = _to_rgb_png_bytes(png_path)
            page_bytes_list.append(data)
            page_sizes.append((str(page_num), size))

    if missing:
        print(
            f"Warning: {len(missing)} page file(s) not found and skipped: {missing}",
            file=sys.stderr,
        )

    _warn_mixed_page_sizes(page_sizes)

    if not page_bytes_list:
        print(
            f"ERROR: No rendered pages found in {pages_dir}. "
            "Render pages first (storybook-render skill, render_book.py), then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine output PDF path.
    if out is not None:
        pdf_path = out
    else:
        title = story.get("title", "")
        filename = f"{_slug(title)}{out_suffix}.pdf"
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
        choices=["overlay", "native", "long"],
        default=None,
        help=(
            "Which rendered files to collect. "
            "overlay: pages/page-NN.png. "
            "native: pages/page-NN-native.png. "
            "long: interleaves pages/page-NN-long.png + pages/page-NN-long-text.png "
            "per body page; cover is pages/page-01-long.png. "
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
