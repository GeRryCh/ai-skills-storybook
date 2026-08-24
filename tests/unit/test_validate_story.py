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


class TestCharacterLaneHardCap(unittest.TestCase):
    """PER-97: pages[].cast character-kind count > 5 is a hard error.

    5 is legal (a 5th character silently auto-upgrades the page to pro at
    render — render_book.py's select_refs); 6 is the first count where a
    sheet is guaranteed to be dropped. The check is model-independent: it
    must never mention flash/pro, since 5 is fine on either.
    """

    @staticmethod
    def _char_entry(cid: str) -> dict:
        return {"id": cid, "name": cid.title(), "appearance": "a small creature"}

    @staticmethod
    def _kind_entry(cid: str, kind: str) -> dict:
        return {"id": cid, "name": cid.title(), "appearance": "a thing", "kind": kind}

    def _story_with_cast(self, extra_cast: list) -> dict:
        story = copy.deepcopy(PIP_STORM_STORY)
        story["cast"].extend(extra_cast)
        return story

    def test_six_characters_is_error(self):
        extra = [self._char_entry(f"char{i}") for i in range(5)]  # + pip = 6
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("character-kind entries" in e and "hard cap is 5" in e for e in errors),
            f"Expected character-lane hard-cap error, got: {errors}",
        )
        # Names the overflow entries (char4 is the 6th mention, index 5, first dropped).
        self.assertTrue(any("char4" in e for e in errors), errors)

    def test_five_characters_no_error(self):
        extra = [self._char_entry(f"char{i}") for i in range(4)]  # + pip = 5
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertFalse(
            any("character-kind entries" in e for e in errors),
            f"5 characters must be legal, got: {errors}",
        )

    def test_five_characters_flash_model_still_no_error(self):
        """The cap is model-independent — pinning the page to flash must not
        change the outcome, and the error text must never mention either model."""
        extra = [self._char_entry(f"char{i}") for i in range(4)]
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        story["pages"][0]["model"] = "gemini-3.1-flash-image"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertFalse(any("character-kind entries" in e for e in errors), errors)

    def test_error_never_mentions_model(self):
        extra = [self._char_entry(f"char{i}") for i in range(5)]  # 6 total
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        cap_errors = [e for e in errors if "character-kind entries" in e]
        self.assertTrue(cap_errors)
        for e in cap_errors:
            self.assertNotIn("flash", e.lower())
            self.assertNotIn("gemini-3-pro", e.lower())

    def test_five_characters_plus_eight_objects_no_character_error(self):
        """Object/location count never counts toward the character-lane cap."""
        chars = [self._char_entry(f"char{i}") for i in range(4)]  # + pip = 5
        objs = [self._kind_entry(f"obj{i}", "object") for i in range(8)]
        story = self._story_with_cast(chars + objs)
        cast_ids = ["pip"] + [c["id"] for c in chars] + [o["id"] for o in objs]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertFalse(any("character-kind entries" in e for e in errors), errors)

    def test_kind_absent_counts_as_character(self):
        """No 'kind' field defaults to character (same default as render_book.py)."""
        extra = [self._char_entry(f"char{i}") for i in range(5)]  # kind omitted → 6 chars
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(any("character-kind entries" in e for e in errors), errors)

    def test_location_kind_does_not_count(self):
        """kind: 'location' entries never count toward the character lane."""
        chars = [self._char_entry(f"char{i}") for i in range(4)]  # + pip = 5
        locs = [self._kind_entry(f"loc{i}", "location") for i in range(3)]
        story = self._story_with_cast(chars + locs)
        cast_ids = ["pip"] + [c["id"] for c in chars] + [l["id"] for l in locs]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids)
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertFalse(any("character-kind entries" in e for e in errors), errors)

    def test_unknown_page_cast_id_does_not_crash_counter(self):
        """An unknown id alongside 6 real characters: membership error fires,
        counter skips the unknown id, and the character-lane error still fires
        for the 6 real characters (it doesn't crash or get silently skipped)."""
        extra = [self._char_entry(f"char{i}") for i in range(5)]  # + pip = 6
        story = self._story_with_cast(extra)
        cast_ids = ["pip"] + [c["id"] for c in extra] + ["ghost"]
        story["pages"][0]["cast"] = cast_ids
        story["pages"][0]["image_prompt"] = " ".join(f"<{c}>" for c in cast_ids[:-1])
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(any("not in the cast" in e for e in errors), errors)
        self.assertTrue(any("character-kind entries" in e for e in errors), errors)


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


class TestSceneTextAndPersistentDetails(unittest.TestCase):
    """PER-87 schema additions: scene_text (top-level + per-page enum) and
    cast[].persistent_details (string). Bad enum/type -> error; valid -> clean."""

    def test_valid_top_level_scene_text_no_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["scene_text"] = "allow"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertEqual(errors, [])

    def test_bad_top_level_scene_text_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["scene_text"] = "sometimes"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("scene_text" in e for e in errors), f"Expected scene_text error, got: {errors}"
        )

    def test_valid_page_scene_text_no_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["scene_text"] = "suppress"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertEqual(errors, [])

    def test_bad_page_scene_text_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["scene_text"] = "maybe"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("scene_text" in e for e in errors), f"Expected scene_text error, got: {errors}"
        )

    def test_valid_persistent_details_no_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["cast"][0]["persistent_details"] = "a small red satchel"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertEqual(errors, [])

    def test_non_string_persistent_details_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["cast"][0]["persistent_details"] = 123
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("persistent_details" in e for e in errors),
            f"Expected persistent_details type error, got: {errors}",
        )

    def test_appearance_echo_warning_points_at_persistent_details(self):
        """PER-87: the PER-42 echo warning now names persistent_details as the
        sanctioned channel, so the warning and the fix stop pointing opposite ways."""
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["image_prompt"] = "A small brave hedgehog stands in the rain."
        story["pages"][0]["cast"] = ["pip"]
        _, warnings = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any("persistent_details" in w for w in warnings),
            f"Expected warning to mention persistent_details, got: {warnings}",
        )


class TestTextAlignRight(unittest.TestCase):
    """PER-100: 'right' is a legal pages[].text_align value alongside left/center."""

    def test_right_align_no_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["text_align"] = "right"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertEqual(errors, [])

    def test_bogus_align_is_error_listing_three_values(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["text_align"] = "justify"
        errors, _ = edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)
        self.assertTrue(
            any(
                "text_align" in e and "justify" in e
                and all(v in e for v in ("left", "center", "right"))
                for e in errors
            ),
            f"Expected text_align enum error naming all three values, got: {errors}",
        )


class TestLayoutValidation(unittest.TestCase):
    """PER-104: top-level 'layout' object and per-page 'font_size' override."""

    def _validate(self, story):
        return edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)

    def test_valid_layout_no_errors(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {
            "reference_size": 2048,
            "font_size": 48,
            "min_font_size": 22,
            "padding": {"h": 40, "v": 48},
            "max_panel_fraction": 0.9,
            "radius": 36,
            "feather": 14,
        }
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_layout_not_object_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = "not an object"
        errors, _ = self._validate(story)
        self.assertTrue(any("layout" in e and "object" in e for e in errors))

    def test_layout_font_size_wrong_type_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"font_size": "big"}
        errors, _ = self._validate(story)
        self.assertTrue(any("layout.font_size" in e for e in errors))

    def test_layout_font_size_zero_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"font_size": 0}
        errors, _ = self._validate(story)
        self.assertTrue(any("layout.font_size" in e for e in errors))

    def test_max_panel_fraction_out_of_range_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"max_panel_fraction": 1.5}
        errors, _ = self._validate(story)
        self.assertTrue(any("max_panel_fraction" in e for e in errors))

    def test_max_panel_fraction_zero_is_error(self):
        """exclusiveMinimum: 0 — a panel that may cover none of the page makes no sense."""
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"max_panel_fraction": 0}
        errors, _ = self._validate(story)
        self.assertTrue(any("max_panel_fraction" in e for e in errors))

    def test_padding_negative_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"padding": {"h": -5}}
        errors, _ = self._validate(story)
        self.assertTrue(any("layout.padding.h" in e for e in errors))

    def test_padding_not_object_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"padding": "wide"}
        errors, _ = self._validate(story)
        self.assertTrue(any("layout.padding" in e for e in errors))

    def test_unknown_layout_key_is_warning_not_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"font_size": 48, "line_height": 99}
        errors, warnings = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertTrue(any("line_height" in w for w in warnings))

    def test_unknown_layout_padding_key_is_warning_not_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["layout"] = {"padding": {"h": 40, "diagonal": 5}}
        errors, warnings = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertTrue(any("diagonal" in w for w in warnings))

    def test_page_font_size_valid_no_errors(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["font_size"] = 72
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_page_font_size_wrong_type_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["font_size"] = "large"
        errors, _ = self._validate(story)
        self.assertTrue(any("font_size" in e for e in errors))

    def test_page_font_size_zero_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["font_size"] = 0
        errors, _ = self._validate(story)
        self.assertTrue(any("font_size" in e for e in errors))

    def test_page_font_size_absent_no_errors(self):
        """Absent per-page font_size must never be materialised or required —
        it means 'inherit the book value'."""
        story = copy.deepcopy(PIP_STORM_STORY)
        self.assertNotIn("font_size", story["pages"][0])
        errors, _ = self._validate(story)
        self.assertEqual(errors, [])

    def test_text_align_right_is_valid(self):
        """PER-100: 'right' joins the text_align enum."""
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["text_align"] = "right"
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")


class TestBorderValidation(unittest.TestCase):
    """PER-105: top-level 'border' object and per-page 'border': 'none' opt-out."""

    def _validate(self, story):
        return edit_story.validate_story(story, PIP_STORM_DIR, SCHEMA)

    def test_valid_border_no_errors(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {
            "width": 64,
            "color": "#FFEDC7",
            "radius": 48,
            "shadow": {"offset": 12, "blur": 24, "opacity": 0.25},
        }
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_border_not_object_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = "thick"
        errors, _ = self._validate(story)
        self.assertTrue(any("border" in e and "object" in e for e in errors))

    def test_border_width_negative_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"width": -1}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.width" in e for e in errors))

    def test_border_width_wrong_type_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"width": "wide"}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.width" in e for e in errors))

    def test_border_radius_zero_is_valid(self):
        # Unlike layout.font_size, a zero radius (square corners) is legitimate.
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"radius": 0}
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_border_color_bad_hex_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"color": "cream"}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.color" in e for e in errors))

    def test_border_color_short_hex_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"color": "#FFF"}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.color" in e for e in errors))

    def test_border_color_valid_hex_no_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"color": "#FFEDC7"}
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_shadow_not_object_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"shadow": "soft"}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.shadow" in e and "object" in e for e in errors))

    def test_shadow_opacity_out_of_range_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"shadow": {"opacity": 1.5}}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.shadow.opacity" in e for e in errors))

    def test_shadow_offset_negative_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"shadow": {"offset": -1}}
        errors, _ = self._validate(story)
        self.assertTrue(any("border.shadow.offset" in e for e in errors))

    def test_unknown_border_key_is_warning_not_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"width": 64, "opacity": 0.5}
        errors, warnings = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertTrue(any("opacity" in w for w in warnings))

    def test_unknown_shadow_key_is_warning_not_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"shadow": {"offset": 12, "spread": 4}}
        errors, warnings = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertTrue(any("spread" in w for w in warnings))

    def test_border_absent_no_errors(self):
        """Absent 'border' must never be required — it means full-bleed."""
        story = copy.deepcopy(PIP_STORM_STORY)
        self.assertNotIn("border", story)
        errors, _ = self._validate(story)
        self.assertEqual(errors, [])

    def test_page_border_none_is_valid(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["border"] = {"width": 64}
        story["pages"][0]["border"] = "none"
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_page_border_none_valid_even_with_no_book_border(self):
        """A harmless no-op, not a misconfiguration — the book has nothing to opt out of."""
        story = copy.deepcopy(PIP_STORM_STORY)
        self.assertNotIn("border", story)
        story["pages"][0]["border"] = "none"
        errors, _ = self._validate(story)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_page_border_bogus_value_is_error(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        story["pages"][0]["border"] = "thick"
        errors, _ = self._validate(story)
        self.assertTrue(any("border" in e and "thick" in e for e in errors))

    def test_page_border_absent_no_errors(self):
        story = copy.deepcopy(PIP_STORM_STORY)
        self.assertNotIn("border", story["pages"][0])
        errors, _ = self._validate(story)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
