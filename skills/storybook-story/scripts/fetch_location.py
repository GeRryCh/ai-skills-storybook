#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""
Download and validate a real-place reference photo for story locations.

Stage 1 finds a suitable image URL via the Perplexity MCP (preferred:
Wikimedia Commons freely-licensed photos), then calls this script to
download and verify it before adding the path as `ref_image` on a cast
entry with `"kind": "location"` in story.json.

Usage:
  uv run fetch_location.py \
      --url "https://commons.wikimedia.org/wiki/Special:FilePath/Tour_Eiffel_Wikimedia_Commons.jpg?width=1600" \
      --out {out_dir}/loc-eiffel-tower.jpg

Arguments:
  --url URL       Direct image URL (http/https). For Wikimedia Commons, use
                  https://commons.wikimedia.org/wiki/Special:FilePath/<File-title>?width=1600
                  (redirects are followed automatically).
                  Do NOT pass a Commons file *page* URL (e.g. /wiki/File:…) — those
                  return HTML, not an image.
  --out PATH      Output path. Must end in .jpg, .jpeg, or .png.
                  Parent directories are created automatically.
                  Existing files are silently overwritten — iterate freely.
  --min-edge N    Reject images whose short edge is below N pixels (default: 512).
                  Lower values accept smaller images that may be too weak as refs.
  --max-edge N    Downscale (LANCZOS) so the long edge ≤ N pixels (default: 1536).
                  Keeps the payload small for the Gemini API call.
  --timeout N     HTTP socket timeout in seconds (default: 30).

Naming convention: loc-{slug}.jpg in the output directory, where slug is the
place name lowercased with non-alphanumerics replaced by hyphens — same rule
as ref-{char-slug}.png for character crops.

After a successful download, view the result with the Read tool to confirm:
  - The image shows the right place.
  - It is well-framed and recognizable.
  - It contains no prominent people (a person in the frame risks being read as
    a character by the render model).
If the image is wrong, pick another candidate URL and re-run — the script
silently overwrites the output file.

Exit codes:
  0  Success.
  1  Network, HTTP, content-type, decode, or dimension failures.
  2  Invalid arguments.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

from PIL import Image, ImageOps

# Wikimedia (and many other hosts) 403 the default urllib User-Agent.
# A descriptive contact-bearing string is the right mitigation per Wikimedia policy.
USER_AGENT = (
    "storybook-skill-fetch-location/1.0 "
    "(children's book illustration reference downloader; "
    "https://github.com/anthropics/claude-code)"
)

# Refuse pathological full-resolution downloads that would bloat the Gemini payload.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024  # 64 MB

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _fail(msg: str, code: int = 1) -> None:
    """Print an error message to stderr and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def fetch_location(url: str, out_path: str | Path, *, min_edge: int, max_edge: int, timeout: int) -> None:
    """Download, validate, and save a location reference photo.

    This is the core operation. Raises SystemExit on any unrecoverable error
    (exit 1 for network/content failures, exit 2 for bad arguments).
    """
    out_path = Path(out_path)

    # -- Validate output path -----------------------------------------------
    suffix = out_path.suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        _fail(
            f"--out path must end in .jpg, .jpeg, or .png; got {out_path.suffix!r}.\n"
            f"  Tip: use loc-{{slug}}.jpg as the naming convention.",
            code=2,
        )

    # -- Validate URL scheme -------------------------------------------------
    if not (url.startswith("http://") or url.startswith("https://")):
        _fail(
            f"--url must be an http:// or https:// URL; got {url!r}.\n"
            f"  For Wikimedia Commons, use:\n"
            f"  https://commons.wikimedia.org/wiki/Special:FilePath/<File-title>?width=1600",
            code=2,
        )

    # -- Download -------------------------------------------------------------
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except HTTPError as exc:
        hint = ""
        if exc.code == 403:
            hint = "\n  Hint: Wikimedia may be blocking the request; try adding ?width=1600 to the URL."
        elif exc.code == 404:
            hint = "\n  Hint: re-check the URL. For Commons, use Special:FilePath/<File-title>?width=1600"
        _fail(f"HTTP {exc.code} {exc.reason} — {url}{hint}")
    except URLError as exc:
        _fail(f"network error: {exc.reason} — {url}")
    except TimeoutError:
        _fail(f"timed out after {timeout}s — {url}")

    # -- Verify Content-Type is an image -------------------------------------
    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        _fail(
            f"expected an image (image/*) but got {content_type!r}.\n"
            f"  This usually means you passed a Wikimedia Commons *file page* URL\n"
            f"  (e.g. /wiki/File:…) which returns HTML, not an image.\n"
            f"  Use the direct-download form instead:\n"
            f"  https://commons.wikimedia.org/wiki/Special:FilePath/<File-title>?width=1600"
        )

    # -- Read body with a cap ------------------------------------------------
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            _fail(
                f"download exceeded {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap.\n"
                f"  Tip: request a thumbnail by appending ?width=1600 to the URL."
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    # -- Decode with Pillow --------------------------------------------------
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        _fail(f"cannot decode image from {url}: {exc}")

    # -- Apply EXIF orientation ----------------------------------------------
    img = ImageOps.exif_transpose(img)

    # -- Check minimum dimensions --------------------------------------------
    w, h = img.size
    short_edge = min(w, h)
    if short_edge < min_edge:
        _fail(
            f"image is too small ({w}x{h}px, short edge {short_edge}px < {min_edge}px).\n"
            f"  The image may be too weak as a location reference.\n"
            f"  Try a higher-resolution URL or a different image."
        )

    # -- Downscale if long edge exceeds max ----------------------------------
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        new_w = round(w * scale)
        new_h = round(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        w, h = new_w, new_h

    # -- Normalise to RGB (JPEG cannot carry alpha; flatten on white) --------
    if img.mode in ("RGBA", "LA", "PA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        mask = img.split()[-1] if img.mode in ("RGBA", "LA") else None
        background.paste(img.convert("RGB"), mask=mask)
        img = background
    elif img.mode in ("P", "CMYK"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    # -- Save ----------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if suffix in (".jpg", ".jpeg") else "PNG"
    save_kwargs: dict = {"format": fmt}
    if fmt == "JPEG":
        save_kwargs["quality"] = 88
    img.save(out_path, **save_kwargs)

    print(f"Downloaded {url}")
    print(f"  → {w}x{h}px → {out_path}")
    print(f"MEDIA: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and validate a real-place reference photo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  uv run fetch_location.py \\\n"
            "      --url 'https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Tour_Eiffel_Wikimedia_Commons.jpg?width=1600' \\\n"
            "      --out loc-eiffel-tower.jpg\n\n"
            "See the module docstring for full usage notes."
        ),
    )
    parser.add_argument("--url", required=True, metavar="URL", help="Direct image URL (http/https).")
    parser.add_argument("--out", required=True, metavar="PATH", help="Output path (.jpg, .jpeg, or .png).")
    parser.add_argument(
        "--min-edge",
        type=int,
        default=512,
        metavar="N",
        help="Minimum short-edge in pixels (default: 512).",
    )
    parser.add_argument(
        "--max-edge",
        type=int,
        default=1536,
        metavar="N",
        help="Downscale so the long edge ≤ N pixels (default: 1536).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="N",
        help="HTTP socket timeout in seconds (default: 30).",
    )

    args = parser.parse_args()

    # Validate numeric args (exit 2 on bad values — same as --box in crop_character.py)
    if args.min_edge <= 0:
        _fail("--min-edge must be a positive integer.", code=2)
    if args.max_edge <= 0:
        _fail("--max-edge must be a positive integer.", code=2)
    if args.timeout <= 0:
        _fail("--timeout must be a positive integer.", code=2)
    if args.min_edge > args.max_edge:
        _fail(
            f"--min-edge ({args.min_edge}) must be ≤ --max-edge ({args.max_edge}).",
            code=2,
        )

    fetch_location(
        args.url,
        args.out,
        min_edge=args.min_edge,
        max_edge=args.max_edge,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
