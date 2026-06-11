"""Unit tests for render_book.collect_input_images — priority order, disk gating, fallbacks.

collect_input_images reads disk via Path(...).exists() for every sheet/photo.
Tests use real empty temp files (only existence is checked — no real PNG needed).
Pass a log list to capture warnings without polluting stderr.
"""
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import render_book


def _touch(path: Path) -> str:
    """Create an empty file at path (parents created as needed). Return its str path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return str(path)


class TestCollectInputImages(unittest.TestCase):
    """render_book.collect_input_images — label strings, priority, disk-gating, fallbacks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _f(self, *names: str) -> dict[str, str]:
        """Create empty temp files; return {name: abs_path}."""
        return {n: _touch(self.root / n) for n in names}

    # ------------------------------------------------------------------
    # Hero character
    # ------------------------------------------------------------------

    def test_hero_gets_sheet_then_photo(self):
        """First character-kind entry (hero) contributes sheet + first photo, in that order."""
        f = self._f("hero-sheet.png", "hero-photo.png")
        story = {"cast": [
            {"id": "pip", "name": "Pip",
             "style_sheet": f["hero-sheet.png"],
             "ref_image": [f["hero-photo.png"]]},
        ]}
        result = render_book.collect_input_images(story, {"cast": ["pip"]}, log=[])
        labels = [l for l, _ in result]
        self.assertEqual(labels, [
            "character style sheet for Pip",
            "real photograph of the character Pip (facial likeness reference)",
        ])

    def test_hero_only_first_photo_included(self):
        """Hero has multiple ref_image photos; only the first is included."""
        f = self._f("sheet.png", "photo1.png", "photo2.png")
        story = {"cast": [
            {"id": "pip", "name": "Pip",
             "style_sheet": f["sheet.png"],
             "ref_image": [f["photo1.png"], f["photo2.png"]]},
        ]}
        result = render_book.collect_input_images(story, {"cast": ["pip"]}, log=[])
        paths = [p for _, p in result]
        self.assertIn(f["photo1.png"], paths)
        self.assertNotIn(f["photo2.png"], paths)

    # ------------------------------------------------------------------
    # Non-hero character
    # ------------------------------------------------------------------

    def test_non_hero_character_sheet_only(self):
        """Second character-kind entry: sheet only, no photo."""
        f = self._f("hero-s.png", "hero-p.png", "side-s.png")
        story = {"cast": [
            {"id": "hero", "name": "Hero",
             "style_sheet": f["hero-s.png"], "ref_image": [f["hero-p.png"]]},
            {"id": "sidekick", "name": "Sidekick", "style_sheet": f["side-s.png"]},
        ]}
        result = render_book.collect_input_images(
            story, {"cast": ["hero", "sidekick"]}, log=[]
        )
        labels = [l for l, _ in result]
        self.assertIn("character style sheet for Sidekick", labels)
        self.assertNotIn(
            "real photograph of the character Sidekick (facial likeness reference)", labels
        )

    # ------------------------------------------------------------------
    # Priority order across kinds
    # ------------------------------------------------------------------

    def test_full_priority_order(self):
        """hero sheet → hero photo → other char sheet → object ref → location ref."""
        f = self._f(
            "char-sheet.png", "char-photo.png",
            "side-sheet.png",
            "obj-sheet.png",
            "loc-sheet.png",
        )
        story = {"cast": [
            {"id": "char",    "name": "Char",    "style_sheet": f["char-sheet.png"],
             "ref_image": [f["char-photo.png"]]},
            {"id": "sidekick","name": "Sidekick","style_sheet": f["side-sheet.png"]},
            {"id": "obj",     "name": "Obj",     "kind": "object",
             "style_sheet": f["obj-sheet.png"]},
            {"id": "loc",     "name": "Loc",     "kind": "location",
             "style_sheet": f["loc-sheet.png"]},
        ]}
        result = render_book.collect_input_images(
            story, {"cast": ["char", "sidekick", "obj", "loc"]}, log=[]
        )
        labels = [l for l, _ in result]
        self.assertEqual(labels, [
            "character style sheet for Char",
            "real photograph of the character Char (facial likeness reference)",
            "character style sheet for Sidekick",
            "object reference sheet for Obj",
            "location reference sheet for Loc",
        ])

    # ------------------------------------------------------------------
    # Object fallback
    # ------------------------------------------------------------------

    def test_object_no_sheet_falls_back_to_photo(self):
        """Object without a style_sheet falls back to its first ref_image photo."""
        f = self._f("obj-photo.png")
        story = {"cast": [
            {"id": "obj", "name": "Obj", "kind": "object",
             "ref_image": [f["obj-photo.png"]]},
        ]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["obj"]}, log=log)
        labels = [l for l, _ in result]
        self.assertEqual(labels, [
            "real photograph of the object Obj (appearance reference)"
        ])

    def test_object_missing_sheet_on_disk_falls_back_to_photo(self):
        """Object's style_sheet path set but file absent: falls back to photo, logs warning."""
        f = self._f("obj-photo.png")
        story = {"cast": [
            {"id": "obj", "name": "Obj", "kind": "object",
             "style_sheet": "/nonexistent/obj.png",
             "ref_image": [f["obj-photo.png"]]},
        ]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["obj"]}, log=log)
        labels = [l for l, _ in result]
        self.assertEqual(labels, [
            "real photograph of the object Obj (appearance reference)"
        ])
        self.assertTrue(any("not found on disk" in w for w in log))

    # ------------------------------------------------------------------
    # Location fallback
    # ------------------------------------------------------------------

    def test_location_no_sheet_falls_back_to_photo(self):
        """Location without a style_sheet falls back to its first ref_image photo."""
        f = self._f("loc-photo.jpg")
        story = {"cast": [
            {"id": "loc", "name": "Loc", "kind": "location",
             "ref_image": [f["loc-photo.jpg"]]},
        ]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["loc"]}, log=log)
        labels = [l for l, _ in result]
        self.assertEqual(labels, [
            "real photograph of the location Loc (setting reference)"
        ])

    # ------------------------------------------------------------------
    # Warning and skip cases
    # ------------------------------------------------------------------

    def test_missing_character_sheet_warns_and_skips(self):
        """Character sheet path set but absent on disk: warning, no candidate."""
        story = {"cast": [
            {"id": "pip", "name": "Pip", "style_sheet": "/nonexistent/sheet.png"},
        ]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["pip"]}, log=log)
        self.assertEqual(result, [])
        self.assertTrue(any("not found on disk" in w for w in log))

    def test_character_no_sheet_no_photo_warns_and_skips(self):
        """Character with neither style_sheet nor ref_image: warning + skipped."""
        story = {"cast": [{"id": "pip", "name": "Pip"}]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["pip"]}, log=log)
        self.assertEqual(result, [])
        self.assertTrue(any("no style_sheet" in w for w in log))

    def test_unknown_cast_id_warns_and_skips(self):
        """Page references an id not present in story.cast: warning + skipped."""
        story = {"cast": [{"id": "pip", "name": "Pip"}]}
        log = []
        result = render_book.collect_input_images(story, {"cast": ["ghost"]}, log=log)
        self.assertEqual(result, [])
        self.assertTrue(any("unknown cast id" in w for w in log))

    def test_empty_page_cast(self):
        """Page with no cast: empty candidate list, no warnings."""
        story = {"cast": [{"id": "pip", "name": "Pip", "style_sheet": "/fake/s.png"}]}
        log = []
        result = render_book.collect_input_images(story, {"cast": []}, log=log)
        self.assertEqual(result, [])
        self.assertEqual(log, [])

    def test_warnings_go_to_log_not_stderr(self):
        """When log is provided, warnings append there (not to sys.stderr)."""
        import contextlib, io
        story = {"cast": [{"id": "pip", "name": "Pip"}]}  # no sheet, no photo
        log: list[str] = []
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            render_book.collect_input_images(story, {"cast": ["pip"]}, log=log)
        self.assertEqual(buf.getvalue(), "", "No output to stderr when log is provided")
        self.assertGreater(len(log), 0, "Warning must appear in log list")


class TestBaseDirResolution(unittest.TestCase):
    """Relative `style_sheet`/`ref_image` paths resolve against base_dir (the
    story.json dir), not cwd — PER-68 regression guard."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Files created with story-relative names under root.
        _touch(self.root / "sheet.png")
        _touch(self.root / "refs" / "photo.png")
        _touch(self.root / "loc-sheet.png")

    def tearDown(self):
        self._tmp.cleanup()

    def _story(self):
        return {"cast": [
            {"id": "pip", "name": "Pip",
             "style_sheet": "sheet.png",            # relative to story dir
             "ref_image": ["refs/photo.png"]},      # relative to story dir
            {"id": "loc", "name": "Loc", "kind": "location",
             "style_sheet": "loc-sheet.png"},
        ]}

    def test_relative_paths_resolved_against_base_dir(self):
        """base_dir = story dir → relative sheet + photo + location sheet all found,
        and the returned paths are the resolved absolute paths."""
        log = []
        result = render_book.collect_input_images(
            self._story(), {"cast": ["pip", "loc"]}, log=log, base_dir=self.root,
        )
        labels = [l for l, _ in result]
        paths = [p for _, p in result]
        self.assertEqual(labels, [
            "character style sheet for Pip",
            "real photograph of the character Pip (facial likeness reference)",
            "location reference sheet for Loc",
        ])
        self.assertEqual(paths, [
            str(self.root / "sheet.png"),
            str(self.root / "refs" / "photo.png"),
            str(self.root / "loc-sheet.png"),
        ])
        self.assertEqual(log, [], "No warnings when everything resolves")

    def test_relative_paths_dropped_with_wrong_base_dir(self):
        """base_dir = some other dir → relative paths don't resolve; the exact bug
        (sheet not found, photo skipped, location skipped)."""
        other = Path(self._tmp.name) / "elsewhere"
        other.mkdir()
        log = []
        result = render_book.collect_input_images(
            self._story(), {"cast": ["pip", "loc"]}, log=log, base_dir=other,
        )
        self.assertEqual(result, [])
        self.assertTrue(any("not found on disk" in w for w in log))

    def test_absolute_paths_unaffected_by_base_dir(self):
        """Absolute paths pass through resolve_story_rel unchanged regardless of base_dir."""
        abs_sheet = _touch(self.root / "abs-sheet.png")
        story = {"cast": [{"id": "pip", "name": "Pip", "style_sheet": abs_sheet}]}
        result = render_book.collect_input_images(
            story, {"cast": ["pip"]}, log=[], base_dir=Path("/nonexistent/base"),
        )
        self.assertEqual(result, [("character style sheet for Pip", abs_sheet)])


if __name__ == "__main__":
    unittest.main()
