"""Unit tests for edit_story.validate_story and _appearance_echo.

validate_story(story, story_dir, schema) -> (errors, warnings)
_appearance_echo(appearance, prompt) -> str (PER-42 heuristic)

All tests are in-process and need no API key.
"""
import copy
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support

import edit_story

# Load once; schema reads assets/story_schema.json from disk (stdlib, no API).
SCHEMA = edit_story.load_schema()

PIP_STORM_STORY = _support.fixture_story("pip-storm")
PIP_STORM_DIR = _support.fixture_dir("pip-storm")


class TestValidateStoryClean(unittest.TestCase):
    """Committed pip-storm fixture validates with no errors."""

    def test_pip_storm_no_errors(self):
        errors, warnings = edit_story.validate_story(
            copy.deepcopy(PIP_STORM_STORY), PIP_STORM_DIR, SCHEMA
        )
        self.assertEqual(errors, [], f"Unexpected errors in pip-storm: {errors}")

    def test_root_not_dict_error(self):
        errors, _ = edit_story.validate_story("not a dict", PIP_STORM_DIR, SCHEMA)
        self.assertTrue(any("JSON object" in e for e in errors))

    def test_legacy_characters_key_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["characters"] = []
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(any("characters" in e and "cast" in e for e in errors))


class TestPlaceholderValidation(unittest.TestCase):
    """<id> placeholder validation in image_prompt — error vs warning."""

    def _validate(self, prompt: str, page_cast: list = None) -> tuple:
        story = copy.deepcopy(PIP_STORM_STORY)
        if page_cast is not None:
            story["pages"][0]["cast"] = page_cast
        story["pages"][0]["image_prompt"] = prompt
        return edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)

    def test_known_id_in_page_cast_no_issue(self):
        """Valid <pip> in cast and in prompt: no placeholder error or warning."""
        errors, warnings = self._validate("<pip> runs", page_cast=["pip"])
        self.assertEqual(errors, [])
        placeholder_issues = [
            m for m in warnings + errors
            if "image_prompt" in m and ("cast id" in m or "not in this page" in m)
        ]
        self.assertEqual(placeholder_issues, [])

    def test_unknown_placeholder_is_error(self):
        """<ghost> not in story.cast → ERROR."""
        errors, _ = self._validate("<ghost> appears", page_cast=["pip"])
        self.assertTrue(
            any("ghost" in e and "not a known cast id" in e for e in errors),
            f"Expected unknown-id error but got: {errors}",
        )

    def test_valid_id_not_in_page_cast_is_warning(self):
        """<major-oak> is in story.cast but not on this page → WARNING (not error)."""
        errors, warnings = self._validate(
            "<pip> shelters under <major-oak>",
            page_cast=["pip"],  # major-oak deliberately absent from this page
        )
        self.assertFalse(
            any("major-oak" in e and "not a known cast id" in e for e in errors),
            "major-oak is a valid id; must not be an error",
        )
        self.assertTrue(
            any("major-oak" in w and "not in this page" in w for w in warnings),
            f"Expected not-in-page warning but got: {warnings}",
        )


class TestAppearanceEcho(unittest.TestCase):
    """_appearance_echo (PER-42) — trigram heuristic, direct unit tests."""

    def _echo(self, appearance: str, prompt: str) -> str:
        return edit_story._appearance_echo(appearance, prompt)

    def test_trigram_match_returns_first_match(self):
        result = self._echo(
            "small brave hedgehog, round body, soft brown spines",
            "A small brave hedgehog runs through the rain.",
        )
        self.assertEqual(result, "small brave hedgehog")

    def test_no_match_returns_empty(self):
        result = self._echo("small brave hedgehog", "the storm is coming")
        self.assertEqual(result, "")

    def test_appearance_shorter_than_3_words_returns_empty(self):
        self.assertEqual(self._echo("hedgehog", "a hedgehog runs"), "")
        self.assertEqual(self._echo("brave hedgehog", "brave hedgehog runs"), "")

    def test_case_insensitive_match(self):
        result = self._echo("SMALL BRAVE HEDGEHOG extra", "small brave hedgehog appears")
        self.assertEqual(result, "small brave hedgehog")

    def test_punctuation_stripped(self):
        result = self._echo("soft, brown spines, big eyes", "soft brown spines visible here")
        self.assertEqual(result, "soft brown spines")

    def test_appearance_echo_warns_in_validate_story(self):
        """Echoing pip's 3-word appearance trigram emits validate_story warning."""
        story = copy.deepcopy(PIP_STORM_STORY)
        # pip.appearance starts with "small brave hedgehog"
        story["pages"][0]["image_prompt"] = "A small brave hedgehog stands in the rain."
        story["pages"][0]["cast"] = ["pip"]
        _, warnings = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("repeats appearance" in w and "Pip" in w for w in warnings),
            f"Expected appearance-echo warning, got: {warnings}",
        )

    def test_location_kind_excluded_from_echo_check(self):
        """Locations are excluded from the appearance-echo check (only char/object)."""
        story = copy.deepcopy(PIP_STORM_STORY)
        # major-oak.appearance starts with "ancient oak tree"
        # Deliberately echo those words in a page where major-oak is in cast
        story["pages"][3]["image_prompt"] = (
            "<pip> shelters under ancient oak tree roots."
        )
        story["pages"][3]["cast"] = ["pip", "major-oak"]
        _, warnings = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        echo_warnings = [
            w for w in warnings
            if "repeats appearance" in w and "Major Oak" in w
        ]
        self.assertEqual(echo_warnings, [], "Location kind must not trigger appearance echo")


if __name__ == "__main__":
    unittest.main()
