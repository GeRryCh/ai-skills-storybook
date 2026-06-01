#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""
Overlay story text onto a children's book page image.

Usage (CLI):
  uv run overlay_text.py --image page.png --text "Once upon a time..." \
      --placement bottom --out page-final.png [--font reader|display] [--color dark|light]

Importable:
  from overlay_text import overlay
  overlay("page.png", "Once upon a time...", "bottom", "page-final.png")
"""

from __future__ import annotations
import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SKILL_DIR = Path(__file__).parent.parent
FONTS = {
    "reader": SKILL_DIR / "assets" / "fonts" / "Andika-Regular.ttf",
    "display": SKILL_DIR / "assets" / "fonts" / "PatrickHand-Regular.ttf",
}

# Text block occupies this fraction of image height
TEXT_ZONE_FRACTION = 0.25
# Horizontal padding as fraction of image width
H_PAD_FRACTION = 0.05
# Vertical padding inside the text box (pixels). Bottom is larger to visually
# balance the feather blur that adds perceived space at the top edge.
V_PAD_TOP = 40
V_PAD_BOTTOM = 56
# Horizontal inner padding: gap between text and box left/right edges (pixels)
H_INNER_PAD = 40
# Min/max font sizes in pixels
MIN_FONT_PX = 28
MAX_FONT_PX = 72
# Background box alpha (0=transparent, 255=opaque)
BOX_ALPHA = 205
BOX_RADIUS = 36
# Feather radius (px) for the soft fade between panel and image. 0 = hard edge.
FEATHER_PX = 14
# Margin between text box and image edge as fraction of image height.
# Gives the feather room to fade instead of clipping at the frame.
EDGE_MARGIN_FRACTION = 0.04


def _normalize_family(name: str) -> str:
    """Lowercase, keep only alphanumerics — so 'Patrick Hand' == 'PatrickHand'."""
    return "".join(c for c in name.lower() if c.isalnum())


def _bundled_font_path(name: str) -> Path | None:
    """Match a requested family name against a bundled .ttf in assets/fonts/.

    Bundled OFL fonts always win over a same-named system font, so 'Andika' /
    'Patrick Hand' resolve to the shipped files regardless of what's installed.
    """
    target = _normalize_family(name)
    fonts_dir = SKILL_DIR / "assets" / "fonts"
    for ttf in sorted(fonts_dir.glob("*.ttf")):
        stem = ttf.stem.lower().removesuffix("-regular")
        if _normalize_family(stem) == target:
            return ttf
    return None


def _resolve_font_ref(role: str, name: str | None = None) -> str:
    """Resolve a role + optional family name to a truetype reference (path or system name).

    Resolution order when an explicit family `name` is given:
      1. bundled asset matching the name (assets/fonts/)
      2. system-installed font (PIL searches OS font dirs by family name)
      3. bundled default for the role (reader -> Andika, display -> PatrickHand) + warning

    Rendering is rasterized to pixels, so using a system font (e.g. Arial) does not
    redistribute the font file. Unresolvable names never crash the render. Resolved
    once per overlay so the not-found warning fires at most once.
    """
    if name:
        bundled = _bundled_font_path(name)
        if bundled is not None:
            return str(bundled)
        for candidate in (name, f"{name}.ttf"):
            try:
                ImageFont.truetype(candidate, 12)  # probe OS font dirs
                return candidate
            except OSError:
                continue
        print(
            f"Warning: font {name!r} not found (no bundled asset, not a system font); "
            f"falling back to the bundled {role!r} font.",
            file=sys.stderr,
        )
    return str(FONTS.get(role, FONTS["reader"]))


def _load_font(font_ref: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_ref, size)


def _word_wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _pick_font_size(img_w: int, img_h: int, word_count: int, font_ref: str) -> int:
    zone_h = img_h * TEXT_ZONE_FRACTION
    max_w = int(img_w * (1 - 2 * H_PAD_FRACTION))
    # Start large and shrink until text fits in zone. Measure with the *resolved*
    # font so sizing matches what is actually rendered.
    for size in range(MAX_FONT_PX, MIN_FONT_PX - 1, -2):
        font = _load_font(font_ref, size)
        dummy_img = Image.new("RGBA", (img_w, img_h))
        draw = ImageDraw.Draw(dummy_img)
        lines = _word_wrap("X " * word_count, font, max_w, draw)
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 8
        total_h = len(lines) * line_h + V_PAD_TOP + V_PAD_BOTTOM
        if total_h <= zone_h:
            return size
    return MIN_FONT_PX


def _rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def overlay(
    image_path: str | Path,
    text: str,
    placement: str,
    out_path: str | Path,
    font: str = "reader",
    color: str = "dark",
    box_alpha: int = BOX_ALPHA,
    feather: int = FEATHER_PX,
    font_name: str | None = None,
) -> Path:
    """
    Composite text onto an image in the top or bottom zone.

    Args:
        image_path: Source PNG/JPG.
        text: Story text. Empty string = no overlay, just copies file.
        placement: 'top' or 'bottom'.
        out_path: Destination path.
        font: role 'reader' or 'display' — selects the bundled fallback font.
        color: 'dark' (near-black text) or 'light' (near-white text).
        font_name: optional explicit family name (e.g. 'Arial'); resolved via bundled
            asset -> system font -> bundled role fallback. None = use the role font.

    Returns:
        Path to written file.
    """
    out_path = Path(out_path)
    image_path = Path(image_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(image_path).convert("RGBA")
    if not text.strip():
        img.convert("RGB").save(out_path)
        return out_path

    w, h = img.size
    word_count = len(text.split())
    font_ref = _resolve_font_ref(font, font_name)
    font_size = _pick_font_size(w, h, word_count, font_ref)
    pil_font = _load_font(font_ref, font_size)

    max_text_w = int(w * (1 - 2 * H_PAD_FRACTION))
    h_pad = int(w * H_PAD_FRACTION)

    measure = ImageDraw.Draw(img)
    lines = _word_wrap(text, pil_font, max_text_w, measure)
    line_bbox = measure.textbbox((0, 0), "Ag", font=pil_font)
    line_h = line_bbox[3] - line_bbox[1] + 8
    text_block_h = len(lines) * line_h
    box_h = text_block_h + V_PAD_TOP + V_PAD_BOTTOM

    zone_h = int(h * TEXT_ZONE_FRACTION)
    # Clamp box height to zone
    box_h = min(box_h, zone_h - 8)

    edge_margin = int(h * EDGE_MARGIN_FRACTION)
    if placement == "top":
        box_y0 = edge_margin
    else:
        box_y0 = h - box_h - edge_margin

    box_x0 = h_pad - H_INNER_PAD
    box_x1 = w - h_pad + H_INNER_PAD
    box_y1 = box_y0 + box_h

    # Build the panel on its own alpha mask, then blur the mask so the panel
    # fades softly into the image instead of ending at a hard edge. The blur is
    # applied only to the panel mask — the text is composited separately and
    # stays crisp.
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [box_x0, box_y0, box_x1, box_y1], radius=BOX_RADIUS, fill=box_alpha
    )
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))

    panel = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    panel.putalpha(mask)
    img = Image.alpha_composite(img, panel)

    # Sharp text layer on top of the blended panel.
    text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_color = (30, 30, 30, 255) if color == "dark" else (245, 245, 245, 255)
    text_y = box_y0 + V_PAD_TOP
    for line in lines:
        text_draw.text((h_pad, text_y), line, font=pil_font, fill=text_color)
        text_y += line_h
        if text_y > box_y1 - V_PAD_BOTTOM:
            break

    composed = Image.alpha_composite(img, text_layer)
    composed.convert("RGB").save(out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Overlay story text onto a page image.")
    parser.add_argument("--image", required=True, help="Source image path")
    parser.add_argument("--text", required=True, help="Text to overlay")
    parser.add_argument("--placement", choices=["top", "bottom"], default="bottom")
    parser.add_argument("--out", required=True, help="Output image path")
    parser.add_argument("--font", choices=["reader", "display"], default="reader",
                        help="Font role; selects the bundled fallback font")
    parser.add_argument("--font-name", default=None,
                        help="Explicit font family (e.g. 'Arial'); resolved via bundled asset "
                             "-> system font -> bundled role fallback")
    parser.add_argument("--color", choices=["dark", "light"], default="dark")
    parser.add_argument("--box-alpha", type=int, default=BOX_ALPHA,
                        help=f"Panel opacity 0-255 (default {BOX_ALPHA}; lower = more transparent)")
    parser.add_argument("--feather", type=int, default=FEATHER_PX,
                        help=f"Edge blur radius in px (default {FEATHER_PX}; 0 = hard edge)")
    args = parser.parse_args()

    result = overlay(
        args.image, args.text, args.placement, args.out, args.font, args.color,
        box_alpha=args.box_alpha, feather=args.feather, font_name=args.font_name,
    )
    print(f"Saved: {result}")


if __name__ == "__main__":
    main()
