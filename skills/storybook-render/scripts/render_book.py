#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai",
# ]
# ///
"""
Render all pages of a children's storybook.

For each page in story.json:
  1. Generates the illustration via OpenRouter (Gemini image model), passing the
     style sheet + character refs as input images for consistency.
  2. Runs overlay_text.py to composite the story text.
  3. Prints MEDIA: <path> for each final page.

Pages are fully independent, so they are all fired concurrently via asyncio
(one async OpenRouter request per page, no thread pool, no local concurrency cap).
Transient 429/5xx responses are retried with exponential backoff + jitter.

Skips pages whose final file already exists (safe to re-run after partial failure).

Requires OPENROUTER_API_KEY in the environment.

Usage:
  uv run render_book.py --story /path/to/story.json [--out-dir DIR]
                        [--from N] [--only N] [--resolution 1K|2K|4K]
"""

from __future__ import annotations
import argparse
import asyncio
import base64
import json
import mimetypes
import os
import random
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
OVERLAY_SCRIPT = SCRIPTS_DIR / "overlay_text.py"

# OpenRouter image-generation config (mirrors the nano-banana-pro-openrouter skill).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
IMAGE_MODEL = "google/gemini-3.1-flash-image-preview"
MAX_INPUT_IMAGES = 3

# Retry policy for transient failures (429 rate-limit / 5xx). Pages are fired all
# at once, so a single 429 must not silently drop a page.
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. Preserve the provided reference images' "
    "character design and art style. Output only the generated image without "
    "additional commentary."
)

TEXT_SAFE_ZONE_DIRECTIVE = (
    "Leave the {placement} quarter of the image as a soft, "
    "low-detail, lightly-toned area suitable for overlaying text. "
    "Do not place any narrative text in the image."
)
STYLE_ANCHOR = (
    "Art style must match the provided character reference sheet exactly. "
    "Consistent character design, {style}."
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


def build_image_prompt(page: dict, story: dict, text_mode: str = "overlay") -> str:
    placement = page.get("text_placement", "bottom")
    style = story.get("style", "children's picture book illustration")
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


def encode_image_to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def collect_input_images(story: dict) -> list[str]:
    """Style sheet first, then character refs, capped at MAX_INPUT_IMAGES."""
    input_images: list[str] = []
    style_sheet = story.get("style_sheet_path")
    if style_sheet and Path(style_sheet).exists():
        input_images.append(style_sheet)
    for ref in story.get("character_refs", []):
        if len(input_images) >= MAX_INPUT_IMAGES:
            break
        if Path(ref).exists():
            input_images.append(ref)
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
    resolution: str,
    log: list[str],
) -> bool:
    """Generate one illustration via OpenRouter and write it to raw_path."""
    from openai import APIConnectionError, APIStatusError, RateLimitError

    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in collect_input_images(story):
        content.append(
            {"type": "image_url", "image_url": {"url": encode_image_to_data_url(Path(img))}}
        )

    messages = [
        {"role": "system", "content": IMAGE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    log.append(f"  Generating: {raw_path.name}")
    response = None
    for attempt in range(MAX_RETRIES):
        last_exc: Exception
        try:
            response = await client.chat.completions.create(
                model=IMAGE_MODEL,
                messages=messages,
                extra_body={
                    "modalities": ["image", "text"],
                    "image_config": {"image_size": resolution},
                },
            )
            break
        except (RateLimitError, APIConnectionError) as e:
            last_exc = e
        except APIStatusError as e:
            if e.status_code < 500:
                log.append(f"  ERROR: image API request failed ({e.status_code}): {e}")
                return False
            last_exc = e
        except json.JSONDecodeError as e:
            # OpenRouter returned a non-JSON body (e.g. rate-limit HTML) despite a
            # application/json Content-Type header. Treat as transient and retry.
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

    images = getattr(response.choices[0].message, "images", None)
    if not images:
        log.append("  ERROR: no images returned by the API.")
        return False

    image_url = None
    first = images[0]
    if isinstance(first, dict):
        image_url = first.get("image_url", {}).get("url") or first.get("url")
    if not image_url or not image_url.startswith("data:") or ";base64," not in image_url:
        log.append("  ERROR: image payload missing base64 data URL.")
        return False

    _, encoded = image_url.split(",", 1)
    try:
        raw_path.write_bytes(base64.b64decode(encoded))
    except Exception as e:
        log.append(f"  ERROR: failed to decode/write image: {e}")
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


async def render_page(client, page: dict, story: dict, pages_dir: Path, resolution: str, text_mode: str = "overlay") -> bool:
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
        ok = await run_nano_banana(client, prompt, final_path, story, resolution, log)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False
    else:
        raw_path = pages_dir / f"raw-page-{nn}.png"
        ok = await run_nano_banana(client, prompt, raw_path, story, resolution, log)
        if not ok or not raw_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False

        text = page.get("text", "")
        placement = _overlay_placement(page.get("text_placement", "bottom"))
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


async def render_all(todo: list[dict], story: dict, pages_dir: Path, resolution: str, text_mode: str = "overlay") -> int:
    """Fire every page concurrently. Returns the number of failures."""
    from openai import AsyncOpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in the environment.", file=sys.stderr)
        return len(todo)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    try:
        print(f"\nRendering {len(todo)} page(s) concurrently ({text_mode} mode)...")
        results = await asyncio.gather(
            *(render_page(client, page, story, pages_dir, resolution, text_mode) for page in todo)
        )
    finally:
        await client.close()
    return sum(1 for ok in results if not ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all pages of a storybook.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    parser.add_argument("--resolution", choices=["1K", "2K", "4K"], default=None,
                        help="Override the resolution from story.json (default: story.json 'resolution' field, or 2K if not set)")
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
            "If omitted, uses story.json's top-level 'text_mode' (default overlay)."
        ),
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)

    # CLI flag > story.json field > built-in default (2K).
    resolution = args.resolution or story.get("resolution") or "2K"
    # text_mode precedence: CLI flag (if given) > story.json top-level > "overlay".
    text_mode = args.text_mode or story.get("text_mode", "overlay")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else story_path.parent
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pages = story.get("pages", [])
    if not pages:
        print("ERROR: No pages found in story.json", file=sys.stderr)
        sys.exit(1)

    # Warn if no style sheet
    if not story.get("style_sheet_path"):
        print("Warning: style_sheet_path not set in story.json.")
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

    errors = asyncio.run(render_all(todo, story, pages_dir, resolution, text_mode)) if todo else 0

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
