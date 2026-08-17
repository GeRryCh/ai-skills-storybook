"""Unit tests for make_style_sheet.build_sheet_prompt (PER-88 age fidelity).

Eva and the Box from Baku shipped with a style sheet that rendered the stated
"about five years old" character as roughly age 3 — reference photos spanning
a range of ages plus the soft-watercolor idiom both bias young by default, and
the prior prompt stated the description without ever telling the model that
age is a property to actively match (the same problem shape the outfit lock
already solves for clothing). This asserts the character branch's prompt now
carries an explicit, unconditional age/proportion-fidelity clause, plus a
second clause (only when has_refs=True) that the description's stated age
outranks what any reference photo shows.

Zero-API rule: no test may call Gemini or OpenAI. build_sheet_prompt is pure
string assembly — no network, no disk.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import make_style_sheet

_STORY = {
    "style_guide": {"medium": "soft watercolor with thin pen-and-ink outline"},
}


def _character_entry(appearance: str) -> dict:
    return {
        "id": "eva",
        "name": "Eva",
        "kind": "character",
        "appearance": appearance,
    }


class TestCharacterSheetAgeFidelity(unittest.TestCase):
    """Character branch of build_sheet_prompt must instruct age/proportion matching."""

    def test_unconditional_age_clause_present_without_refs(self):
        """The age-fidelity clause must fire even with no reference photos —
        it's about reading the stated description, not about photos."""
        entry = _character_entry("a girl about five years old, curly brown hair")
        prompt = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=False)
        self.assertIn("Age and body proportions", prompt)
        self.assertIn("never younger", prompt)
        self.assertIn("head-to-body proportions", prompt)

    def test_unconditional_age_clause_present_with_refs(self):
        entry = _character_entry("a girl about five years old, curly brown hair")
        prompt = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=True)
        self.assertIn("Age and body proportions", prompt)

    def test_photo_age_conflict_clause_only_with_refs(self):
        """The 'description wins over photos' sentence is inside the has_refs
        branch — it has nothing to say when there are no photos to conflict with."""
        entry = _character_entry("a girl about five years old")
        with_refs = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=True)
        without_refs = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=False)
        self.assertIn("different ages", with_refs)
        self.assertNotIn("different ages", without_refs)

    def test_object_branch_has_no_age_clause(self):
        """Age fidelity is a character-only concern — objects/locations don't age."""
        entry = {"id": "kite", "name": "Kite", "kind": "object", "appearance": "a red kite"}
        prompt = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=False)
        self.assertNotIn("Age and body proportions", prompt)

    def test_location_branch_has_no_age_clause(self):
        entry = {"id": "park", "name": "Park", "kind": "location", "appearance": "a green park"}
        prompt = make_style_sheet.build_sheet_prompt(_STORY, entry, has_refs=False)
        self.assertNotIn("Age and body proportions", prompt)


if __name__ == "__main__":
    unittest.main()
