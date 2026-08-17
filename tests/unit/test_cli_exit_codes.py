"""subprocess-based exit-code tests — no in-process side effects.

validate_story.py: exit 0 (valid story) / exit 2 (errors).
make_style_sheet.py: exit 2 when require_cast_ids or require_style_guide fire
  (these guards run before any API call, so no GEMINI_API_KEY needed).

First run of make_style_sheet.py tests may be slow (~30-60s) while uv resolves
the PEP-723 deps (openai, Pillow). Subsequent runs use the cached venv.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support

REPO_ROOT = _support.REPO_ROOT
VALIDATE = REPO_ROOT / "skills" / "storybook-story" / "scripts" / "validate_story.py"
MAKE_SHEET = REPO_ROOT / "skills" / "storybook-stylesheet" / "scripts" / "make_style_sheet.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

# Generous timeout: first run may trigger uv dep resolution/install.
_TIMEOUT = 120


def _run(script: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", str(script), *args],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT), timeout=_TIMEOUT,
    )


def _tmp_story(data: dict) -> Path:
    """Write a story dict to a NamedTemporaryFile; caller must unlink."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(data, f, indent=2)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# validate_story.py
# ---------------------------------------------------------------------------


class TestValidateStoryCLI(unittest.TestCase):
    """validate_story.py exit codes."""

    def _validate(self, path: Path) -> subprocess.CompletedProcess:
        return _run(VALIDATE, "--story", str(path))

    def test_pip_storm_exits_0(self):
        r = self._validate(FIXTURES / "pip-storm" / "story.json")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_pip_storm_long_exits_0(self):
        r = self._validate(FIXTURES / "pip-storm-long" / "story.json")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_gazelle_valley_exits_0(self):
        r = self._validate(FIXTURES / "gazelle-valley" / "story.json")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_missing_file_exits_2(self):
        r = self._validate(Path("/nonexistent/story.json"))
        self.assertEqual(r.returncode, 2)

    def test_unknown_placeholder_exits_2(self):
        p = _tmp_story({
            "title": "T", "style": "s",
            "style_guide": {"medium": "watercolor"},
            "cast": [{"id": "pip", "name": "Pip", "appearance": "a hedgehog"}],
            "pages": [{"page_num": 1, "cast": ["pip"], "text": "t",
                       "image_prompt": "<ghost> appears"}],
        })
        try:
            r = self._validate(p)
            self.assertEqual(r.returncode, 2)
            self.assertIn("ghost", r.stdout + r.stderr)
        finally:
            p.unlink(missing_ok=True)

    def test_character_lane_over_cap_exits_2(self):
        """PER-97: 6 character-kind entries on one page is a hard error."""
        cast = [
            {"id": f"char{i}", "name": f"Char {i}", "appearance": "a person"}
            for i in range(6)
        ]
        p = _tmp_story({
            "title": "T", "style": "s",
            "style_guide": {"medium": "watercolor"},
            "cast": cast,
            "pages": [{
                "page_num": 1,
                "cast": [c["id"] for c in cast],
                "text": "t",
                "image_prompt": " ".join(f"<{c['id']}>" for c in cast),
            }],
        })
        try:
            r = self._validate(p)
            self.assertEqual(r.returncode, 2, f"stdout: {r.stdout}\nstderr: {r.stderr}")
            self.assertIn("hard cap is 5", r.stdout + r.stderr)
        finally:
            p.unlink(missing_ok=True)

    def test_invalid_json_exits_2(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        f.write("{invalid json")
        f.close()
        p = Path(f.name)
        try:
            r = self._validate(p)
            self.assertEqual(r.returncode, 2)
        finally:
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# make_style_sheet.py — fail-fast guards (no API key required)
# ---------------------------------------------------------------------------


class TestMakeStyleSheetCLI(unittest.TestCase):
    """make_style_sheet.py exits 2 before any paid call for bad story.json."""

    def _run_sheet(self, path: Path) -> subprocess.CompletedProcess:
        return _run(MAKE_SHEET, "--story", str(path))

    def test_missing_style_guide_exits_2(self):
        """require_style_guide fires before any Gemini call; no API key needed."""
        p = _tmp_story({
            "title": "T", "style": "s",
            # deliberately no style_guide
            "cast": [{"id": "pip", "name": "Pip", "appearance": "a hedgehog"}],
            "pages": [{"page_num": 1, "cast": ["pip"], "text": "t",
                       "image_prompt": "<pip> runs"}],
        })
        try:
            r = self._run_sheet(p)
            self.assertEqual(
                r.returncode, 2,
                f"Expected exit 2 for missing style_guide.\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}",
            )
        finally:
            p.unlink(missing_ok=True)

    def test_missing_cast_id_exits_2(self):
        """require_cast_ids fires before require_style_guide; no API key needed."""
        p = _tmp_story({
            "title": "T", "style": "s",
            "style_guide": {"medium": "watercolor"},
            "cast": [{"name": "Pip", "appearance": "a hedgehog"}],  # no id!
            "pages": [{"page_num": 1, "cast": [], "text": "t", "image_prompt": "test"}],
        })
        try:
            r = self._run_sheet(p)
            self.assertEqual(
                r.returncode, 2,
                f"Expected exit 2 for missing cast id.\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}",
            )
        finally:
            p.unlink(missing_ok=True)


class TestMakeStyleSheetStyleFrameOnly(unittest.TestCase):
    """PER-82: --only must skip book-wide style-frame generation entirely — no API
    call, no story.json write. --only's contract is exactly one top-level diff (the
    named entry's style_sheet); the editor's per-cast regenerate flow (edit_story.py's
    _run_sheet_regen + editor.html's fetchSheetVersions) resyncs only that one field
    after an --only run, so a second unsynced top-level write would silently vanish
    on the next editor save.

    Zero-API: the cast entry's sheet already exists on disk (skip-if-exists branch,
    no generate_image call), and --only skips the frame block outright — no code path
    in this test reaches generate_image, so no OPENAI_API_KEY is needed regardless of
    what's set in the host environment.
    """

    def test_only_id_does_not_write_style_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sheet = root / "style-sheet-pip.png"
            sheet.touch()
            story_path = root / "story.json"
            story = {
                "title": "T", "style": "s",
                "style_guide": {"medium": "watercolor"},
                "cast": [{"id": "pip", "name": "Pip", "appearance": "a hedgehog",
                          "style_sheet": str(sheet)}],
                "pages": [{"page_num": 1, "cast": ["pip"], "text": "t",
                           "image_prompt": "<pip> runs"}],
            }
            story_path.write_text(json.dumps(story, indent=2))

            r = _run(MAKE_SHEET, "--story", str(story_path), "--only", "pip")
            self.assertEqual(
                r.returncode, 0,
                f"stdout: {r.stdout}\nstderr: {r.stderr}",
            )
            updated = json.loads(story_path.read_text())
            self.assertNotIn(
                "style_frame", updated,
                "--only must not write the book-wide style_frame field (would desync "
                "the editor's single-field resync and get silently dropped on save)",
            )


if __name__ == "__main__":
    unittest.main()
