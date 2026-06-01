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


def load_story(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def build_image_prompt(page: dict, story: dict) -> str:
    placement = page.get("text_placement", "bottom")
    style = story.get("style", "children's picture book illustration")
    base = page["image_prompt"].rstrip(". ")
    safe_zone = TEXT_SAFE_ZONE_DIRECTIVE.format(placement=placement)
    anchor = STYLE_ANCHOR.format(style=style)
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
    font_name: str | None, final_path: Path, log: list[str]
) -> bool:
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(raw_path),
        "--text", text,
        "--placement", placement,
        "--out", str(final_path),
        "--color", color,
        "--font", font,
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


async def render_page(client, page: dict, story: dict, pages_dir: Path, resolution: str) -> bool:
    """Render one page (nano-banana + overlay). Prints its own log atomically. Page-independent."""
    page_num = page["page_num"]
    log: list[str] = [f"=== Page {page_num} ==="]
    nn = f"{page_num:02d}"
    final_path = pages_dir / f"page-{nn}.png"
    raw_path = pages_dir / f"raw-page-{nn}.png"

    prompt = build_image_prompt(page, story)

    ok = await run_nano_banana(client, prompt, raw_path, story, resolution, log)
    if not ok or not raw_path.exists():
        log.append(f"  ERROR: image generation failed for page {page_num}")
        print("\n" + "\n".join(log))
        return False

    text = page.get("text", "")
    placement = page.get("text_placement", "bottom")
    color = page.get("text_color_hint", "dark")
    font = page.get("font", "reader")
    # Book-wide role -> family-name map; the resolved name (if any) overrides the
    # bundled role font. Omitted/unknown role -> None -> bundled font used.
    font_name = (story.get("fonts") or {}).get(font)

    ok = await run_overlay(raw_path, text, placement, color, font, font_name, final_path, log)
    if not ok or not final_path.exists():
        log.append(f"  ERROR: text overlay failed for page {page_num}")
        print("\n" + "\n".join(log))
        return False

    log.append(f"  Done: {final_path}")
    log.append(f"MEDIA: {final_path}")
    print("\n" + "\n".join(log))
    return True


async def render_all(todo: list[dict], story: dict, pages_dir: Path, resolution: str) -> int:
    """Fire every page concurrently. Returns the number of failures."""
    from openai import AsyncOpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in the environment.", file=sys.stderr)
        return len(todo)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    try:
        print(f"\nRendering {len(todo)} page(s) concurrently...")
        results = await asyncio.gather(
            *(render_page(client, page, story, pages_dir, resolution) for page in todo)
        )
    finally:
        await client.close()
    return sum(1 for ok in results if not ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all pages of a storybook.")
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument("--out-dir", help="Output directory (default: same dir as story.json)")
    parser.add_argument("--resolution", choices=["1K", "2K", "4K"], default="1K")
    parser.add_argument("--from", dest="from_page", type=int, default=1,
                        help="Start from this page number (1-indexed)")
    parser.add_argument("--only", dest="only_page", type=int, default=None,
                        help="Render only this page number")
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)

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
    todo: list[dict] = []
    for page in pages:
        page_num = page["page_num"]
        if args.only_page is not None and page_num != args.only_page:
            continue
        if page_num < args.from_page:
            continue
        final_path = pages_dir / f"page-{page_num:02d}.png"
        if final_path.exists():
            print(f"Page {page_num}: already exists, skipping. ({final_path})")
            print(f"MEDIA: {final_path}")
            continue
        todo.append(page)

    errors = asyncio.run(render_all(todo, story, pages_dir, args.resolution)) if todo else 0

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
