#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Merge all rendered page images for a storybook into a fixed-layout EPUB3.

Reads the ordered page list from story.json and collects the final rendered
PNGs from {out_dir}/pages/. Works for all three text modes:
  - overlay: reads pages/page-NN.png
  - native:  reads pages/page-NN-native.png
  - long:    interleaves art and text pages — pages/page-NN-long.png followed by
             pages/page-NN-long-text.png (when the page has text); cover (page 1)
             is a single pages/page-01-long.png.

Output: {out_dir}/{slug(title)}.epub         (overlay mode)
        {out_dir}/{slug(title)}-native.epub  (native mode)
        {out_dir}/{slug(title)}-long.epub    (long mode)
or whatever path is given via --out.

The EPUB is EPUB3 fixed-layout (pre-paginated): each physical page is one full-bleed
image spread with viewport dimensions matching the image. In long mode, the EPUB nav
only lists art pages (the logical story pages); text pages immediately follow each art
page in the spine but do not appear as separate nav entries.

Page text from story.json rides along as the <img> alt attribute (accessible).
No external dependencies — built entirely from stdlib zipfile, struct, uuid, and
xml.sax.saxutils.

Emits MEDIA: <epub_path> on success (consistent with render_book.py convention).
Designed to run standalone — no image API cost, no OpenRouter calls.

Keep collection logic in sync with merge_pdf.py.

Usage:
  uv run merge_epub.py --story /path/to/story.json [--text-mode overlay|native|long]
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
    page_descs: list[tuple[str, int, int, bool]],  # (slug, width, height, is_cover_image)
) -> str:
    """Build the OPF package document for a fixed-layout EPUB3.

    page_descs is an ordered list of physical-page descriptors — one per physical
    page in spine order. In long mode this includes interleaved art + text pages.
    """
    manifest_items: list[str] = []
    spine_items: list[str] = []

    manifest_items.append(
        '    <item id="nav" href="nav.xhtml"'
        ' media-type="application/xhtml+xml" properties="nav"/>'
    )

    for slug, _w, _h, is_cover in page_descs:
        pid = slug
        iid = f"img-{slug}"
        cover_prop = ' properties="cover-image"' if is_cover else ""

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


def _nav_xhtml(title: str, nav_items: list[tuple[str, str]], lang: str) -> str:
    """Build the EPUB3 navigation document (required; not in spine).

    nav_items: ordered list of (slug, label) for pages that appear in the nav.
    In long mode only art pages (with nav labels) are listed; text pages are None-labeled
    and omitted from the nav, so readers see one entry per logical story page.
    """
    items: list[str] = []
    for slug, label in nav_items:
        items.append(
            f'        <li><a href="pages/{slug}.xhtml">{_esc(label)}</a></li>'
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


def _page_xhtml(slug: str, width: int, height: int, alt_text: str, lang: str) -> str:
    """Build the XHTML page document for one fixed-layout spread.

    slug is the internal page identifier, e.g. 'page-02' or 'page-02-text'.
    The <style> block uses plain string concatenation (not an f-string) to
    avoid the f-string brace trap: literal { } in CSS are replacement fields
    inside an f-string and cause NameError at runtime.
    """
    pid = slug
    style = "html,body{margin:0;padding:0;width:100%;height:100%}img{display:block;width:100%;height:100%}"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        f'<html xmlns="http://www.w3.org/1999/xhtml"'
        f' lang="{_esc(lang)}" xml:lang="{_esc(lang)}">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_esc(slug)}</title>\n"
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
        text_mode:  Resolved text mode ("overlay", "native", or "long"), or None to auto-detect.
        out_dir:    Directory that contains pages/ and where the EPUB is written.
        out:        Explicit EPUB output path (overrides default naming).

    Returns:
        Path to the written EPUB.
    """
    story = _load_story(story_path)

    # Resolve text_mode: arg > story.json field > "native".
    resolved_mode = text_mode or story.get("text_mode", "native")
    # Output filename suffix (3-way map; "long" gets its own literal suffix).
    out_suffix = {"native": "-native", "long": "-long"}.get(resolved_mode, "")

    # Output directory mirrors render_book.py logic.
    resolved_out_dir = out_dir if out_dir is not None else story_path.parent
    pages_dir = resolved_out_dir / "pages"

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json.", file=sys.stderr)
        sys.exit(1)

    # Build ordered physical-page descriptors.
    # Each descriptor: (slug, png_bytes, w, h, alt_text, nav_label_or_None)
    # slug: internal EPUB identifier, e.g. "page-02" or "page-02-text" (suffix-free inside EPUB).
    # nav_label_or_None: str = appears in EPUB nav; None = spine-only (text pages in long mode).
    #
    # Collect in story.json array order — do NOT glob (glob sweeps in raw-page-NN.png
    # intermediates and misorders past 99 pages). Keep in sync with merge_pdf.py.
    phys_pages: list[tuple[str, bytes, int, int, str, str | None]] = []
    missing: list[str] = []
    is_first_collected = True  # first non-skipped page gets the title as nav label

    if resolved_mode == "long":
        # Long mode: per logical page, art page + optional text page; cover is single.
        for page in pages:
            page_num = page["page_num"]
            nn = f"{page_num:02d}"
            page_text = page.get("text", "")

            if page_num == 1:  # Cover
                png_path = pages_dir / "page-01-long.png"
                if not png_path.exists():
                    missing.append("1 (cover)")
                    continue
                pb = png_path.read_bytes()
                w, h = _png_dims(pb, png_path)
                nav_label: str | None = story.get("title", "") if is_first_collected else f"Page {page_num}"
                is_first_collected = False
                phys_pages.append(("page-01", pb, w, h, page_text, nav_label))
            else:  # Body page
                art_path = pages_dir / f"page-{nn}-long.png"
                if not art_path.exists():
                    missing.append(str(page_num))
                    continue
                pb = art_path.read_bytes()
                w, h = _png_dims(pb, art_path)
                nav_label = story.get("title", "") if is_first_collected else f"Page {page_num}"
                is_first_collected = False
                # Art page — empty alt (text is on the separate text page).
                phys_pages.append((f"page-{nn}", pb, w, h, "", nav_label))

                if page_text.strip():
                    text_path = pages_dir / f"page-{nn}-long-text.png"
                    if text_path.exists():
                        tb = text_path.read_bytes()
                        tw, th = _png_dims(tb, text_path)
                        # Text page — carries the story text as alt; not in nav.
                        phys_pages.append((f"page-{nn}-text", tb, tw, th, page_text, None))
                    else:
                        missing.append(f"{page_num}-text")
    else:
        # Overlay / native: one physical page per logical page.
        file_suffix = "-native" if resolved_mode == "native" else ""
        for page in pages:
            page_num = page["page_num"]
            png_path = pages_dir / f"page-{page_num:02d}{file_suffix}.png"
            if not png_path.exists():
                missing.append(str(page_num))
                continue
            pb = png_path.read_bytes()
            w, h = _png_dims(pb, png_path)
            page_text = page.get("text", "")
            nav_label = story.get("title", "") if is_first_collected else f"Page {page_num}"
            is_first_collected = False
            phys_pages.append((f"page-{page_num:02d}", pb, w, h, page_text, nav_label))

    if missing:
        print(
            f"Warning: {len(missing)} page file(s) not found and skipped: {missing}",
            file=sys.stderr,
        )

    if not phys_pages:
        print(
            f"ERROR: No rendered pages found in {pages_dir}. "
            "Render pages first (storybook-render skill, render_book.py), then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine output EPUB path.
    if out is not None:
        epub_path = out
    else:
        title = story.get("title", "")
        filename = f"{_slug(title)}{out_suffix}.epub"
        epub_path = resolved_out_dir / filename

    # EPUB metadata.
    title = story.get("title", "")
    lang = story.get("language", "en")
    slug_str = _slug(title)
    identifier = "urn:uuid:" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, "urn:storybook:" + slug_str)
    )
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Derive OPF/nav structures from the physical-page list.
    # cover-image property goes to the first physical page (descriptor index 0).
    page_descs = [
        (slug, w, h, idx == 0)
        for idx, (slug, _pb, w, h, _alt, _label) in enumerate(phys_pages)
    ]
    nav_items = [
        (slug, label)
        for slug, _pb, _w, _h, _alt, label in phys_pages
        if label is not None
    ]

    # Build the EPUB zip.  Always rebuild: a stale EPUB after a re-render is
    # worse than a fresh one.
    #
    # ZipInfo date_time=(1980,1,1,0,0,0) makes all entries byte-stable across
    # runs except for the dcterms:modified string in content.opf.
    _DT = (1980, 1, 1, 0, 0, 0)

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
        zf.writestr(oi, _content_opf(title, lang, identifier, modified, page_descs))

        # 4. OEBPS/nav.xhtml
        ni = zipfile.ZipInfo("OEBPS/nav.xhtml", date_time=_DT)
        ni.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(ni, _nav_xhtml(title, nav_items, lang))

        # 5 & 6. Per-physical-page XHTML + PNG.
        # Internal slugs are suffix-free (e.g. "page-02", "page-02-text") — the
        # on-disk "-long"/"-native" suffix only disambiguates outside the container.
        for slug, png_bytes, w, h, alt_text, _label in phys_pages:
            xi = zipfile.ZipInfo(f"OEBPS/pages/{slug}.xhtml", date_time=_DT)
            xi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(xi, _page_xhtml(slug, w, h, alt_text, lang))

            # PNGs are already compressed; store without re-deflating.
            pi = zipfile.ZipInfo(f"OEBPS/images/{slug}.png", date_time=_DT)
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
