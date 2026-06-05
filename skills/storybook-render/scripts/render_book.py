#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
# ]
# ///
"""
Render all pages of a children's storybook.

For each page in story.json:
  1. Generates the illustration via the Gemini API (gemini-3.1-flash-image), passing
     the style sheet + character refs as input images for consistency.
  2. Runs overlay_text.py to composite the story text.
  3. Prints MEDIA: <path> for each final page.

Pages are fully independent, so they are all fired concurrently via asyncio
(one async Gemini request per page, no thread pool, no local concurrency cap).
Transient 429/5xx responses are retried with exponential backoff + jitter.

Skips pages whose final file already exists (safe to re-run after partial failure).

Requires GEMINI_API_KEY in the environment.

After all pages render successfully a PDF is assembled automatically via
merge_pdf.py (skipped when --only is used or when --no-pdf is passed).

Usage:
  uv run render_book.py --story /path/to/story.json [--out-dir DIR]
                        [--from N] [--only N] [--resolution 1K|2K|4K]
                        [--aspect-ratio RATIO] [--text-mode overlay|native] [--no-pdf]
"""

from __future__ import annotations
import argparse
import asyncio
import json
import mimetypes
import os
import random
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
OVERLAY_SCRIPT = SCRIPTS_DIR / "overlay_text.py"
MERGE_SCRIPT = SCRIPTS_DIR / "merge_pdf.py"

# Gemini image-generation config.
IMAGE_MODEL = "gemini-3.1-flash-image"
MAX_INPUT_IMAGES = 4  # Gemini 3.1 Flash Image: up to 4 character reference images per call

# Retry policy for transient failures (429 rate-limit / 5xx). Pages are fired all
# at once, so a single 429 must not silently drop a page.
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. When a reference photograph is provided "
    "alongside a character style sheet, draw the character's facial likeness from "
    "the photo and the art style from the sheet. Clothing, outfit, and character "
    "design always come from the style sheet, never from the photograph. "
    "Output only the generated image without additional commentary."
)

TEXT_SAFE_ZONE_DIRECTIVE = (
    "Leave the {placement} quarter of the image as a soft, "
    "low-detail, lightly-toned area suitable for overlaying text. "
    "Do not place any narrative text in the image."
)
STYLE_ANCHOR = (
    "Art style and character design must match the provided character reference "
    "sheet(s) exactly. If a reference photograph is also provided, match that "
    "character's facial likeness and identity to the photo, but render fully in the "
    "illustration style of the sheet(s) — never reproduce photographic detail. "
    "Each character wears exactly the outfit shown on their reference sheet; "
    "never take clothing or outfit from a photograph. "
    "Consistent character design, {style}. "
    "Preserve this exact palette, lighting, line treatment, and rendering style "
    "unchanged across every page of the book."
)

# Used in --text-mode native: tells the model to render text into the illustration.
# One fixed, detailed letterform descriptor reused verbatim on every page so the
# lettering style stays consistent book-wide (each page is a separate stateless call
# with no seed). {font_ref} names a target font family when story.fonts configures one;
# the descriptor adjectives must stay coherent with that family (rounded sans here).
NATIVE_TEXT_DIRECTIVE = (
    "Render this exact story text as part of the illustration, {placement_clause}. "
    "Letter it in a clean, rounded, child-friendly "
    "style{font_ref}: even weight, steady baseline, generous letter spacing, warm dark ink, "
    "crisp and highly legible against the soft low-detail background — and keep this exact "
    "lettering style identical on every page of the book. Preserve the text's natural "
    "reading direction. Reproduce every word, comma, quotation mark, and dash exactly as "
    'written — no changes, no omissions, no extra text: "{text}"'
)


# Placement clause spliced into NATIVE_TEXT_DIRECTIVE. "floating" hands the model creative
# control over where the text lands; top/bottom pin it to a band.
FLOATING_PLACEMENT_CLAUSE = (
    "integrated naturally into the scene wherever it best suits the composition — you choose "
    "the most visually pleasing, creative placement for a children's storybook (open sky, a "
    "calm patch of background, along an edge or corner), kept clear of faces and the main "
    "subject and fully legible"
)


def _placement_clause(placement: str) -> str:
    if placement == "floating":
        return FLOATING_PLACEMENT_CLAUSE
    return f"integrated naturally into the {placement} of the scene"


def _overlay_placement(placement: str) -> str:
    """Overlay (Pillow) composites at a fixed band; it can't float. Degrade to bottom."""
    return "bottom" if placement == "floating" else placement


def load_story(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# Keep in sync with the copies in make_style_sheet.py
# (the two skills share no module; both copies must stay identical).
STYLE_GUIDE_EXAMPLE = """  "style_guide": {
    "medium": "soft watercolor with thin pen-and-ink outline",
    "palette": ["warm cream #F5E9D4", "sage green #8FAF85", "dusty coral #E8917A"],
    "line": "thin sepia ink, even weight, rounded corners",
    "lighting": "golden-hour side-light, soft warm shadows",
    "mood": "cozy, gentle, storybook calm"
  }"""


def build_style_block(story: dict) -> str:
    """Verbatim style descriptor for this book, injected byte-identically into every call.

    Assembles a deterministic block from the required 'style_guide' object's fields in
    fixed order (medium → palette → line → lighting → mood) so every API call receives
    exactly the same string by construction — the documented cross-page consistency
    mechanism (per Google's Book_illustration workflow: one verbatim style string reused
    on every independent call).

    'style_guide' is required. main() validates it via require_style_guide() before any
    paid API work; raises ValueError if it is missing, malformed, or assembles empty.
    """
    guide = story.get("style_guide")
    parts: list[str] = []
    if isinstance(guide, dict):
        if guide.get("medium"):
            parts.append(f"Medium: {guide['medium']}")
        if guide.get("palette"):
            palette_str = ", ".join(str(s) for s in guide["palette"])
            parts.append(f"Palette: {palette_str}")
        if guide.get("line"):
            parts.append(f"Line: {guide['line']}")
        if guide.get("lighting"):
            parts.append(f"Lighting: {guide['lighting']}")
        if guide.get("mood"):
            parts.append(f"Mood: {guide['mood']}")
    if not parts:
        raise ValueError("story.json is missing a usable top-level 'style_guide' object")
    return ". ".join(parts)


def require_style_guide(story: dict) -> None:
    """Fail fast — before any paid API work — when 'style_guide' is missing or empty."""
    try:
        build_style_block(story)
    except ValueError:
        print(
            "ERROR: story.json must define a top-level 'style_guide' object — it is the\n"
            "book-wide consistency anchor injected verbatim into every image call.\n"
            "Add for example:\n\n" + STYLE_GUIDE_EXAMPLE + "\n\n"
            "See skills/storybook-story/assets/STYLE_PRIMER.md for the field reference.",
            file=sys.stderr,
        )
        sys.exit(2)


def build_image_prompt(page: dict, story: dict, text_mode: str = "native") -> str:
    placement = page.get("text_placement", "floating")
    style = build_style_block(story)
    anchor = STYLE_ANCHOR.format(style=style)

    if text_mode == "native":
        # Strip any baked-in safe-zone sentence (". Leave the <...>.") from the prompt so the
        # model gets a clean slate — then append NATIVE_TEXT_DIRECTIVE with the verbatim text.
        raw_prompt = re.sub(r"\.\s*Leave the [^.]+\.?\s*$", "", page["image_prompt"])
        base = raw_prompt.rstrip(". ")
        text = page.get("text", "")
        # Reuse the same role -> family map the overlay path uses; name the family as a
        # lettering reference so the baked-in text matches the book's configured font.
        font_role = page.get("font", "reader")
        family = (story.get("fonts") or {}).get(font_role)
        font_ref = f", styled after the {family} typeface" if family else ""
        native = NATIVE_TEXT_DIRECTIVE.format(
            placement_clause=_placement_clause(placement), text=text, font_ref=font_ref
        )
        return f"{base}. {anchor}. {native}"

    # overlay (default): unchanged behaviour. "floating" is native-only -> bottom here.
    base = page["image_prompt"].rstrip(". ")
    safe_zone = TEXT_SAFE_ZONE_DIRECTIVE.format(placement=_overlay_placement(placement))
    return f"{base}. {safe_zone}. {anchor}"


def _ref_photos(char: dict) -> list[str]:
    """This character's own original reference photo paths, in order, existing only.

    Mirrors collect_ref_images_for_char() in make_style_sheet.py (the two skills share
    no module): normalize a string-or-list `ref_image` -> dedup keeping order -> drop
    missing files. No cap here; the caller's MAX_INPUT_IMAGES budget governs.
    """
    raw = char.get("ref_image")
    if isinstance(raw, str):
        refs = [raw]
    elif isinstance(raw, list):
        refs = raw
    else:
        refs = []
    ordered: list[str] = []
    seen: set[str] = set()
    for r in refs:
        r = r.strip() if isinstance(r, str) else ""
        if r and r not in seen:
            seen.add(r)
            ordered.append(r)
    return [r for r in ordered if Path(r).exists()]


def collect_input_images(
    story: dict, page: dict, log: list[str] | None = None
) -> list[str]:
    """Per-page reference images for the render, capped at MAX_INPUT_IMAGES (4 for flash).

    Each character listed in page['characters'] contributes its style sheet (names must
    match story['characters'][].name exactly). The HERO — the first name in
    page['characters'] — additionally contributes its first original reference photo, so
    the render anchors the hero's facial likeness on the real photo rather than only the
    derived (lossy) style sheet. CONVENTION: author the hero/child first in each page's
    cast list.

    Priority order into the budget: hero sheet, hero photo, then the remaining characters'
    sheets in cast order. Anything beyond the cap is named in a log line so nothing is
    silently dropped.
    """
    def warn(msg: str) -> None:
        if log is not None:
            log.append(f"  Warning: {msg}")
        else:
            print(f"Warning: {msg}", file=sys.stderr)

    char_index: dict[str, dict] = {
        c.get("name", ""): c for c in story.get("characters", [])
    }
    page_cast: list[str] = page.get("characters", [])

    # Prioritized (label, path) candidates; trimmed to the cap below.
    candidates: list[tuple[str, str]] = []
    for i, name in enumerate(page_cast):
        char = char_index.get(name)
        if char is None:
            warn(f"page references unknown character {name!r}; skipping.")
            continue
        sheet = char.get("style_sheet", "")
        if not sheet:
            warn(
                f"character {name!r} has no style_sheet; skipping. "
                "Run make_style_sheet.py first."
            )
        elif not Path(sheet).exists():
            warn(f"style sheet for {name!r} not found on disk ({sheet}); skipping.")
        else:
            candidates.append((f"{name} sheet", sheet))
        # Hero (first cast member) also contributes its real photo for face fidelity.
        if i == 0:
            photos = _ref_photos(char)
            if photos:
                candidates.append((f"{name} photo", photos[0]))

    input_images = [path for _, path in candidates[:MAX_INPUT_IMAGES]]
    dropped = [label for label, _ in candidates[MAX_INPUT_IMAGES:]]
    if dropped:
        msg = f"cap ({MAX_INPUT_IMAGES}) reached; dropped: {', '.join(dropped)}"
        if log is not None:
            log.append(f"  Note: {msg}")
        else:
            print(f"Note: {msg}")

    return input_images


def _retry_delay(attempt: int, exc: Exception) -> float:
    """Backoff for the given attempt (0-indexed). Honors a Retry-After header if present."""
    retry_after = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw = headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
    if retry_after is not None:
        return min(retry_after, BACKOFF_MAX_SECONDS)
    # Exponential backoff with full jitter.
    capped = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
    return random.uniform(0, capped)


async def run_nano_banana(
    client,
    prompt: str,
    raw_path: Path,
    story: dict,
    page: dict,
    resolution: str,
    log: list[str],
    aspect_ratio: str | None = None,
) -> bool:
    """Generate one illustration via the Gemini API and write it to raw_path."""
    from google.genai import errors, types

    # Build contents: text prompt + one Part.from_bytes per input image.
    contents: list = [prompt]
    for img_path in collect_input_images(story, page, log):
        p = Path(img_path)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        contents.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))

    config = types.GenerateContentConfig(
        system_instruction=IMAGE_SYSTEM_PROMPT,
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(image_size=resolution, aspect_ratio=aspect_ratio),
    )

    log.append(f"  Generating: {raw_path.name}")
    response = None
    for attempt in range(MAX_RETRIES):
        last_exc: Exception
        try:
            response = await client.aio.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=config,
            )
            break
        except errors.APIError as e:
            if e.code != 429 and e.code < 500:
                log.append(f"  ERROR: image API request failed ({e.code}): {e}")
                return False
            last_exc = e
        except Exception as e:
            log.append(f"  ERROR: image API request failed: {e}")
            return False

        if attempt == MAX_RETRIES - 1:
            log.append(f"  ERROR: image API request failed after {MAX_RETRIES} attempts: {last_exc}")
            return False
        delay = _retry_delay(attempt, last_exc)
        log.append(f"  Retry {attempt + 1}/{MAX_RETRIES - 1} after transient error; waiting {delay:.1f}s...")
        await asyncio.sleep(delay)

    # Extract image bytes from the response parts.
    image_data: bytes | None = None
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            image_data = part.inline_data.data
            break
    if not image_data:
        log.append("  ERROR: no images returned by the API.")
        return False

    try:
        raw_path.write_bytes(image_data)
    except Exception as e:
        log.append(f"  ERROR: failed to write image: {e}")
        return False
    return True


async def run_overlay(
    raw_path: Path, text: str, placement: str, color: str, font: str,
    font_name: str | None, align: str, final_path: Path, log: list[str]
) -> bool:
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(raw_path),
        "--text", text,
        "--placement", placement,
        "--out", str(final_path),
        "--color", color,
        "--font", font,
        "--align", align,
    ]
    if font_name:
        cmd += ["--font-name", font_name]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        if stdout:
            log.append(stdout.decode().rstrip())
        if stderr:
            log.append(stderr.decode().rstrip())
    return proc.returncode == 0


async def render_page(client, page: dict, story: dict, pages_dir: Path, resolution: str, text_mode: str = "native", aspect_ratio: str | None = None) -> bool:
    """Render one page (nano-banana + optional overlay). Prints its own log atomically. Page-independent."""
    page_num = page["page_num"]
    log: list[str] = [f"=== Page {page_num} (text-mode: {text_mode}) ==="]
    nn = f"{page_num:02d}"
    suffix = "-native" if text_mode == "native" else ""
    final_path = pages_dir / f"page-{nn}{suffix}.png"

    prompt = build_image_prompt(page, story, text_mode)

    if text_mode == "native":
        # In native mode the model bakes text into the illustration — write directly
        # to final_path; no separate raw file needed.
        ok = await run_nano_banana(client, prompt, final_path, story, page, resolution, log, aspect_ratio)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False
    else:
        raw_path = pages_dir / f"raw-page-{nn}.png"
        ok = await run_nano_banana(client, prompt, raw_path, story, page, resolution, log, aspect_ratio)
        if not ok or not raw_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False

        text = page.get("text", "")
        placement = _overlay_placement(page.get("text_placement", "floating"))
        color = page.get("text_color_hint", "dark")
        font = page.get("font", "reader")
        # Book-wide role -> family-name map; the resolved name (if any) overrides the
        # bundled role font. Omitted/unknown role -> None -> bundled font used.
        font_name = (story.get("fonts") or {}).get(font)
        align = page.get("text_align", "left")

        ok = await run_overlay(raw_path, text, placement, color, font, font_name, align, final_path, log)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: text overlay failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False

    log.append(f"  Done: {final_path}")
    log.append(f"MEDIA: {final_path}")
    print("\n" + "\n".join(log))
    return True


async def render_all(todo: list[dict], story: dict, pages_dir: Path, resolution: str, text_mode: str = "native", aspect_ratio: str | None = None) -> int:
    """Fire every page concurrently. Returns the number of failures."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set in the environment.", file=sys.stderr)
        return len(todo)

    client = genai.Client(api_key=api_key)
    print(f"\nRendering {len(todo)} page(s) concurrently ({text_mode} mode)...")
    results = await asyncio.gather(
        *(render_page(client, page, story, pages_dir, resolution, text_mode, aspect_ratio) for page in todo)
    )
    return sum(1 for ok in results if not ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all pages of a storybook.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    parser.add_argument("--resolution", choices=["1K", "2K", "4K"], default=None,
                        help="Override the resolution from story.json (default: story.json 'resolution' field, or 2K if not set)")
    parser.add_argument(
        "--aspect-ratio",
        choices=["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        default=None,
        dest="aspect_ratio",
        help=(
            "Override the aspect ratio from story.json "
            "(default: story.json 'aspect_ratio' field, or unset — model chooses)."
        ),
    )
    parser.add_argument("--from", dest="from_page", type=int, default=1,
                        help="Start from this page number (1-indexed)")
    parser.add_argument("--only", dest="only_page", type=int, default=None,
                        help="Render only this page number")
    parser.add_argument(
        "--text-mode",
        dest="text_mode",
        choices=["overlay", "native"],
        default=None,
        help=(
            "Override story.json's text_mode for this run. "
            "overlay: generate image with text-safe zone, then Pillow-composite text. "
            "native: ask the model to render story text directly into the illustration "
            "(output goes to page-NN-native.png). "
            "If omitted, uses story.json's top-level 'text_mode' (default native)."
        ),
    )
    parser.add_argument(
        "--no-pdf",
        dest="no_pdf",
        action="store_true",
        default=False,
        help="Skip the automatic PDF merge step after rendering (useful for --from partial runs).",
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)
    require_style_guide(story)

    # CLI flag > story.json field > built-in default (2K).
    resolution = args.resolution or story.get("resolution") or "2K"
    # CLI flag > story.json field > unset (model chooses framing).
    aspect_ratio = args.aspect_ratio or story.get("aspect_ratio") or None
    # text_mode precedence: CLI flag (if given) > story.json top-level > "native".
    text_mode = args.text_mode or story.get("text_mode", "native")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json", file=sys.stderr)
        sys.exit(1)

    # Warn if no per-character style sheets have been generated yet
    chars = story.get("characters", [])
    if chars and not any(c.get("style_sheet") for c in chars):
        print("Warning: no character style_sheet paths found in story.json.")
        print("Run make_style_sheet.py first for better character consistency.")
        print()

    # Select pages to render, skipping filtered-out and already-existing ones.
    suffix = "-native" if text_mode == "native" else ""
    todo: list[dict] = []
    for page in pages:
        page_num = page["page_num"]
        if args.only_page is not None and page_num != args.only_page:
            continue
        if page_num < args.from_page:
            continue
        final_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
        if final_path.exists():
            print(f"Page {page_num}: already exists, skipping. ({final_path})")
            print(f"MEDIA: {final_path}")
            continue
        todo.append(page)

    errors = asyncio.run(render_all(todo, story, pages_dir, resolution, text_mode, aspect_ratio)) if todo else 0

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)

    # Auto-merge rendered pages into a single PDF.
    # Gate: all pages succeeded (errors == 0), not a single-page proof run (--only),
    # and the user has not opted out (--no-pdf). The errors==0 gate also covers the
    # all-skipped case (empty todo → errors=0) so a re-run of a finished book refreshes
    # the PDF.
    if not args.no_pdf and args.only_page is None:
        merge_cmd = [
            "uv", "run", str(MERGE_SCRIPT),
            "--story", str(story_path),
            "--out-dir", str(out_dir),
            "--text-mode", text_mode,
        ]
        result = subprocess.run(merge_cmd, capture_output=False)
        if result.returncode != 0:
            print("Warning: PDF merge step failed (pages are still intact).", file=sys.stderr)


if __name__ == "__main__":
    main()
