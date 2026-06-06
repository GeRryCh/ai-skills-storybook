#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Local visual editor for story.json — a browser form instead of raw JSON.

Serves a single-page editor (assets/editor.html) plus a tiny JSON API over
plain stdlib http.server, bound to 127.0.0.1 only. No image API cost, no
external dependencies, no build step.

Endpoints:
  GET  /            editor page
  GET  /api/story   raw story.json bytes (+ X-Story-Mtime / X-Story-Path headers)
  PUT  /api/story   validated atomic save (409 if file changed on disk meanwhile,
                    422 with {errors, warnings} when validation blocks the save)
  GET  /api/schema  assets/story_schema.json — the client derives enum options,
                    defaults, and required-field sets from it
  GET  /api/status  per-page rendered flags (pages/page-NN[-native].png exists)
                    and per-character style_sheet existence
  GET  /img?path=…  image preview. Absolute paths are served as-is; relative
                    paths resolve against the story.json directory.

/img security stance: serving absolute paths outside the book directory is the
feature — `ref_image` is documented as absolute paths anywhere on disk. There
is deliberately no root jail; the guard is regular-file + image-extension
allowlist + localhost-only binding. The user already has read access to their
own machine; this tool adds no privilege.

Round-trip contract: the server never reshapes the document. GET returns the
raw file bytes; PUT writes exactly the object the client sent with
json.dump(indent=2, ensure_ascii=False) — the same formatter
make_style_sheet.py already uses — preserving key order and unknown keys.
A trailing newline is preserved iff the file on disk had one.

Usage:
  uv run edit_story.py --story /path/to/story.json [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# App assets (editor.html, story_schema.json) live next to this script's skill,
# resolved via __file__ — uv run is invoked from arbitrary working directories.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SCHEMA_PATH = ASSETS_DIR / "story_schema.json"
EDITOR_PATH = ASSETS_DIR / "editor.html"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

DEFAULT_PORT = 8765


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _schema_enums(schema: dict) -> dict[str, list]:
    """Pull every enum the validator needs out of story_schema.json — single
    source of truth shared with the client (which fetches /api/schema)."""
    top = schema["properties"]
    page = top["pages"]["items"]["properties"]
    return {
        "age_band": top["age_band"]["enum"],
        "text_mode": top["text_mode"]["enum"],
        "resolution": top["resolution"]["enum"],
        "aspect_ratio": top["aspect_ratio"]["enum"],
        "saved_formats": top["saved_formats"]["items"]["enum"],
        "text_placement": page["text_placement"]["enum"],
        "text_align": page["text_align"]["enum"],
        "font": page["font"]["enum"],
    }


def _known_keys(schema: dict) -> dict[str, set]:
    top = schema["properties"]
    return {
        "top": set(top.keys()),
        "style_guide": set(top["style_guide"]["properties"].keys()),
        "character": set(top["characters"]["items"]["properties"].keys()),
        "fonts": set(top["fonts"]["properties"].keys()),
        "page": set(top["pages"]["items"]["properties"].keys()),
    }


def resolve_story_rel(path_str: str, story_dir: Path) -> Path:
    """Resolve a story-data path: absolute as-is, relative against the
    story.json directory (NOT cwd — deliberate divergence from the paid
    scripts, which assume they run from the story dir)."""
    p = Path(path_str)
    return p if p.is_absolute() else story_dir / p


def _ref_image_list(char: dict) -> list:
    """Normalize the string-or-array ref_image polymorphism to a list."""
    ref = char.get("ref_image")
    if ref is None:
        return []
    return ref if isinstance(ref, list) else [ref]


def validate_story(
    story: object, story_dir: Path, schema: dict
) -> tuple[list[str], list[str]]:
    """
    Hand-rolled validation (stdlib only — no jsonschema dependency).

    Returns (errors, warnings). Errors block the save; warnings never do.
    Required strings are checked for presence + type, NOT non-emptiness —
    `text: ""` is legitimate for wordless pages per the schema.
    """
    errors: list[str] = []
    warnings: list[str] = []
    enums = _schema_enums(schema)
    known = _known_keys(schema)

    if not isinstance(story, dict):
        return ["story.json root must be a JSON object"], []

    def warn_unknown(obj: dict, known_set: set, where: str) -> None:
        for k in obj:
            if k not in known_set:
                warnings.append(f"{where}: unknown key '{k}' (kept as-is)")

    warn_unknown(story, known["top"], "top level")

    # --- required top-level keys -------------------------------------------
    for key in schema.get("required", []):
        if key not in story:
            errors.append(f"missing required field '{key}'")

    for key in ("title", "style"):
        if key in story and not isinstance(story[key], str):
            errors.append(f"'{key}' must be a string")

    if "age_band" in story and story["age_band"] not in enums["age_band"]:
        errors.append(
            f"'age_band' must be one of {enums['age_band']} "
            f"(got {story['age_band']!r})"
        )

    # --- style_guide ---------------------------------------------------------
    sg = story.get("style_guide")
    if sg is not None:
        if not isinstance(sg, dict):
            errors.append("'style_guide' must be an object")
        else:
            warn_unknown(sg, known["style_guide"], "style_guide")
            if not any(v for v in sg.values()):
                errors.append(
                    "'style_guide' must have at least one non-empty field "
                    "(medium / palette / line / lighting / mood) — "
                    "both paid scripts refuse to run without it"
                )
            palette = sg.get("palette")
            if palette is not None and (
                not isinstance(palette, list)
                or not all(isinstance(s, str) for s in palette)
            ):
                errors.append("'style_guide.palette' must be an array of strings")

    # --- optional top-level enums/types -------------------------------------
    for key in ("text_mode", "resolution", "aspect_ratio"):
        if key in story and story[key] not in enums[key]:
            errors.append(
                f"'{key}' must be one of {enums[key]} (got {story[key]!r})"
            )
    if "language" in story and not isinstance(story["language"], str):
        errors.append("'language' must be a string")
    fonts = story.get("fonts")
    if fonts is not None:
        if not isinstance(fonts, dict):
            errors.append("'fonts' must be an object")
        else:
            warn_unknown(fonts, known["fonts"], "fonts")
            for role, fam in fonts.items():
                if role in known["fonts"] and not isinstance(fam, str):
                    errors.append(f"'fonts.{role}' must be a string")
    sf = story.get("saved_formats")
    if sf is not None:
        if not isinstance(sf, list) or not all(
            v in enums["saved_formats"] for v in sf
        ):
            errors.append(
                f"'saved_formats' must be an array drawn from "
                f"{enums['saved_formats']}"
            )

    # --- characters ----------------------------------------------------------
    cast_names: list[str] = []
    chars = story.get("characters")
    if chars is not None:
        if not isinstance(chars, list) or not chars:
            errors.append("'characters' must be a non-empty array")
        else:
            for i, char in enumerate(chars):
                where = f"characters[{i}]"
                if not isinstance(char, dict):
                    errors.append(f"{where} must be an object")
                    continue
                warn_unknown(char, known["character"], where)
                for req in ("name", "appearance"):
                    if not isinstance(char.get(req), str):
                        errors.append(f"{where} missing string field '{req}'")
                name = char.get("name")
                if isinstance(name, str):
                    if name in cast_names:
                        warnings.append(f"duplicate character name '{name}'")
                    cast_names.append(name)
                refs = char.get("ref_image")
                if refs is not None and not (
                    isinstance(refs, str)
                    or (
                        isinstance(refs, list)
                        and all(isinstance(r, str) for r in refs)
                    )
                ):
                    errors.append(
                        f"{where}.ref_image must be a string or array of strings"
                    )
                    refs = None
                ref_list = _ref_image_list({"ref_image": refs} if refs else {})
                if len(ref_list) > 5:
                    warnings.append(
                        f"{where} has {len(ref_list)} ref images; "
                        "only the first 5 are used (Gemini character-lane limit)"
                    )
                for r in ref_list:
                    if not resolve_story_rel(r, story_dir).exists():
                        warnings.append(f"{where}.ref_image not found on disk: {r}")
                sheet = char.get("style_sheet")
                if sheet is not None:
                    if not isinstance(sheet, str):
                        errors.append(f"{where}.style_sheet must be a string")
                    elif not resolve_story_rel(sheet, story_dir).exists():
                        warnings.append(
                            f"{where}.style_sheet not found on disk: {sheet}"
                        )

    # --- pages ----------------------------------------------------------------
    pages = story.get("pages")
    if pages is not None:
        if not isinstance(pages, list) or not pages:
            errors.append("'pages' must be a non-empty array")
        else:
            page_required = schema["properties"]["pages"]["items"]["required"]
            nums: list = []
            for i, page in enumerate(pages):
                where = f"pages[{i}]"
                if not isinstance(page, dict):
                    errors.append(f"{where} must be an object")
                    continue
                warn_unknown(page, known["page"], where)
                for req in page_required:
                    if req not in page:
                        errors.append(f"{where} missing required field '{req}'")
                num = page.get("page_num")
                if num is not None:
                    if not isinstance(num, int) or isinstance(num, bool) or num < 1:
                        errors.append(f"{where}.page_num must be a positive integer")
                    else:
                        nums.append(num)
                for key in ("text", "image_prompt", "text_color_hint"):
                    if key in page and not isinstance(page[key], str):
                        errors.append(f"{where}.{key} must be a string")
                for key in ("text_placement", "text_align", "font"):
                    if key in page and page[key] not in enums[key]:
                        errors.append(
                            f"{where}.{key} must be one of {enums[key]} "
                            f"(got {page[key]!r})"
                        )
                pc = page.get("characters")
                if pc is not None:
                    if not isinstance(pc, list) or not all(
                        isinstance(n, str) for n in pc
                    ):
                        errors.append(f"{where}.characters must be an array of strings")
                    else:
                        for n in pc:
                            if n not in cast_names:
                                errors.append(
                                    f"{where}.characters: '{n}' is not in the cast "
                                    f"({cast_names}) — names must match "
                                    "characters[].name exactly"
                                )
            if nums and sorted(nums) != list(range(1, len(nums) + 1)):
                warnings.append(
                    f"page_num sequence is not contiguous 1..{len(nums)}: {nums}"
                )

    return errors, warnings


# ---------------------------------------------------------------------------
# Atomic save
# ---------------------------------------------------------------------------


def save_story(story: dict, story_path: Path) -> None:
    """Atomic write preserving the file's trailing-newline state.

    The repo's only other save site (make_style_sheet.py) writes no trailing
    newline, while hand-authored files typically end with one — detect and
    preserve whichever the current file has.
    """
    had_newline = True
    try:
        had_newline = story_path.read_bytes().endswith(b"\n")
    except OSError:
        pass
    payload = json.dumps(story, indent=2, ensure_ascii=False)
    if had_newline:
        payload += "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=story_path.parent, prefix=story_path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, story_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


def make_handler(story_path: Path, schema: dict):
    story_dir = story_path.parent

    class EditorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "StoryEditor/1.0"

        # -- plumbing -------------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:
            pass  # stay quiet; errors are reported as JSON responses

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, obj: dict, extra: dict | None = None):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8", extra)

        def _fail(self, code: int, message: str):
            self._send_json(code, {"ok": False, "error": message})

        # -- GET --------------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            url = urlparse(self.path)
            route = url.path
            if route == "/":
                self._serve_editor()
            elif route == "/api/story":
                self._get_story()
            elif route == "/api/schema":
                self._send(
                    200,
                    SCHEMA_PATH.read_bytes(),
                    "application/json; charset=utf-8",
                )
            elif route == "/api/status":
                self._get_status()
            elif route == "/img":
                self._get_img(url)
            else:
                self._fail(404, f"no such route: {route}")

        def _serve_editor(self) -> None:
            try:
                body = EDITOR_PATH.read_bytes()
            except OSError:
                self._fail(500, f"editor.html not found at {EDITOR_PATH}")
                return
            self._send(200, body, "text/html; charset=utf-8")

        def _get_story(self) -> None:
            try:
                body = story_path.read_bytes()
                mtime = story_path.stat().st_mtime_ns
            except OSError as e:
                self._fail(500, f"cannot read {story_path}: {e}")
                return
            # Raw bytes passthrough — zero chance of server-side mutation.
            self._send(
                200,
                body,
                "application/json; charset=utf-8",
                {"X-Story-Mtime": str(mtime), "X-Story-Path": str(story_path)},
            )

        def _get_status(self) -> None:
            try:
                with story_path.open(encoding="utf-8") as f:
                    story = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                self._fail(500, f"cannot read story.json: {e}")
                return
            pages_dir = story_dir / "pages"
            page_status: dict[str, dict] = {}
            for page in story.get("pages", []):
                num = page.get("page_num")
                if not isinstance(num, int) or isinstance(num, bool):
                    continue
                rendered = any(
                    (pages_dir / f"page-{num:02d}{sfx}.png").exists()
                    for sfx in ("", "-native")
                )
                page_status[str(num)] = {"rendered": rendered}
            char_status: dict[str, dict] = {}
            for char in story.get("characters", []):
                name, sheet = char.get("name"), char.get("style_sheet")
                if isinstance(name, str) and isinstance(sheet, str):
                    char_status[name] = {
                        "style_sheet_exists": resolve_story_rel(
                            sheet, story_dir
                        ).exists()
                    }
            self._send_json(
                200, {"ok": True, "pages": page_status, "characters": char_status}
            )

        def _get_img(self, url) -> None:
            path_str = parse_qs(url.query).get("path", [""])[0]
            if not path_str:
                self._fail(404, "missing ?path= parameter")
                return
            p = resolve_story_rel(path_str, story_dir).resolve()
            # Guard AFTER resolution: regular file + image extension only.
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                self._fail(404, f"not a previewable image: {path_str}")
                return
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            try:
                self._send(200, p.read_bytes(), ctype)
            except OSError as e:
                self._fail(500, f"cannot read image: {e}")

        # -- PUT/POST ----------------------------------------------------------
        def do_PUT(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/story":
                self._fail(404, "PUT is only supported on /api/story")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0:
                self._fail(400, "empty request body")
                return
            try:
                story = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._fail(400, f"request body is not valid JSON: {e}")
                return

            # Conflict check: protect against another writer (e.g. Stage 2
            # filling in style_sheet paths) while the editor is open.
            client_mtime = self.headers.get("X-Story-Mtime")
            if client_mtime is not None:
                try:
                    current = story_path.stat().st_mtime_ns
                except OSError:
                    current = None
                if current is not None and client_mtime != str(current):
                    self._send_json(
                        409,
                        {
                            "ok": False,
                            "error": "story.json changed on disk — reload the "
                            "editor before saving to avoid clobbering it",
                        },
                    )
                    return

            errors, warnings = validate_story(story, story_dir, schema)
            if errors:
                self._send_json(
                    422, {"ok": False, "errors": errors, "warnings": warnings}
                )
                return

            try:
                save_story(story, story_path)
            except OSError as e:
                self._fail(500, f"failed to write {story_path}: {e}")
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "warnings": warnings,
                    "mtime": str(story_path.stat().st_mtime_ns),
                },
            )

        do_POST = do_PUT

    return EditorHandler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local visual editor for story.json (no API cost)."
    )
    parser.add_argument("--story", required=True, help="Path to story.json")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind on 127.0.0.1 (default {DEFAULT_PORT}; "
        "falls back to an OS-assigned port when busy)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the editor in a browser (headless/smoke tests)",
    )
    args = parser.parse_args()

    story_path = Path(args.story).resolve()
    if not story_path.is_file():
        print(f"ERROR: story.json not found: {story_path}", file=sys.stderr)
        sys.exit(2)
    try:
        with story_path.open(encoding="utf-8") as f:
            json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: {story_path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    if not EDITOR_PATH.is_file():
        print(f"ERROR: editor asset missing: {EDITOR_PATH}", file=sys.stderr)
        sys.exit(2)

    schema = load_schema()
    handler = make_handler(story_path, schema)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError:
        # Port busy (another editor instance?) — let the OS pick one.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Editing {story_path}")
    print(f"Open {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
