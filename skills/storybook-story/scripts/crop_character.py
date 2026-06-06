#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""
Crop one person from a multi-person source photo.

When a reference photo contains more than one person, run this script once per
character to produce a single-person crop. Set each character's `ref_image` to
its own crop — never to the original multi-person photo.

Usage:
  uv run crop_character.py --image /path/to/family.jpg \
      --box 0.05,0.10,0.48,0.95 --out {out_dir}/ref-mia.png

Arguments:
  --image PATH     Source photo (JPEG, PNG, WebP, or any Pillow-readable format).
                   If the photo is HEIC, convert it to JPEG or PNG first.
  --box L,T,R,B    Bounding box as four comma-separated fractions of image
                   width/height in [0, 1].  Example: 0.05,0.10,0.48,0.95
                   These are FRACTIONS, not pixel coordinates.
                   L = left edge, T = top edge, R = right edge, B = bottom edge.
  --out PATH       Output PNG path.  Parent directories are created automatically.
                   Existing files are silently overwritten (iterate freely).

Tips:
  - Use GENEROUS boxes — full person head-to-toe with margin.  Background context
    is harmless; a clipped head or missing hair weakens face-likeness anchoring.
  - After running, view the crop with the Read tool to verify the right person is
    captured and nothing is clipped.  If not, adjust the box and re-run.
  - Fractional coordinates are scale-invariant: they work regardless of whether
    the Read-tool preview shows the image at full or reduced resolution.
  - EXIF orientation is applied before cropping so phone-photo coordinates match
    what you see in the Read-tool preview.

Exit codes:
  0  Success.
  1  Cannot open or read the source image.
  2  Invalid --box argument (wrong format, out-of-range values, degenerate region).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

# Short-side pixel threshold below which face-likeness anchoring may be too weak.
MIN_FACE_PX = 200


def _parse_box(raw: str) -> tuple[float, float, float, float]:
    """Parse and validate the --box fraction string.

    Returns (left, top, right, bottom) as floats.
    Raises SystemExit(2) with a helpful message on any error.
    """
    parts = raw.strip().split(",")
    if len(parts) != 4:
        print(
            f"ERROR: --box must be four comma-separated numbers 'left,top,right,bottom'; "
            f"got {raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        l, t, r, b = (float(p.strip()) for p in parts)
    except ValueError:
        print(
            f"ERROR: --box values must be numbers; got {raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    out_of_range = [(name, v) for name, v in [("L", l), ("T", t), ("R", r), ("B", b)] if not (0.0 <= v <= 1.0)]
    if out_of_range:
        names = ", ".join(f"{n}={v}" for n, v in out_of_range)
        print(
            f"ERROR: --box values are fractions of image size in [0, 1] (not pixels); "
            f"out-of-range: {names}.  Got {raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if l >= r:
        print(
            f"ERROR: --box left ({l}) must be less than right ({r}); got {raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if t >= b:
        print(
            f"ERROR: --box top ({t}) must be less than bottom ({b}); got {raw!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return l, t, r, b


def crop_character(image_path: str | Path, box_raw: str, out_path: str | Path) -> None:
    """Crop one person from *image_path* using fractional *box_raw* and save to *out_path*.

    This is the core operation: open the source photo, apply EXIF orientation so
    that fractional coordinates match what the Read tool displays, validate the
    bounding box, crop, convert to RGB (stripping transparency / palette modes that
    would confuse downstream Gemini calls), and save as PNG.

    The output file is always overwritten — no skip-if-exists logic.  This keeps the
    adjust-box-and-rerun loop frictionless (unlike the paid scripts, there is no API
    cost to protect against).

    Raises SystemExit on any unrecoverable error (exit 1 for I/O, exit 2 for bad args).
    """
    image_path = Path(image_path)
    out_path = Path(out_path)

    # -- Open source image --------------------------------------------------
    try:
        img = Image.open(image_path)
        img.load()  # force decode so errors surface here, not later
    except FileNotFoundError:
        print(
            f"ERROR: image not found: {image_path}\n"
            f"  If this is a HEIC photo, convert it to JPEG or PNG first.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except Exception as exc:
        print(
            f"ERROR: cannot open image: {image_path}\n"
            f"  {exc}\n"
            f"  If this is a HEIC photo, convert it to JPEG or PNG first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # -- Apply EXIF orientation so pixel coords match the Read-tool preview --
    img = ImageOps.exif_transpose(img)

    width, height = img.size

    # -- Validate and convert fractional box to pixel coordinates ------------
    l_f, t_f, r_f, b_f = _parse_box(box_raw)
    px_box = (
        round(l_f * width),
        round(t_f * height),
        round(r_f * width),
        round(b_f * height),
    )

    # -- Crop ----------------------------------------------------------------
    crop = img.crop(px_box)
    crop_w, crop_h = crop.size

    # -- Warn on small crops (weak face-likeness anchor) ---------------------
    if min(crop_w, crop_h) < MIN_FACE_PX:
        print(
            f"Warning: crop is only {crop_w}x{crop_h}px — face-likeness anchoring may be "
            f"weak.  Use a larger bounding box or a higher-resolution source photo.",
            file=sys.stderr,
        )

    # -- Mode-convert: Gemini expects RGB or RGBA; drop palette/CMYK --------
    if crop.mode in ("P", "PA"):
        crop = crop.convert("RGBA" if "A" in crop.getbands() else "RGB")
    elif crop.mode == "CMYK":
        crop = crop.convert("RGB")
    # L (greyscale) and LA are fine as-is; Pillow PNG supports them.

    # -- Write output --------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, format="PNG")

    print(f"Cropped {image_path.name} ({width}x{height}) → {crop_w}x{crop_h}px → {out_path}")
    print(f"MEDIA: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crop one person from a multi-person source photo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  uv run crop_character.py --image family.jpg \\\n"
            "      --box 0.05,0.10,0.48,0.95 --out ref-mia.png\n\n"
            "See the module docstring for full usage notes."
        ),
    )
    parser.add_argument("--image", required=True, metavar="PATH", help="Source photo path.")
    parser.add_argument(
        "--box",
        required=True,
        metavar="L,T,R,B",
        help="Bounding box as fractions [0,1]: left,top,right,bottom.",
    )
    parser.add_argument("--out", required=True, metavar="PATH", help="Output PNG path.")

    args = parser.parse_args()
    crop_character(args.image, args.box, args.out)


if __name__ == "__main__":
    main()
