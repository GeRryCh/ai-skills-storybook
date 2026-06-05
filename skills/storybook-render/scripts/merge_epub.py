#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Merge all rendered page images for a storybook into a fixed-layout EPUB3.

Reads the ordered page list from story.json and collects the final rendered
PNGs from {out_dir}/pages/. Works for both text modes:
  - overlay (default): reads pages/page-NN.png
  - native:            reads pages/page-NN-native.png

Output: {out_dir}/{slug(title)}.epub   (overlay mode)
        {out_dir}/{slug(title)}-native.epub  (native mode)
or whatever path is given via --out.

The EPUB is EPUB3 fixed-layout (pre-paginated): each page is one full-bleed
image spread with viewport dimensions matching the image. Page text from
story.json rides along as the <img> alt attribute (accessible, but text is
baked into the rendered PNG itself in overlay mode, or rendered natively by
the image model in native mode). No external dependencies — built entirely
from stdlib zipfile, struct, uuid, and xml.sax.saxutils.

Emits MEDIA: <epub_path> on success (consistent with render_book.py convention).
Designed to run standalone — no image API cost, no OpenRouter calls.

Usage:
  uv run merge_epub.py --story /path/to/story.json [--text-mode overlay|native]
                       [--out-dir DIR] [--out my-book.epub]
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


# ---------------------------------------------------------------------------
# Helpers shared with merge_pdf.py (keep in sync)
# ---------------------------------------------------------------------------

def _load_story(story_path: Path) -> dict:
    with story_path.open() as f:
        return json.load(f)


def _slug(title: str) -> str:
    """Convert a book title to a safe filename slug."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "storybook"


# ---------------------------------------------------------------------------
# EPUB-specific helpers
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_dims(data: bytes, src: Path) -> tuple[int, int]:
    """Return (width, height) from PNG IHDR chunk bytes.

    Validates the 8-byte PNG signature and reads dimensions from the fixed
    IHDR offset (bytes 16-24). Raises ValueError naming the file on mismatch
    so a corrupt/non-PNG file can never yield garbage viewport dimensions.
    """
    if data[:8] != _PNG_SIGNATURE:
        raise ValueError(
            f"{src} is not a valid PNG (bad signature: {data[:8]!r})"
        )
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _esc(s: str) -> str:
    """Attribute-safe XML escaping for user-supplied text (alt, titles)."""
    return escape(s, {'"': "&quot;"})


# ---------------------------------------------------------------------------
# EPUB XML / XHTML template builders
# ---------------------------------------------------------------------------

def _container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0"'
        ' xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf"'
        ' media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )


def _content_opf(
    title: str,
    lang: str,
    identifier: str,
    modified: str,
    page_entries: list[tuple[int, int, int]],  # (page_num, width, height)
) -> str:
    """Build the OPF package document for a fixed-layout EPUB3."""
    manifest_items: list[str] = []
    spine_items: list[str] = []

    manifest_items.append(
        '    <item id="nav" href="nav.xhtml"'
        ' media-type="application/xhtml+xml" properties="nav"/>'
    )

    for idx, (page_num, _w, _h) in enumerate(page_entries):
        pid = f"page-{page_num:02d}"
        iid = f"img-{page_num:02d}"
        cover_prop = ' properties="cover-image"' if idx == 0 else ""

        manifest_items.append(
            f'    <item id="{pid}" href="pages/{pid}.xhtml"'
            f' media-type="application/xhtml+xml"/>'
        )
        manifest_items.append(
            f'    <item id="{iid}" href="images/{pid}.png"'
            f' media-type="image/png"{cover_prop}/>'
        )
        spine_items.append(f'    <itemref idref="{pid}"/>')

    manifest_block = "\n".join(manifest_items)
    spine_block = "\n".join(spine_items)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
        ' unique-identifier="book-id"\n'
        '         prefix="rendition: http://www.idpf.org/vocab/rendition/#">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="book-id">{_esc(identifier)}</dc:identifier>\n'
        f'    <dc:title>{_esc(title)}</dc:title>\n'
        f'    <dc:language>{_esc(lang)}</dc:language>\n'
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        '    <meta property="rendition:layout">pre-paginated</meta>\n'
        '    <meta property="rendition:orientation">auto</meta>\n'
        '    <meta property="rendition:spread">auto</meta>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        f"{manifest_block}\n"
        "  </manifest>\n"
        "  <spine>\n"
        f"{spine_block}\n"
        "  </spine>\n"
        "</package>\n"
    )


def _nav_xhtml(title: str, page_nums: list[int], lang: str) -> str:
    """Build the EPUB3 navigation document (required; not in spine)."""
    items: list[str] = []
    for idx, n in enumerate(page_nums):
        label = _esc(title) if idx == 0 else f"Page {n}"
        items.append(
            f'        <li><a href="pages/page-{n:02d}.xhtml">{label}</a></li>'
        )
    toc_items = "\n".join(items)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml"'
        f' xmlns:epub="http://www.idpf.org/2007/ops"'
        f' lang="{_esc(lang)}" xml:lang="{_esc(lang)}">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_esc(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        '  <nav epub:type="toc">\n'
        "    <ol>\n"
        f"{toc_items}\n"
        "    </ol>\n"
        "  </nav>\n"
        "</body>\n"
        "</html>\n"
    )


def _page_xhtml(page_num: int, width: int, height: int, alt_text: str, lang: str) -> str:
    """Build the XHTML page document for one fixed-layout spread.

    The <style> block uses plain string concatenation (not an f-string) to
    avoid the f-string brace trap: literal { } in CSS are replacement fields
    inside an f-string and cause NameError at runtime.
    """
    pid = f"page-{page_num:02d}"
    style = "html,body{margin:0;padding:0;width:100%;height:100%}img{display:block;width:100%;height:100%}"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        f'<html xmlns="http://www.w3.org/1999/xhtml"'
        f' lang="{_esc(lang)}" xml:lang="{_esc(lang)}">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>Page {page_num}</title>\n"
        f'  <meta name="viewport" content="width={width}, height={height}"/>\n'
        f"  <style>{style}</style>\n"
        "</head>\n"
        "<body>\n"
        f'  <img src="../images/{pid}.png" alt="{_esc(alt_text)}"/>\n'
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Core assembler
# ---------------------------------------------------------------------------

def merge_epub(
    story_path: Path,
    text_mode: str | None,
    out_dir: Path | None,
    out: Path | None,
) -> Path:
    """
    Build a fixed-layout EPUB3 from the rendered page PNGs described by story.json.

    Args:
        story_path: Absolute path to story.json.
        text_mode:  Resolved text mode ("overlay" or "native"), or None to auto-detect.
        out_dir:    Directory that contains pages/ and where the EPUB is written.
        out:        Explicit EPUB output path (overrides default naming).

    Returns:
        Path to the written EPUB.
    """
    story = _load_story(story_path)

    # Resolve text_mode: arg > story.json field > "native".
    resolved_mode = text_mode or story.get("text_mode", "native")
    suffix = "-native" if resolved_mode == "native" else ""

    # Output directory mirrors render_book.py logic.
    resolved_out_dir = out_dir if out_dir is not None else story_path.parent
    pages_dir = resolved_out_dir / "pages"

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json.", file=sys.stderr)
        sys.exit(1)

    # Collect pages in story.json array order — do NOT glob (glob sweeps in
    # raw-page-NN.png intermediates and misorders past 99 pages).
    page_data: list[tuple[int, str, bytes, int, int]] = []  # (page_num, text, png_bytes, w, h)
    missing: list[int] = []
    for page in pages:
        page_num = page["page_num"]
        png_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
        if not png_path.exists():
            missing.append(page_num)
            continue
        png_bytes = png_path.read_bytes()
        w, h = _png_dims(png_bytes, png_path)
        page_text = page.get("text", "")
        page_data.append((page_num, page_text, png_bytes, w, h))

    if missing:
        print(
            f"Warning: {len(missing)} page(s) not found and skipped: {missing}",
            file=sys.stderr,
        )

    if not page_data:
        print(
            f"ERROR: No rendered pages found in {pages_dir}. "
            "Run render_book.py first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine output EPUB path.
    if out is not None:
        epub_path = out
    else:
        title = story.get("title", "")
        filename = f"{_slug(title)}{suffix}.epub"
        epub_path = resolved_out_dir / filename

    # EPUB metadata.
    title = story.get("title", "")
    lang = story.get("language", "en")
    slug = _slug(title)
    identifier = "urn:uuid:" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, "urn:storybook:" + slug)
    )
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build the EPUB zip.  Always rebuild: a stale EPUB after a re-render is
    # worse than a fresh one.
    #
    # ZipInfo date_time=(1980,1,1,0,0,0) makes all entries byte-stable across
    # runs except for the dcterms:modified string in content.opf.
    _DT = (1980, 1, 1, 0, 0, 0)

    page_entries = [(pn, w, h) for pn, _txt, _bytes, w, h in page_data]
    page_nums = [pn for pn, *_ in page_data]

    with zipfile.ZipFile(epub_path, "w") as zf:
        # 1. mimetype — MUST be first entry, STORED (uncompressed), no trailing newline.
        mi = zipfile.ZipInfo("mimetype", date_time=_DT)
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, "application/epub+zip")

        # 2. META-INF/container.xml
        ci = zipfile.ZipInfo("META-INF/container.xml", date_time=_DT)
        ci.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(ci, _container_xml())

        # 3. OEBPS/content.opf
        oi = zipfile.ZipInfo("OEBPS/content.opf", date_time=_DT)
        oi.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(oi, _content_opf(title, lang, identifier, modified, page_entries))

        # 4. OEBPS/nav.xhtml
        ni = zipfile.ZipInfo("OEBPS/nav.xhtml", date_time=_DT)
        ni.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(ni, _nav_xhtml(title, page_nums, lang))

        # 5 & 6. Per-page XHTML + PNG (internal names are suffix-free — the
        # on-disk suffix only disambiguates overlay vs native outside the container).
        for page_num, page_text, png_bytes, w, h in page_data:
            pid = f"page-{page_num:02d}"

            xi = zipfile.ZipInfo(f"OEBPS/pages/{pid}.xhtml", date_time=_DT)
            xi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(xi, _page_xhtml(page_num, w, h, page_text, lang))

            # PNGs are already compressed; store without re-deflating.
            pi = zipfile.ZipInfo(f"OEBPS/images/{pid}.png", date_time=_DT)
            pi.compress_type = zipfile.ZIP_STORED
            zf.writestr(pi, png_bytes)

    print(f"MEDIA: {epub_path}")
    return epub_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge rendered storybook pages into a fixed-layout EPUB3."
    )
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument(
        "--text-mode",
        dest="text_mode",
        choices=["overlay", "native"],
        default=None,
        help=(
            "Which rendered files to collect. "
            "overlay: pages/page-NN.png. "
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
        help="Explicit output EPUB path (overrides default slug naming)",
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    if not story_path.exists():
        print(f"ERROR: story.json not found: {story_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else None
    out = Path(args.out).resolve() if args.out else None

    epub_path = merge_epub(story_path, args.text_mode, out_dir, out)
    print(f"EPUB written: {epub_path} ({epub_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
