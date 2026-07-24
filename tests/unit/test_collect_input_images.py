"""Unit tests for render_book.collect_input_images — priority order, disk gating, fallbacks.

collect_input_images reads disk via Path(...).exists() for every sheet/photo.
Tests use real empty temp files (only existence is checked — no real PNG needed).
Pass a log list to capture warnings without polluting stderr.

PER-83: results are (label, path, lane) triples, lane in {"character", "object"}
(locations share the object lane with objects, but rank above them — a wrong-style
background poisons the whole frame, a slightly-off prop does not).
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

    def test_hero_sheet_only_no_photo(self):
        """Hero with a ref_image photo: sheet only — photo is NOT included (PER-65)."""
        f = self._f("hero-sheet.png", "hero-photo.png")
        story = {"cast": [
            {"id": "pip", "name": "Pip",
             "style_sheet": f["hero-sheet.png"],
             "ref_image": [f["hero-photo.png"]]},
        ]}
        result = render_book.collect_input_images(story, {"cast": ["pip"]}, log=[])
        labels = [l for l, _, _ in result]
        self.assertEqual(labels, ["character style sheet for Pip"])
        self.assertNotIn(
            "real photograph of the character Pip (facial likeness reference)", labels
        )

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
        labels = [l for l, _, _ in result]
        self.assertIn("character style sheet for Sidekick", labels)
        self.assertNotIn(
            "real photograph of the character Sidekick (facial likeness reference)", labels
        )

    # ------------------------------------------------------------------
    # Priority order across kinds
    # ------------------------------------------------------------------

    def test_full_priority_order(self):
        """hero sheet → other char sheet → location ref → object ref (no hero
        photo). PER-83: location outranks object within the shared object lane —
        a wrong-style background poisons the whole frame, a slightly-off prop
        does not."""
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
        labels = [l for l, _, _ in result]
        lanes = [lane for _, _, lane in result]
        self.assertEqual(labels, [
            "character style sheet for Char",
            "character style sheet for Sidekick",
            "location reference sheet for Loc",
            "object reference sheet for Obj",
        ])
        self.assertEqual(lanes, ["character", "character", "object", "object"])

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
        labels = [l for l, _, _ in result]
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
        labels = [l for l, _, _ in result]
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
        labels = [l for l, _, _ in result]
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
        """base_dir = story dir → relative sheet + location sheet all found,
        and the returned paths are the resolved absolute paths. Character photo
        not included (PER-65 — characters are sheet-only at render time)."""
        log = []
        result = render_book.collect_input_images(
            self._story(), {"cast": ["pip", "loc"]}, log=log, base_dir=self.root,
        )
        labels = [l for l, _, _ in result]
        paths = [p for _, p, _ in result]
        self.assertEqual(labels, [
            "character style sheet for Pip",
            "location reference sheet for Loc",
        ])
        self.assertEqual(paths, [
            str(self.root / "sheet.png"),
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
        self.assertEqual(result, [("character style sheet for Pip", abs_sheet, "character")])


class TestMissingCharacterSheets(unittest.TestCase):
    """render_book.missing_character_sheets — PER-69 hard gate: characters need a
    sheet (no fallback); objects/locations are exempt (photo fallback)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_present_sheet_not_missing(self):
        sheet = _touch(self.root / "pip.png")
        story = {"cast": [{"id": "pip", "name": "Pip", "style_sheet": sheet}]}
        self.assertEqual(
            render_book.missing_character_sheets(story, {"cast": ["pip"]}), [])

    def test_no_style_sheet_field_is_missing(self):
        story = {"cast": [{"id": "pip", "name": "Pip"}]}
        result = render_book.missing_character_sheets(story, {"cast": ["pip"]})
        self.assertEqual(result, [("pip", "Pip", "")])

    def test_sheet_path_set_but_absent_is_missing(self):
        story = {"cast": [{"id": "pip", "name": "Pip", "style_sheet": "/nope/x.png"}]}
        result = render_book.missing_character_sheets(story, {"cast": ["pip"]})
        self.assertEqual(result, [("pip", "Pip", "/nope/x.png")])

    def test_object_and_location_exempt(self):
        """Objects/locations with no sheet are NOT flagged — they keep photo fallback."""
        story = {"cast": [
            {"id": "ball", "name": "Ball", "kind": "object"},
            {"id": "valley", "name": "Valley", "kind": "location"},
        ]}
        self.assertEqual(
            render_book.missing_character_sheets(story, {"cast": ["ball", "valley"]}), [])

    def test_relative_sheet_resolves_against_base_dir(self):
        _touch(self.root / "pip.png")
        story = {"cast": [{"id": "pip", "name": "Pip", "style_sheet": "pip.png"}]}
        # Correct base dir → not missing.
        self.assertEqual(
            render_book.missing_character_sheets(
                story, {"cast": ["pip"]}, base_dir=self.root), [])
        # Wrong base dir → missing (relative path doesn't resolve).
        self.assertEqual(
            render_book.missing_character_sheets(
                story, {"cast": ["pip"]}, base_dir=self.root / "elsewhere"),
            [("pip", "Pip", "pip.png")])

    def test_dedup_and_unknown_id(self):
        story = {"cast": [{"id": "pip", "name": "Pip"}]}
        # Same char listed twice → reported once; unknown id ignored.
        result = render_book.missing_character_sheets(
            story, {"cast": ["pip", "pip", "ghost"]})
        self.assertEqual(result, [("pip", "Pip", "")])

    def test_empty_cast_passes(self):
        """Shared text-bg call uses cast=[] → nothing required."""
        story = {"cast": [{"id": "pip", "name": "Pip"}]}
        self.assertEqual(render_book.missing_character_sheets(story, {"cast": []}), [])


if __name__ == "__main__":
    unittest.main()
