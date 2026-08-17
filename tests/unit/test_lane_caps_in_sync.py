"""PER-97: guard the triplicated reference-lane-cap constants against drift.

The lane caps (character lane 4 flash / 5 pro, object lane 10 flash / 6 pro
(PER-96), 14 total) exist in three places:
  - render_book.py — the source of truth (CHARACTER_LANE_CAP, OBJECT_LANE_CAP,
    MAX_CHARACTER_LANE, MAX_OBJECT_LANE, TOTAL_REF_CAP)
  - edit_story.py — CHARACTER_LANE_HARD_CAP, the validator's hard-block cap
    (mirrors CHARACTER_LANE_CAP[PRO_IMAGE_MODEL])
  - editor.html — a JS mirror of the same constants for the client-side badge
    and the @-mention picker guard. Not importable (it's a static asset the
    browser loads), so this is the one constant this repo's test suite reads
    out of source text rather than importing — every other cross-file drift
    guard (test_costs.py::TestPricingTablesInSync) is import-based.

Zero-API rule: no test may call Gemini or OpenAI. Pure source/attribute
comparison only.
"""
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import edit_story
import render_book

EDITOR_HTML = (
    _support.REPO_ROOT / "skills" / "storybook-story" / "assets" / "editor.html"
)


class TestCharacterLaneHardCapMatchesRenderBook(unittest.TestCase):
    """edit_story.py's validator cap must equal render_book's pro character cap."""

    def test_hard_cap_equals_pro_character_lane_cap(self):
        self.assertEqual(
            edit_story.CHARACTER_LANE_HARD_CAP,
            render_book.CHARACTER_LANE_CAP[render_book.PRO_IMAGE_MODEL],
        )


class TestEditorHtmlLaneConstantsInSync(unittest.TestCase):
    """editor.html's JS mirror of the lane-cap constants must match render_book.py.

    Regex-over-source: editor.html is not importable, so each constant is
    scraped from its exact `const NAME = ...;` declaration. Every pattern
    must match — a reformat that changes the declaration shape must fail
    loudly here, not silently pass with a stale cached value.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = EDITOR_HTML.read_text(encoding="utf-8")

    def _find(self, pattern: str) -> re.Match:
        m = re.search(pattern, self.source)
        self.assertIsNotNone(
            m, f"editor.html: expected to find pattern {pattern!r} — has the "
            "declaration shape changed? Update this test's regex to match."
        )
        return m

    def test_flash_image_model_name(self):
        m = self._find(r"const FLASH_IMAGE_MODEL = '([^']+)';")
        self.assertEqual(m.group(1), render_book.FLASH_IMAGE_MODEL)

    def test_pro_image_model_name(self):
        m = self._find(r"const PRO_IMAGE_MODEL = '([^']+)';")
        self.assertEqual(m.group(1), render_book.PRO_IMAGE_MODEL)

    def test_character_lane_cap(self):
        m = self._find(
            r"const CHARACTER_LANE_CAP = \{ \[FLASH_IMAGE_MODEL\]: (\d+), "
            r"\[PRO_IMAGE_MODEL\]: (\d+) \};"
        )
        flash_cap, pro_cap = int(m.group(1)), int(m.group(2))
        self.assertEqual(flash_cap, render_book.CHARACTER_LANE_CAP[render_book.FLASH_IMAGE_MODEL])
        self.assertEqual(pro_cap, render_book.CHARACTER_LANE_CAP[render_book.PRO_IMAGE_MODEL])
        # Also the validator's hard cap — the number the picker/save block enforces.
        self.assertEqual(pro_cap, edit_story.CHARACTER_LANE_HARD_CAP)

    def test_max_character_lane(self):
        m = self._find(r"const MAX_CHARACTER_LANE = (\d+);")
        self.assertEqual(int(m.group(1)), render_book.MAX_CHARACTER_LANE)

    def test_object_lane_cap(self):
        m = self._find(
            r"const OBJECT_LANE_CAP = \{ \[FLASH_IMAGE_MODEL\]: (\d+), "
            r"\[PRO_IMAGE_MODEL\]: (\d+) \};"
        )
        flash_cap, pro_cap = int(m.group(1)), int(m.group(2))
        self.assertEqual(flash_cap, render_book.OBJECT_LANE_CAP[render_book.FLASH_IMAGE_MODEL])
        self.assertEqual(pro_cap, render_book.OBJECT_LANE_CAP[render_book.PRO_IMAGE_MODEL])

    def test_max_object_lane(self):
        m = self._find(r"const MAX_OBJECT_LANE = (\d+);")
        self.assertEqual(int(m.group(1)), render_book.MAX_OBJECT_LANE)

    def test_total_ref_cap(self):
        m = self._find(r"const TOTAL_REF_CAP = (\d+);")
        self.assertEqual(int(m.group(1)), render_book.TOTAL_REF_CAP)

    def test_all_expected_constants_were_found(self):
        """Completeness check: every constant name this test knows about must
        appear exactly once in editor.html. Catches a constant silently
        disappearing (e.g. renamed) without any individual test above
        reporting it as the culprit."""
        expected_names = [
            "FLASH_IMAGE_MODEL", "PRO_IMAGE_MODEL", "CHARACTER_LANE_CAP",
            "MAX_CHARACTER_LANE", "OBJECT_LANE_CAP", "MAX_OBJECT_LANE",
            "TOTAL_REF_CAP",
        ]
        found = [
            name for name in expected_names
            if re.search(rf"const {name} = ", self.source)
        ]
        self.assertEqual(
            found, expected_names,
            f"Expected all of {expected_names} declared as `const NAME = ...` "
            f"in editor.html; found {found}",
        )


if __name__ == "__main__":
    unittest.main()
