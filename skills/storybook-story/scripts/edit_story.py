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
  GET  /                      editor page
  GET  /api/story             raw story.json bytes (+ X-Story-Mtime / X-Story-Path headers)
  PUT  /api/story             validated atomic save (409 if file changed on disk meanwhile,
                               422 with {errors, warnings} when validation blocks the save)
  GET  /api/schema            assets/story_schema.json — the client derives enum options,
                               defaults, and required-field sets from it
  GET  /api/status            per-page rendered flags (pages/page-NN[-native].png exists)
                               and per-character style_sheet existence
  GET  /api/versions?page=N   list generated versions for a page; pure read (no disk mutation).
                               Returns {ok, versions: [{id, path, mtime, in_use}], regen: {…}}
  GET  /img?path=…            image preview. Absolute paths are served as-is; relative
                               paths resolve against the story.json directory.
  POST /api/page/regenerate   re-render one page (paid Gemini call). Requires GEMINI_API_KEY
                               in the editor's environment. Spawns uv run render_book.py
                               --only N in a background thread; returns 200 immediately.
                               Poll /api/versions to watch progress.
  POST /api/page/select       copy a history version into the canonical slot (free, no API
                               call). Sets the "used in book" image for that page.

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

History layout (pages/ next to story.json):
  pages/
    page-NN.png                  ← canonical (consolidation input)
    history/
      page-NN/
        YYYYMMDD-HHMMSS/         ← one generation per stamped dir
          page-NN.png            (whatever artifacts existed are copied here)
          raw-page-NN.png
          …

"Used in book" = whichever history entry's preview-file hash matches the
canonical preview. No manifest; survives CLI renders and pre-feature books.
Adopt-on-mutate invariant: before any mutating op (regenerate, select) touches
the canonical slot, the current canonical is copied into history if its hash
is not already present there. GETs are pure (no disk mutation).

Usage:
  uv run edit_story.py --story /path/to/story.json [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
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

# render_book.py, resolved relative to this file's position in the skills/ tree:
#   skills/storybook-story/scripts/ → parents[2] = skills/
#   → skills/storybook-render/scripts/render_book.py
RENDER_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "storybook-render"
    / "scripts"
    / "render_book.py"
)

# Per-page regeneration job state, shared across all handler threads.
# Keys: page_num (int). Values: {"status": "running"|"done"|"error", "error": str|None}
_regen_jobs: dict = {}
_regen_lock = threading.Lock()


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
        "kind": top["cast"]["items"]["properties"]["kind"]["enum"],
    }


def _known_keys(schema: dict) -> dict[str, set]:
    top = schema["properties"]
    return {
        "top": set(top.keys()),
        "style_guide": set(top["style_guide"]["properties"].keys()),
        "cast_entry": set(top["cast"]["items"]["properties"].keys()),
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

    # --- legacy pre-PER-34 keys: hard errors with migration guidance -----------
    if "characters" in story:
        errors.append(
            "'characters' was renamed to 'cast' (PER-34) — rename the key; "
            "entry shape is unchanged (optional 'kind' field added)"
        )
    if "locations" in story:
        errors.append(
            "'locations' was removed (PER-34) — move each place into 'cast' "
            "with \"kind\": \"location\" (keep ref_image and source_url; fold "
            "'description' into 'appearance')"
        )
    if isinstance(story.get("pages"), list):
        for _pi, _p in enumerate(story["pages"]):
            if isinstance(_p, dict):
                if "characters" in _p:
                    errors.append(
                        f"pages[{_pi}].characters was renamed to pages[{_pi}].cast (PER-34)"
                    )
                if "location" in _p:
                    errors.append(
                        f"pages[{_pi}].location was removed (PER-34) — append the place "
                        f"name to pages[{_pi}].cast instead"
                    )
    if errors:
        # Return early: remaining validation will KeyError on the old keys.
        return errors, warnings

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
    if "text_background_prompt" in story and not isinstance(story["text_background_prompt"], str):
        errors.append("'text_background_prompt' must be a string")
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

    # --- cast ----------------------------------------------------------------
    cast_names: list[str] = []
    cast = story.get("cast")
    if cast is not None:
        if not isinstance(cast, list) or not cast:
            errors.append("'cast' must be a non-empty array")
        else:
            for i, entry in enumerate(cast):
                where = f"cast[{i}]"
                if not isinstance(entry, dict):
                    errors.append(f"{where} must be an object")
                    continue
                warn_unknown(entry, known["cast_entry"], where)
                for req in ("name", "appearance"):
                    if not isinstance(entry.get(req), str):
                        errors.append(f"{where} missing string field '{req}'")
                name = entry.get("name")
                if isinstance(name, str):
                    if name in cast_names:
                        warnings.append(f"duplicate cast name '{name}'")
                    cast_names.append(name)
                kind = entry.get("kind")
                if kind is not None and kind not in enums["kind"]:
                    errors.append(
                        f"{where}.kind must be one of {enums['kind']} (got {kind!r})"
                    )
                refs = entry.get("ref_image")
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
                        "only the first 5 are used (Gemini reference-image limit)"
                    )
                for r in ref_list:
                    if not resolve_story_rel(r, story_dir).exists():
                        warnings.append(f"{where}.ref_image not found on disk: {r}")
                sheet = entry.get("style_sheet")
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
                for key in ("text", "image_prompt", "text_color_hint", "text_background_prompt"):
                    if key in page and not isinstance(page[key], str):
                        errors.append(f"{where}.{key} must be a string")
                for key in ("text_placement", "text_align", "font"):
                    if key in page and page[key] not in enums[key]:
                        errors.append(
                            f"{where}.{key} must be one of {enums[key]} "
                            f"(got {page[key]!r})"
                        )
                pc = page.get("cast")
                if pc is not None:
                    if not isinstance(pc, list) or not all(
                        isinstance(n, str) for n in pc
                    ):
                        errors.append(f"{where}.cast must be an array of strings")
                    else:
                        for n in pc:
                            if n not in cast_names:
                                errors.append(
                                    f"{where}.cast: '{n}' is not in the cast "
                                    f"({cast_names}) — names must match "
                                    "cast[].name exactly"
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
# Generation history helpers
# ---------------------------------------------------------------------------

_STAMP_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def _file_hash(p: Path) -> str:
    """SHA-256 of a file — used to detect duplicate generations."""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_preview_candidates(num: int) -> list[str]:
    """Ordered list of preview filenames to probe on disk (overlay > native > long)."""
    nn = f"{num:02d}"
    return [
        f"page-{nn}.png",        # overlay
        f"page-{nn}-native.png", # native
        f"page-{nn}-long.png",   # long (body or cover)
    ]


def _canonical_artifact_names(num: int) -> list[str]:
    """All canonical artifact filenames archived and deleted on regenerate.

    This is the union across all text modes (on-disk mode may differ from
    story.text_mode when --text-mode was overridden at render time).
    Per-page bg and shared bg are excluded: bg is copied to history for
    completeness but never deleted (avoids a repeat paid bg call on regen).
    """
    nn = f"{num:02d}"
    return [
        f"page-{nn}.png",           # overlay final
        f"raw-page-{nn}.png",       # overlay raw
        f"page-{nn}-native.png",    # native
        f"page-{nn}-long.png",      # long body art / long cover final
        f"raw-page-{nn}-long.png",  # long cover raw (page 1 only)
        f"page-{nn}-long-text.png", # long body text page
    ]


def _adopt_artifact_names(num: int) -> list[str]:
    """Canonical artifacts + per-page bg — everything to copy into history."""
    return _canonical_artifact_names(num) + [f"page-{num:02d}-long-bg.png"]


def _history_dir(pages_dir: Path, num: int) -> Path:
    return pages_dir / "history" / f"page-{num:02d}"


def _list_history_entries(pages_dir: Path, num: int) -> list[dict]:
    """List history entries for page num, newest first.

    Each dict: {"id": stamp_str, "preview_path": Path, "mtime": float}
    """
    hdir = _history_dir(pages_dir, num)
    if not hdir.is_dir():
        return []
    entries = []
    for stamp_dir in sorted(hdir.iterdir(), reverse=True):
        if not stamp_dir.is_dir() or not _STAMP_RE.match(stamp_dir.name):
            continue
        preview: Path | None = None
        for name in _page_preview_candidates(num):
            p = stamp_dir / name
            if p.is_file():
                preview = p
                break
        if preview is None:
            continue
        try:
            mtime = stamp_dir.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append({"id": stamp_dir.name, "preview_path": preview, "mtime": mtime})
    return entries


def _adopt_canonical(pages_dir: Path, num: int) -> str | None:
    """Copy the current canonical artifacts into a new history entry.

    Returns the entry id (timestamp dir name) if something was copied, or the
    id of an existing matching entry if the hash is already in history, or None
    if nothing exists on disk to adopt. Does NOT delete canonical files.

    Idempotent: two calls with the same canonical file produce one history entry.
    """
    # Find the preview file
    preview_name: str | None = None
    for name in _page_preview_candidates(num):
        if (pages_dir / name).is_file():
            preview_name = name
            break
    if preview_name is None:
        return None

    try:
        canonical_hash = _file_hash(pages_dir / preview_name)
    except OSError:
        return None

    # Check for existing duplicate
    for entry in _list_history_entries(pages_dir, num):
        try:
            if _file_hash(entry["preview_path"]) == canonical_hash:
                return entry["id"]
        except OSError:
            pass

    # Create a new stamped entry
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    hdir = _history_dir(pages_dir, num)
    target = hdir / stamp
    suffix = 0
    while target.exists():
        suffix += 1
        target = hdir / f"{stamp}-{suffix}"
    target.mkdir(parents=True, exist_ok=True)

    for name in _adopt_artifact_names(num):
        src = pages_dir / name
        if src.is_file():
            try:
                shutil.copy2(src, target / name)
            except OSError:
                pass

    return target.name


def _restore_from_history(pages_dir: Path, num: int) -> None:
    """Copy the most recent history entry back to canonical filenames.

    Called on regen failure so the book isn't left with a hole.
    """
    entries = _list_history_entries(pages_dir, num)
    if not entries:
        return
    stamp_dir = _history_dir(pages_dir, num) / entries[0]["id"]
    for name in _canonical_artifact_names(num):
        src = stamp_dir / name
        if src.is_file():
            try:
                shutil.copy2(src, pages_dir / name)
            except OSError:
                pass


def _build_versions_payload(
    pages_dir: Path, story_dir: Path, num: int
) -> dict:
    """Build the {versions, regen} dict returned by GET /api/versions."""
    nn = f"{num:02d}"

    # Canonical preview (if any)
    canonical_preview_name: str | None = None
    for name in _page_preview_candidates(num):
        if (pages_dir / name).is_file():
            canonical_preview_name = name
            break

    canonical_hash: str | None = None
    if canonical_preview_name:
        try:
            canonical_hash = _file_hash(pages_dir / canonical_preview_name)
        except OSError:
            pass

    hist = _list_history_entries(pages_dir, num)
    versions = []
    canonical_found_in_history = False

    for entry in hist:
        try:
            h = _file_hash(entry["preview_path"])
        except OSError:
            continue
        in_use = canonical_hash is not None and h == canonical_hash
        if in_use:
            canonical_found_in_history = True
        try:
            rel = entry["preview_path"].relative_to(story_dir)
        except ValueError:
            rel = entry["preview_path"]
        versions.append(
            {
                "id": entry["id"],
                "path": str(rel),
                "mtime": entry["mtime"],
                "in_use": in_use,
            }
        )

    # Canonical exists but not yet in history: prepend pseudo-entry
    if canonical_preview_name and not canonical_found_in_history:
        try:
            mtime = (pages_dir / canonical_preview_name).stat().st_mtime
        except OSError:
            mtime = 0.0
        versions.insert(
            0,
            {
                "id": "current",
                "path": str(Path("pages") / canonical_preview_name),
                "mtime": mtime,
                "in_use": True,
            },
        )

    with _regen_lock:
        regen = dict(_regen_jobs.get(num, {"status": None, "error": None}))

    return {"versions": versions, "regen": regen}


def _run_regen(story_path: Path, pages_dir: Path, num: int, env: dict) -> None:
    """Thread target: invoke render_book.py --only N, then update _regen_jobs.

    On success: adopts the new canonical into history, marks done.
    On failure: restores the previous canonical from history, marks error.
    """
    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                str(RENDER_SCRIPT),
                "--story",
                str(story_path),
                "--only",
                str(num),
            ],
            cwd=str(story_path.parent),
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
        if result.returncode == 0:
            _adopt_canonical(pages_dir, num)
            with _regen_lock:
                _regen_jobs[num] = {"status": "done", "error": None}
        else:
            _restore_from_history(pages_dir, num)
            stderr_tail = (result.stderr or "").strip()[-800:]
            with _regen_lock:
                _regen_jobs[num] = {
                    "status": "error",
                    "error": stderr_tail or "render failed (no stderr captured)",
                }
    except subprocess.TimeoutExpired:
        _restore_from_history(pages_dir, num)
        with _regen_lock:
            _regen_jobs[num] = {
                "status": "error",
                "error": "render timed out after 10 minutes",
            }
    except Exception as exc:  # noqa: BLE001
        _restore_from_history(pages_dir, num)
        with _regen_lock:
            _regen_jobs[num] = {"status": "error", "error": str(exc)}


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
            elif route == "/api/versions":
                self._get_versions(url)
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
                    for sfx in ("", "-native", "-long")
                )
                page_status[str(num)] = {"rendered": rendered}
            cast_status: dict[str, dict] = {}
            for entry in story.get("cast", []):
                name, sheet = entry.get("name"), entry.get("style_sheet")
                if isinstance(name, str) and isinstance(sheet, str):
                    cast_status[name] = {
                        "style_sheet_exists": resolve_story_rel(
                            sheet, story_dir
                        ).exists()
                    }
            self._send_json(
                200, {"ok": True, "pages": page_status, "cast": cast_status}
            )

        def _get_versions(self, url) -> None:
            qs = parse_qs(url.query)
            try:
                num = int(qs.get("page", [""])[0])
            except (ValueError, IndexError):
                self._fail(400, "?page= must be a positive integer")
                return
            pages_dir = story_dir / "pages"
            payload = _build_versions_payload(pages_dir, story_dir, num)
            self._send_json(200, {"ok": True, **payload})

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

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/story":
                self.do_PUT()
            elif route == "/api/page/regenerate":
                self._post_regenerate()
            elif route == "/api/page/select":
                self._post_select()
            else:
                self._fail(404, f"no such POST route: {route}")

        # -- POST helpers ------------------------------------------------------

        def _read_json_body(self):
            """Read and parse the request body as JSON. Returns (obj, error_str)."""
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0:
                return None, "empty request body"
            try:
                return json.loads(self.rfile.read(length).decode("utf-8")), None
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return None, f"request body is not valid JSON: {e}"

        def _post_regenerate(self) -> None:
            body, err = self._read_json_body()
            if err:
                self._fail(400, err)
                return
            try:
                num = int(body["page_num"])
            except (KeyError, ValueError, TypeError):
                self._fail(400, "body must have page_num (integer)")
                return

            # GEMINI_API_KEY check — regenerate always triggers a paid call.
            if not os.environ.get("GEMINI_API_KEY"):
                self._fail(
                    400,
                    "GEMINI_API_KEY is not set in the editor's environment. "
                    "Restart the editor with the key: "
                    "GEMINI_API_KEY=your_key uv run edit_story.py --story …",
                )
                return

            if not RENDER_SCRIPT.is_file():
                self._fail(500, f"render script not found: {RENDER_SCRIPT}")
                return

            # Gate: only one regen per page at a time.
            with _regen_lock:
                job = _regen_jobs.get(num, {})
                if job.get("status") == "running":
                    self._fail(409, f"regeneration already running for page {num}")
                    return
                _regen_jobs[num] = {"status": "running", "error": None}

            pages_dir = story_dir / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)

            # Adopt existing canonical into history before deleting it.
            _adopt_canonical(pages_dir, num)

            # Delete canonical artifacts (except bg files — kept to avoid
            # a repeat paid bg call; render_book.py skips them when present).
            for name in _canonical_artifact_names(num):
                p = pages_dir / name
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

            # Spawn background render thread.
            env = os.environ.copy()
            t = threading.Thread(
                target=_run_regen,
                args=(story_path, pages_dir, num, env),
                daemon=True,
            )
            t.start()

            self._send_json(200, {"ok": True})

        def _post_select(self) -> None:
            body, err = self._read_json_body()
            if err:
                self._fail(400, err)
                return
            try:
                num = int(body["page_num"])
                version = str(body["version"])
            except (KeyError, ValueError, TypeError):
                self._fail(400, "body must have page_num (int) and version (str)")
                return

            # Gate: select and regen's restore both mutate the canonical slot.
            with _regen_lock:
                job = _regen_jobs.get(num, {})
                if job.get("status") == "running":
                    self._fail(
                        409,
                        f"regeneration is running for page {num} — "
                        "try again after it completes",
                    )
                    return

            pages_dir = story_dir / "pages"

            # "current" pseudo-entry means canonical is already correct — no-op.
            if version == "current":
                payload = _build_versions_payload(pages_dir, story_dir, num)
                self._send_json(200, {"ok": True, **payload})
                return

            # Validate stamp format (no path traversal).
            if not _STAMP_RE.match(version):
                self._fail(400, f"invalid version id: {version!r}")
                return

            stamp_dir = _history_dir(pages_dir, num) / version
            if not stamp_dir.is_dir():
                self._fail(404, f"version {version!r} not found for page {num}")
                return

            # Adopt current canonical before overwriting it.
            _adopt_canonical(pages_dir, num)

            # Copy history entry artifacts to canonical names.
            for name in _canonical_artifact_names(num):
                src = stamp_dir / name
                if src.is_file():
                    try:
                        shutil.copy2(src, pages_dir / name)
                    except OSError:
                        pass

            payload = _build_versions_payload(pages_dir, story_dir, num)
            self._send_json(200, {"ok": True, **payload})

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
            doc = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: {story_path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    # Fail fast on pre-PER-34 schema: the editor UI cannot bind legacy keys.
    legacy_keys_found: list[str] = []
    if "characters" in doc:
        legacy_keys_found.append('top-level "characters"')
    if "locations" in doc:
        legacy_keys_found.append('top-level "locations"')
    if isinstance(doc.get("pages"), list):
        for _p in doc["pages"]:
            if isinstance(_p, dict):
                pn = _p.get("page_num", "?")
                if "characters" in _p:
                    legacy_keys_found.append(f'pages[{pn}].characters')
                if "location" in _p:
                    legacy_keys_found.append(f'pages[{pn}].location')
    if legacy_keys_found:
        print(
            f"ERROR: story.json uses the pre-PER-34 schema. "
            f"Legacy keys found: {', '.join(legacy_keys_found)}\n\n"
            "Migrate story.json before opening the editor:\n"
            "  top-level \"characters\"  ->  \"cast\"\n"
            "  top-level \"locations\"   ->  cast entries with \"kind\": \"location\"\n"
            "  pages[].characters      ->  pages[].cast\n"
            "  pages[].location        ->  append the place name to pages[].cast\n"
            "See skills/storybook-story/assets/story_schema.json.",
            file=sys.stderr,
        )
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
