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
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SKILL_DIR = Path(__file__).parent.parent
FONTS = {
    "reader": SKILL_DIR / "assets" / "fonts" / "Andika-Regular.ttf",
    "display": SKILL_DIR / "assets" / "fonts" / "PatrickHand-Regular.ttf",
}

# Text block occupies this fraction of image height
TEXT_ZONE_FRACTION = 0.25
# Horizontal padding as fraction of image width
H_PAD_FRACTION = 0.05
# Vertical padding inside the text box (pixels)
V_PAD = 24
# Min/max font sizes in pixels
MIN_FONT_PX = 28
MAX_FONT_PX = 72
# Background box alpha (0=transparent, 255=opaque)
BOX_ALPHA = 210
BOX_RADIUS = 18


def _load_font(font_key: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS.get(font_key, FONTS["reader"])
    return ImageFont.truetype(str(path), size)


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


def _pick_font_size(img_w: int, img_h: int, word_count: int, font_key: str) -> int:
    zone_h = img_h * TEXT_ZONE_FRACTION
    max_w = int(img_w * (1 - 2 * H_PAD_FRACTION))
    # Start large and shrink until text fits in zone
    for size in range(MAX_FONT_PX, MIN_FONT_PX - 1, -2):
        font = _load_font(font_key, size)
        dummy_img = Image.new("RGBA", (img_w, img_h))
        draw = ImageDraw.Draw(dummy_img)
        lines = _word_wrap("X " * word_count, font, max_w, draw)
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 8
        total_h = len(lines) * line_h + 2 * V_PAD
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
) -> Path:
    """
    Composite text onto an image in the top or bottom zone.

    Args:
        image_path: Source PNG/JPG.
        text: Story text. Empty string = no overlay, just copies file.
        placement: 'top' or 'bottom'.
        out_path: Destination path.
        font: 'reader' (Andika) or 'display' (PatrickHand).
        color: 'dark' (near-black text) or 'light' (near-white text).

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
    font_size = _pick_font_size(w, h, word_count, font)
    pil_font = _load_font(font, font_size)

    max_text_w = int(w * (1 - 2 * H_PAD_FRACTION))
    h_pad = int(w * H_PAD_FRACTION)

    overlay_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay_layer)

    lines = _word_wrap(text, pil_font, max_text_w, draw)
    line_bbox = draw.textbbox((0, 0), "Ag", font=pil_font)
    line_h = line_bbox[3] - line_bbox[1] + 8
    text_block_h = len(lines) * line_h
    box_h = text_block_h + 2 * V_PAD

    zone_h = int(h * TEXT_ZONE_FRACTION)
    # Clamp box height to zone
    box_h = min(box_h, zone_h - 8)

    if placement == "top":
        box_y0 = 8
    else:
        box_y0 = h - box_h - 8

    box_x0 = h_pad - 8
    box_x1 = w - h_pad + 8
    box_y1 = box_y0 + box_h

    box_fill = (255, 255, 255, BOX_ALPHA)
    _rounded_rect(draw, (box_x0, box_y0, box_x1, box_y1), BOX_RADIUS, box_fill)

    text_color = (30, 30, 30, 255) if color == "dark" else (245, 245, 245, 255)
    text_y = box_y0 + V_PAD
    for line in lines:
        draw.text((h_pad, text_y), line, font=pil_font, fill=text_color)
        text_y += line_h
        if text_y > box_y1 - V_PAD:
            break

    composed = Image.alpha_composite(img, overlay_layer)
    composed.convert("RGB").save(out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Overlay story text onto a page image.")
    parser.add_argument("--image", required=True, help="Source image path")
    parser.add_argument("--text", required=True, help="Text to overlay")
    parser.add_argument("--placement", choices=["top", "bottom"], default="bottom")
    parser.add_argument("--out", required=True, help="Output image path")
    parser.add_argument("--font", choices=["reader", "display"], default="reader")
    parser.add_argument("--color", choices=["dark", "light"], default="dark")
    args = parser.parse_args()

    result = overlay(args.image, args.text, args.placement, args.out, args.font, args.color)
    print(f"Saved: {result}")


if __name__ == "__main__":
    main()
