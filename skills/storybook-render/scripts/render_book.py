#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
#     "Pillow",
# ]
# ///
"""
Render all pages of a children's storybook.

For each page in story.json:
  1. Generates the illustration via the Gemini API (default: gemini-3.1-flash-image),
     passing the style sheet + character refs as input images for consistency.
  2. In overlay/long mode: runs overlay_text.py to composite the story text.
  3. Prints MEDIA: <path> for each final page.

Pages are fully independent, so they are all fired concurrently via asyncio
(one async Gemini request per page, no thread pool, no local concurrency cap).
Transient 429/5xx responses are retried with exponential backoff + jitter.

Skips pages whose final file(s) already exist (safe to re-run after partial failure).
In long mode, per-artifact skip checks mean a missing text page can be rebuilt without
re-firing the paid art call — and without needing GEMINI_API_KEY when no paid call is made.

Requires GEMINI_API_KEY in the environment when a paid Gemini call is needed.

This script produces page images only. Assembly into PDF/EPUB is Stage 4
(storybook-consolidate skill: merge_pdf.py / merge_epub.py / package_book.py — free,
no API cost, run independently after reviewing the rendered pages).

Model selection (precedence: CLI --model > page 'model' > story 'model' > default flash):
  gemini-3.1-flash-image — default; faster/cheaper, up to 4 reference images per call.
  gemini-3-pro-image     — higher quality, up to 5 reference images per call.
  Use --model or set page-level/book-level 'model' in story.json to override.
  Style sheets (Stage 2) always use gemini-3-pro-image regardless of this setting.

Text modes:
  overlay — safe-zone art + Pillow text overlay → pages/page-NN.png
  native  — model bakes text into illustration → pages/page-NN-native.png
  long    — full-bleed art (no text) + separate text page → pages/page-NN-long.png +
            pages/page-NN-long-text.png. Cover (page 1) stays combined (overlay-style).
            Text pages sit on ONE shared model-generated background per book
            (pages/text-bg-long.png — generated once, before pages fire, +1 paid call
            total); a page with text_background_prompt gets its own dedicated bg instead
            (pages/page-NN-long-bg.png, +1 call for that page). Text-page composition
            itself is free Pillow work. Cost: N art calls + 1 shared bg call.

Usage:
  uv run render_book.py --story /path/to/story.json [--out-dir DIR]
                        [--from N] [--only N] [--resolution 1K|2K|4K]
                        [--aspect-ratio RATIO] [--text-mode overlay|native|long]
                        [--model gemini-3.1-flash-image|gemini-3-pro-image]
                        [--saved-formats pdf epub|none]
"""

from __future__ import annotations
import argparse
import asyncio
import json
import mimetypes
import os
import random
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
OVERLAY_SCRIPT = SCRIPTS_DIR / "overlay_text.py"

# Gemini image-generation config.
IMAGE_MODEL = "gemini-3.1-flash-image"  # default; overridable per page/book/CLI
IMAGE_MODELS = ["gemini-3.1-flash-image", "gemini-3-pro-image"]  # keep in sync with story_schema.json
MODEL_MAX_INPUT_IMAGES = {
    "gemini-3.1-flash-image": 4,  # Gemini 3.1 Flash Image: up to 4 reference images per call
    "gemini-3-pro-image": 5,      # Gemini 3 Pro Image: up to 5 reference images per call
}
MAX_INPUT_IMAGES = 4  # safe fallback cap for unrecognised models

# Retry policy for transient failures (429 rate-limit / 5xx). Pages are fired all
# at once, so a single 429 must not silently drop a page.
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

IMAGE_SYSTEM_PROMPT = (
    "You are a visionary image-creation artist. Transform the request into a "
    "vivid, concrete, model-ready illustration. Pay attention to composition, "
    "lighting, color, and visual balance. Reference images follow the prompt, "
    "each introduced by a short text note identifying it. Rules by kind: "
    "A 'character style sheet' defines that character's design, outfit, and "
    "the illustration art style — follow it exactly. "
    "A 'character photograph' supplies that character's facial likeness only — "
    "clothing, outfit, and character design always come from the style sheet, "
    "never from any photograph. "
    "An 'object reference sheet' defines that object's design, colours, and "
    "proportions in the book's art style — follow it exactly. "
    "An 'object photograph' supplies that object's shape, materials, and "
    "distinguishing details — render it fully in the book's illustration style, "
    "never photographically. "
    "A 'location reference sheet' defines that place's look in the book's art "
    "style — follow it exactly; it is scenery, never a character. "
    "A 'location photograph' shows a real place that is the SETTING of the "
    "scene: reproduce its recognizable architecture, landmarks, and geography, "
    "rendered fully in the book's illustration style — it is scenery, never a "
    "character or a person, and never photographic rendering. "
    "The identification notes are instructions, not story text; never letter "
    "them into the image. "
    "Output only the generated image without additional commentary."
)

TEXT_SAFE_ZONE_DIRECTIVE = (
    "Leave the {placement} quarter of the image as a soft, "
    "low-detail, lightly-toned area suitable for overlaying text. "
    "Do not place any narrative text in the image."
)

# Long mode: the model fills the full canvas — no text, no safe zone reserved.
FULL_BLEED_ART_DIRECTIVE = (
    "Use the full canvas for the illustration — rich, full-bleed artwork from edge to edge "
    "with no reserved text area. Do not render any words, letters, captions, or typography "
    "anywhere in the image."
)
# Long mode: one shared text-page background per book, generated once in render_all
# before pages fire (pages only consume it — generating inside render_page would race).
SHARED_TEXT_BG_NAME = "text-bg-long.png"
STYLE_ANCHOR = (
    "Art style and character design must match the provided reference "
    "sheet(s) exactly. If a reference photograph of a character is also provided, "
    "match that character's facial likeness and identity to the photo, but render "
    "fully in the illustration style of the sheet(s) — never reproduce photographic "
    "detail. "
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


def resolve_model(
    story: dict,
    page: dict | None = None,
    cli_model: str | None = None,
) -> str:
    """Resolve which Gemini model to use for one render call.

    Precedence: CLI --model > page 'model' field > story top-level 'model' > IMAGE_MODEL default.
    page=None skips per-page resolution (used for book-wide shared text-bg generation).
    """
    page_model = page.get("model") if page else None
    return cli_model or page_model or story.get("model") or IMAGE_MODEL


# Keep in sync with the copy in make_style_sheet.py
# (the two skills share no module; both copies must stay identical).
LEGACY_KEY_MESSAGE = (
    "ERROR: story.json uses the pre-PER-34 schema. The cast contract changed "
    "(breaking, no shim):\n"
    "  top-level \"characters\"  ->  \"cast\"  (same entry shape; add optional\n"
    "                                         \"kind\": \"character\"|\"object\"|\"location\",\n"
    "                                         default character)\n"
    "  top-level \"locations\"   ->  cast entries with \"kind\": \"location\"\n"
    "                                (keep ref_image and source_url; fold\n"
    "                                 \"description\" into \"appearance\")\n"
    "  pages[].characters      ->  pages[].cast  (ONE flat name list, mixed\n"
    "                                kinds; hero = first character-kind entry)\n"
    "  pages[].location        ->  append the place name to that page's\n"
    "                                \"cast\" list\n"
    "Migrate story.json (or re-run Stage 1) and re-run. See\n"
    "skills/storybook-story/assets/story_schema.json."
)


def reject_legacy_keys(story: dict) -> None:
    """Fail fast (exit 2) on pre-PER-34 story.json files. Breaking rename, no shim."""
    found: list[str] = []
    if "characters" in story:
        found.append('top-level "characters"')
    if "locations" in story:
        found.append('top-level "locations"')
    pages = story.get("pages")
    if isinstance(pages, list):
        for p in pages:
            if isinstance(p, dict):
                pn = p.get("page_num", "?")
                if "characters" in p:
                    found.append(f'pages[{pn}].characters')
                if "location" in p:
                    found.append(f'pages[{pn}].location')
    if found:
        print(
            f"Legacy keys found: {', '.join(found)}\n\n{LEGACY_KEY_MESSAGE}",
            file=sys.stderr,
        )
        sys.exit(2)


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

    if text_mode == "long":
        # Long mode art page: strip any baked-in safe-zone sentence; request full-bleed
        # art with no text (text is rendered on a separate physical page).
        raw_prompt = re.sub(r"\.\s*Leave the [^.]+\.?\s*$", "", page["image_prompt"])
        base = raw_prompt.rstrip(". ")
        return f"{base}. {anchor}. {FULL_BLEED_ART_DIRECTIVE}"

    # overlay (default): unchanged behaviour. "floating" is native-only -> bottom here.
    base = page["image_prompt"].rstrip(". ")
    safe_zone = TEXT_SAFE_ZONE_DIRECTIVE.format(placement=_overlay_placement(placement))
    return f"{base}. {safe_zone}. {anchor}"


def build_text_bg_prompt(story: dict, page: dict | None = None) -> str:
    """Prompt for a text-page background (long mode).

    One shared background per book by default (pages/text-bg-long.png); a page with
    text_background_prompt set gets its own dedicated background instead. No character
    references are sent for this call, so STYLE_ANCHOR is not used. The style block is
    injected verbatim for book-wide visual consistency.

    Description precedence: page-level text_background_prompt (when page is given) ->
    top-level text_background_prompt -> generic default.
    """
    style = build_style_block(story)
    bg_desc = ""
    if page is not None:
        bg_desc = page.get("text_background_prompt", "").rstrip(". ")
    if not bg_desc:
        bg_desc = story.get("text_background_prompt", "").rstrip(". ")
    if not bg_desc:
        bg_desc = "A soft decorative background for the story's text pages"
    return (
        f"{bg_desc}. "
        "Render a soft, low-detail, calm full-bleed decorative background — "
        "no characters, no faces, no lettering, no typography anywhere in the image. "
        "Use the full canvas from edge to edge, and reserve a large, especially "
        "low-detail, lightly-toned central area where story text will be placed. "
        f"Art style: {style}. "
        "Preserve this exact palette, lighting, line treatment, and rendering style "
        "unchanged across every page of the book."
    )


def _ref_photos(entry: dict) -> list[str]:
    """This cast entry's own reference photo paths, in order, existing only.

    For kind=character entries, each path must be a single-person image — a solo
    photo or a Stage-1 crop produced by storybook-story's crop_character.py.
    Multi-person group photos should have been cropped to per-person files before
    story.json was written.

    Mirrors collect_ref_images_for_entry() in make_style_sheet.py (the two skills share
    no module): normalize a string-or-list `ref_image` -> dedup keeping order -> drop
    missing files. No cap here; the caller's per-model budget governs.
    """
    raw = entry.get("ref_image")
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
    story: dict, page: dict, log: list[str] | None = None,
    max_images: int = MAX_INPUT_IMAGES,
) -> list[tuple[str, str]]:
    """Per-page reference images for the render, capped at max_images (4 flash default / 5 pro).

    page['cast'] is ONE flat name list of mixed kinds (names must match
    story['cast'][].name exactly). Contribution by kind:
      character — its style sheet; the HERO (first character-kind entry in page
                  order) additionally contributes its first ref_image photo for
                  facial likeness. CONVENTION: author the hero/child first.
      object    — its reference sheet; falls back to its first ref_image photo
                  when no sheet exists.
      location  — its reference sheet when one exists (photo-less fictional
                  place); otherwise its ref_image photo (PER-38 real-place flow).

    Priority order into the budget: hero sheet → hero photo → remaining character
    sheets (page order) → object refs (page order) → location refs (page order,
    lowest, first to drop from the cap). Anything past the cap is named in a log
    line so nothing is silently dropped.

    Returns (label, path) pairs; labels are interleaved identification notes in
    run_nano_banana. Label vocabulary must stay in sync with IMAGE_SYSTEM_PROMPT's
    rules-by-kind.
    """
    def warn(msg: str) -> None:
        if log is not None:
            log.append(f"  Warning: {msg}")
        else:
            print(f"Warning: {msg}", file=sys.stderr)

    cast_index: dict[str, dict] = {
        c.get("name", ""): c for c in story.get("cast", [])
    }
    page_cast: list[str] = page.get("cast", [])

    # Partition the page cast by kind, preserving page order within each group.
    characters: list[tuple[str, dict]] = []
    objects: list[tuple[str, dict]] = []
    locations: list[tuple[str, dict]] = []
    for name in page_cast:
        entry = cast_index.get(name)
        if entry is None:
            warn(f"page references unknown cast name {name!r}; skipping.")
            continue
        kind = (entry.get("kind") or "character").strip() or "character"
        if kind == "object":
            objects.append((name, entry))
        elif kind == "location":
            locations.append((name, entry))
        else:  # character (default)
            characters.append((name, entry))

    # Prioritized (label, path) candidates; trimmed to the cap below.
    candidates: list[tuple[str, str]] = []

    for i, (name, entry) in enumerate(characters):
        sheet = entry.get("style_sheet", "")
        if not sheet:
            warn(
                f"character {name!r} has no style_sheet; skipping. "
                "Run make_style_sheet.py first."
            )
        elif not Path(sheet).exists():
            warn(f"style sheet for {name!r} not found on disk ({sheet}); skipping.")
        else:
            candidates.append((f"character style sheet for {name}", sheet))
        # Hero (first character-kind entry in page order) also contributes its
        # real photo for face fidelity.
        if i == 0:
            photos = _ref_photos(entry)
            if photos:
                candidates.append(
                    (f"real photograph of the character {name} (facial likeness reference)", photos[0])
                )

    for name, entry in objects:
        sheet = entry.get("style_sheet", "")
        if sheet and Path(sheet).exists():
            candidates.append((f"object reference sheet for {name}", sheet))
        else:
            if sheet:
                warn(
                    f"object sheet for {name!r} not found on disk ({sheet}); "
                    f"falling back to photo."
                )
            photos = _ref_photos(entry)
            if photos:
                candidates.append(
                    (f"real photograph of the object {name} (appearance reference)", photos[0])
                )
            else:
                warn(
                    f"object {name!r} has neither a usable style_sheet nor a "
                    f"ref_image; skipping. Run make_style_sheet.py first."
                )

    # Location refs — lowest priority, first to drop from the cap.
    for name, entry in locations:
        sheet = entry.get("style_sheet", "")
        if sheet and Path(sheet).exists():
            candidates.append((f"location reference sheet for {name}", sheet))
        else:
            if sheet:
                warn(
                    f"location sheet for {name!r} not found on disk ({sheet}); "
                    f"falling back to photo."
                )
            photos = _ref_photos(entry)
            if photos:
                candidates.append(
                    (f"real photograph of the location {name} (setting reference)", photos[0])
                )
            else:
                warn(
                    f"location {name!r} has neither a style_sheet nor a "
                    f"ref_image; skipping."
                )

    selected = candidates[:max_images]
    dropped = [label for label, _ in candidates[max_images:]]
    if dropped:
        msg = f"cap ({max_images}) reached; dropped: {', '.join(dropped)}"
        if log is not None:
            log.append(f"  Note: {msg}")
        else:
            print(f"Note: {msg}")

    return selected


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


class _LazyClient:
    """Builds the genai.Client on first paid API call.

    Allows runs that only rebuild free artifacts (e.g. a missing text page in long mode,
    or an overlay composite when the raw already exists) to complete without a
    GEMINI_API_KEY in the environment, honoring the repo's idempotency contract:
    'regen free artifacts for free'.

    When composite_only=True, any would-be paid Gemini call returns False immediately
    with a clear error message — guaranteeing zero API spend.
    """

    def __init__(self, composite_only: bool = False) -> None:
        self._client = None
        self.composite_only = composite_only

    def get(self):
        if self._client is None:
            from google import genai as _genai
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                print(
                    "ERROR: GEMINI_API_KEY is not set in the environment.",
                    file=sys.stderr,
                )
                sys.exit(1)
            self._client = _genai.Client(api_key=api_key)
        return self._client


def _ensure_png(data: bytes) -> bytes:
    """Transcode image bytes to PNG when they aren't already.

    The Gemini API may return JPEG inline data; every downstream consumer
    (merge_epub.py's IHDR parser, the EPUB image/png media-type, the .png file
    contract) requires real PNG bytes, so convert at the save site.
    Keep in sync with the copy in make_style_sheet.py.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).save(buf, format="PNG")
    return buf.getvalue()


async def run_nano_banana(
    client: _LazyClient,
    prompt: str,
    raw_path: Path,
    story: dict,
    page: dict,
    resolution: str,
    log: list[str],
    aspect_ratio: str | None = None,
    model: str = IMAGE_MODEL,
) -> bool:
    """Generate one illustration via the Gemini API and write it to raw_path."""
    # --composite-only guard: refuse any paid call, return False so the run continues.
    # The caller checks the return value; the run exits 1 via the "N page(s) failed" path.
    if client.composite_only:
        log.append(
            f"  ERROR: {raw_path.name} requires a paid Gemini call, but --composite-only "
            "is set. Delete only the composite output (not the raw/art file) and re-run, "
            "or run without --composite-only to generate the image."
        )
        return False

    from google.genai import errors, types

    # Build contents: text prompt, then for each reference image a short
    # identification note followed by the image Part. The note tells the model
    # what the next image IS (sheet vs photograph, per cast kind);
    # the behavioural rules for each kind live in IMAGE_SYSTEM_PROMPT.
    contents: list = [prompt]
    ref_pairs = collect_input_images(
        story, page, log,
        max_images=MODEL_MAX_INPUT_IMAGES.get(model, MAX_INPUT_IMAGES),
    )
    for label, img_path in ref_pairs:
        p = Path(img_path)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "image/png"
        contents.append(f"Next image: {label}.")
        contents.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
    if ref_pairs:
        log.append(
            f"  Refs: {'; '.join(label for label, _ in ref_pairs)}"
        )

    config = types.GenerateContentConfig(
        system_instruction=IMAGE_SYSTEM_PROMPT,
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(image_size=resolution, aspect_ratio=aspect_ratio),
    )

    log.append(f"  Generating: {raw_path.name} ({model})")
    # Build the genai client lazily — only on first actual paid call, so runs that
    # only rebuild free artifacts (text pages in long mode) need no GEMINI_API_KEY.
    genai_client = client.get()
    response = None
    for attempt in range(MAX_RETRIES):
        last_exc: Exception
        try:
            response = await genai_client.aio.models.generate_content(
                model=model,
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
        raw_path.write_bytes(_ensure_png(image_data))
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


async def run_text_page(
    image_path: Path,
    text: str,
    color: str,
    font: str,
    font_name: str | None,
    align: str,
    out_path: Path,
    canvas_from: Path | None,
    log: list[str],
) -> bool:
    """Shell overlay_text.py in text-page mode (long story mode body pages). No API cost."""
    cmd = [
        "uv", "run", str(OVERLAY_SCRIPT),
        "--image", str(image_path),
        "--text", text,
        "--text-page",
        "--out", str(out_path),
        "--color", color,
        "--font", font,
        "--align", align,
    ]
    if font_name:
        cmd += ["--font-name", font_name]
    if canvas_from is not None:
        cmd += ["--canvas-from", str(canvas_from)]
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


async def render_page(client, page: dict, story: dict, pages_dir: Path, resolution: str, text_mode: str = "native", aspect_ratio: str | None = None, cli_model: str | None = None) -> bool:
    """Render one page (nano-banana + optional overlay/text-page). Prints its own log atomically. Page-independent."""
    page_num = page["page_num"]
    # Resolve model once: CLI override > page field > story field > default flash.
    model = resolve_model(story, page, cli_model)
    log: list[str] = [f"=== Page {page_num} (text-mode: {text_mode}) ==="]
    nn = f"{page_num:02d}"

    # ---- Long story mode --------------------------------------------------------
    if text_mode == "long":
        is_cover = page_num == 1

        if is_cover:
            # Cover: single combined page rendered overlay-style (title on art, no split).
            final_path = pages_dir / "page-01-long.png"
            if not final_path.exists():
                raw_path = pages_dir / "raw-page-01-long.png"
                if not raw_path.exists():
                    prompt = build_image_prompt(page, story, "overlay")
                    ok = await run_nano_banana(client, prompt, raw_path, story, page, resolution, log, aspect_ratio, model=model)
                    if not ok or not raw_path.exists():
                        log.append("  ERROR: image generation failed for cover")
                        print("\n" + "\n".join(log))
                        return False
                text = page.get("text", "")
                placement = _overlay_placement(page.get("text_placement", "floating"))
                color = page.get("text_color_hint", "dark")
                font = page.get("font", "reader")
                font_name = (story.get("fonts") or {}).get(font)
                align = page.get("text_align", "left")
                ok = await run_overlay(raw_path, text, placement, color, font, font_name, align, final_path, log)
                if not ok or not final_path.exists():
                    log.append("  ERROR: text overlay failed for cover")
                    print("\n" + "\n".join(log))
                    return False
            log.append(f"  Done: {final_path}")
            log.append(f"MEDIA: {final_path}")
            print("\n" + "\n".join(log))
            return True

        # Body page: art page + optional text page.
        art_path = pages_dir / f"page-{nn}-long.png"
        if not art_path.exists():
            prompt = build_image_prompt(page, story, "long")
            ok = await run_nano_banana(client, prompt, art_path, story, page, resolution, log, aspect_ratio, model=model)
            if not ok or not art_path.exists():
                log.append(f"  ERROR: art image generation failed for page {page_num}")
                print("\n" + "\n".join(log))
                return False

        page_text = page.get("text", "").strip()
        if not page_text:
            text_bg_prompt = page.get("text_background_prompt", "")
            if text_bg_prompt:
                log.append(f"  Warning: text_background_prompt set but text is empty; skipping text page.")
            log.append(f"  Done: {art_path}")
            log.append(f"MEDIA: {art_path}")
            print("\n" + "\n".join(log))
            return True

        # Shared text-page rendering args.
        color = page.get("text_color_hint", "dark")
        font = page.get("font", "reader")
        font_name = (story.get("fonts") or {}).get(font)
        align = page.get("text_align", "left")
        text_path = pages_dir / f"page-{nn}-long-text.png"

        if not text_path.exists():
            if page.get("text_background_prompt", ""):
                # Per-page override: dedicated background (+1 paid call, idempotent on bg_path).
                bg_path = pages_dir / f"page-{nn}-long-bg.png"
                if not bg_path.exists():
                    bg_prompt_str = build_text_bg_prompt(story, page)
                    ok = await run_nano_banana(
                        client, bg_prompt_str, bg_path, story, {"cast": []},
                        resolution, log, aspect_ratio, model=model,
                    )
                    if not ok or not bg_path.exists():
                        log.append(f"  ERROR: text bg generation failed for page {page_num}")
                        print("\n" + "\n".join(log))
                        return False
            else:
                # Default: the book-wide shared background, generated once in render_all.
                bg_path = pages_dir / SHARED_TEXT_BG_NAME
                if not bg_path.exists():
                    log.append(f"  ERROR: shared text-page background missing ({bg_path}); its generation failed earlier — re-run to retry")
                    print("\n" + "\n".join(log))
                    return False
            # bg_path is the source image; canvas_from=art_path locks the dims.
            ok = await run_text_page(bg_path, page_text, color, font, font_name, align, text_path, art_path, log)
            if not ok or not text_path.exists():
                log.append(f"  ERROR: text page failed for page {page_num}")
                print("\n" + "\n".join(log))
                return False

        log.append(f"  Done: {art_path}, {text_path}")
        log.append(f"MEDIA: {art_path}")
        log.append(f"MEDIA: {text_path}")
        print("\n" + "\n".join(log))
        return True

    # ---- Native / overlay modes -------------------------------------------------
    suffix = "-native" if text_mode == "native" else ""
    final_path = pages_dir / f"page-{nn}{suffix}.png"

    prompt = build_image_prompt(page, story, text_mode)

    if text_mode == "native":
        # In native mode the model bakes text into the illustration — write directly
        # to final_path; no separate raw file needed.
        ok = await run_nano_banana(client, prompt, final_path, story, page, resolution, log, aspect_ratio, model=model)
        if not ok or not final_path.exists():
            log.append(f"  ERROR: image generation failed for page {page_num}")
            print("\n" + "\n".join(log))
            return False
    else:
        raw_path = pages_dir / f"raw-page-{nn}.png"
        # Generate only when the raw doesn't already exist — mirrors the long-cover guard
        # at :762.  Deleting page-NN.png alone (without its raw) triggers a free
        # re-composite; deleting both triggers a paid re-render.
        if not raw_path.exists():
            ok = await run_nano_banana(client, prompt, raw_path, story, page, resolution, log, aspect_ratio, model=model)
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


async def render_all(todo: list[dict], story: dict, pages_dir: Path, resolution: str, text_mode: str = "native", aspect_ratio: str | None = None, cli_model: str | None = None, composite_only: bool = False) -> int:
    """Fire every page concurrently. Returns the number of failures.

    The genai.Client is built lazily on the first actual paid API call via _LazyClient,
    so runs that only rebuild free artifacts (e.g. text pages in long mode, overlay
    composites when the raw already exists) need no GEMINI_API_KEY.

    When composite_only=True, any page that would require a paid Gemini call fails with
    a clear error; free pages (existing raw/art/bg → overlay only) succeed normally.
    """
    client = _LazyClient(composite_only=composite_only)

    # Long mode: the shared text-page background is one per book — generate it once,
    # sequentially, BEFORE the pages fire (pages only consume it; generating it inside
    # render_page would race across concurrent pages). Only when some todo page will
    # actually need it: a body page with text, no per-page override, text page missing.
    if text_mode == "long":
        shared_bg = pages_dir / SHARED_TEXT_BG_NAME
        needs_shared_bg = not shared_bg.exists() and any(
            p["page_num"] != 1
            and p.get("text", "").strip()
            and not p.get("text_background_prompt", "")
            and not (pages_dir / f"page-{p['page_num']:02d}-long-text.png").exists()
            for p in todo
        )
        if needs_shared_bg:
            log = ["=== Shared text-page background ==="]
            prompt = build_text_bg_prompt(story)
            # Shared bg: use book-level model resolution (no page in scope).
            bg_model = resolve_model(story, None, cli_model)
            ok = await run_nano_banana(
                client, prompt, shared_bg, story, {"cast": []},
                resolution, log, aspect_ratio, model=bg_model,
            )
            if ok and shared_bg.exists():
                log.append(f"  Done: {shared_bg}")
                log.append(f"MEDIA: {shared_bg}")
            else:
                # Don't abort: art pages can still render; their text pages will fail
                # with a clear message, and a re-run retries the bg cheaply.
                log.append("  ERROR: shared text-page background generation failed; text pages will fail this run")
            print("\n" + "\n".join(log))

    print(f"\nRendering {len(todo)} page(s) concurrently ({text_mode} mode)...")
    results = await asyncio.gather(
        *(render_page(client, page, story, pages_dir, resolution, text_mode, aspect_ratio, cli_model=cli_model) for page in todo)
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
    parser.add_argument(
        "--model",
        choices=IMAGE_MODELS,
        default=None,
        help=(
            "Override the image model for every page this run "
            "(default: per-page 'model' field, then story.json top-level 'model', "
            "then gemini-3.1-flash-image). Page renders only; style sheets always use "
            "gemini-3-pro-image regardless of this setting."
        ),
    )
    parser.add_argument("--from", dest="from_page", type=int, default=1,
                        help="Start from this page number (1-indexed)")
    parser.add_argument("--only", dest="only_page", type=int, default=None,
                        help="Render only this page number")
    parser.add_argument(
        "--text-mode",
        dest="text_mode",
        choices=["overlay", "native", "long"],
        default=None,
        help=(
            "Override story.json's text_mode for this run. "
            "overlay: generate image with text-safe zone, then Pillow-composite text. "
            "native: ask the model to render story text directly into the illustration "
            "(output goes to page-NN-native.png). "
            "long: full-bleed art + separate text page per body page (page-NN-long.png + "
            "page-NN-long-text.png); cover stays combined (overlay-style). Text pages "
            "share one model-generated background per book (text-bg-long.png, +1 paid "
            "call total); a page with text_background_prompt gets a dedicated bg instead "
            "(+1 call for that page). "
            "If omitted, uses story.json's top-level 'text_mode' (default native)."
        ),
    )
    parser.add_argument(
        "--composite-only",
        dest="composite_only",
        action="store_true",
        default=False,
        help=(
            "Abort instead of making any paid Gemini call; only rebuild free Pillow "
            "composites (overlay text panels, long-mode text pages, long cover) from "
            "existing raw/art/bg files. Needs no GEMINI_API_KEY. "
            "Pages that would require a new image are reported as failures (exit 1) "
            "with a message naming the missing prerequisite file."
        ),
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    story = load_story(story_path)
    reject_legacy_keys(story)
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

    # Warn if no style sheets have been generated yet for sheet-eligible cast entries.
    cast = story.get("cast", [])
    sheetable = [
        c for c in cast
        if not (
            (c.get("kind") or "character") == "location" and c.get("ref_image")
        )
    ]
    if sheetable and not any(c.get("style_sheet") for c in sheetable):
        print("Warning: no style_sheet paths found in story.json's cast.")
        print("Run make_style_sheet.py first for better consistency.")
        print()

    # Select pages to render, skipping filtered-out and already-existing ones.
    # Long mode uses its own skip logic (two physical files per logical page);
    # native/overlay map 1-to-1 via a filename suffix.
    todo: list[dict] = []
    for page in pages:
        page_num = page["page_num"]
        if args.only_page is not None and page_num != args.only_page:
            continue
        if page_num < args.from_page:
            continue

        if text_mode == "long":
            is_cover = page_num == 1
            art_path = pages_dir / ("page-01-long.png" if is_cover else f"page-{page_num:02d}-long.png")
            if is_cover:
                if art_path.exists():
                    print(f"Page {page_num} (cover): already exists, skipping. ({art_path})")
                    print(f"MEDIA: {art_path}")
                    continue
            else:
                page_text = page.get("text", "").strip()
                text_path = pages_dir / f"page-{page_num:02d}-long-text.png"
                art_done = art_path.exists()
                text_done = not page_text or text_path.exists()
                if art_done and text_done:
                    print(f"Page {page_num}: already exists, skipping. ({art_path})")
                    print(f"MEDIA: {art_path}")
                    if page_text and text_path.exists():
                        print(f"MEDIA: {text_path}")
                    continue
        else:
            suffix = "-native" if text_mode == "native" else ""
            final_path = pages_dir / f"page-{page_num:02d}{suffix}.png"
            if final_path.exists():
                print(f"Page {page_num}: already exists, skipping. ({final_path})")
                print(f"MEDIA: {final_path}")
                continue

        todo.append(page)

    errors = asyncio.run(render_all(todo, story, pages_dir, resolution, text_mode, aspect_ratio, cli_model=args.model, composite_only=args.composite_only)) if todo else 0

    print(f"\n{'All pages rendered.' if errors == 0 else f'{errors} page(s) failed.'}")
    if errors:
        sys.exit(1)

    if args.only_page is None:
        print(
            "Next: assemble the book with storybook-consolidate "
            "(merge_pdf.py / merge_epub.py) — free, no API cost."
        )


if __name__ == "__main__":
    main()
