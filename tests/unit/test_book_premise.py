"""Unit tests for PER-66: book_premise() helper and premise injection in build_image_prompt.

Zero API calls. Verifies:
  - book_premise() strips whitespace; missing/empty/whitespace-only → ""
  - premise is injected immediately after STYLE_ANCHOR (not end-of-prompt)
  - in native mode the lettering directive stays last (premise doesn't follow it)
  - absent/empty premise → output byte-identical to no-premise (back-compat)
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import render_book

# Minimal story that satisfies build_style_block (requires at least one truthy style_guide key)
_STORY_BASE = {
    "style_guide": {"medium": "soft watercolor"},
    "cast": [{"id": "pip", "name": "Pip"}],
}

_PAGE = {
    "image_prompt": "<pip> runs through rain",
    "text": "Pip ran fast.",
    "text_placement": "bottom",
}

_PREMISE = "A gentle picture book for ages 3-5; warm, calm tone; soft golden-hour light."


def _story_with_premise() -> dict:
    return {**_STORY_BASE, "premise": _PREMISE}


def _story_without_premise() -> dict:
    return dict(_STORY_BASE)


def _anchor(story: dict) -> str:
    """Compute the raw (pre-premise) STYLE_ANCHOR string for this story."""
    return render_book.STYLE_ANCHOR.format(style=render_book.build_style_block(story))


class TestBookPremise(unittest.TestCase):
    """render_book.book_premise() — extraction and edge cases."""

    def test_returns_premise(self):
        self.assertEqual(render_book.book_premise(_story_with_premise()), _PREMISE)

    def test_strips_whitespace(self):
        story = {**_STORY_BASE, "premise": f"  {_PREMISE}  "}
        self.assertEqual(render_book.book_premise(story), _PREMISE)

    def test_missing_key_returns_empty(self):
        self.assertEqual(render_book.book_premise(_story_without_premise()), "")

    def test_none_value_returns_empty(self):
        story = {**_STORY_BASE, "premise": None}
        self.assertEqual(render_book.book_premise(story), "")

    def test_empty_string_returns_empty(self):
        story = {**_STORY_BASE, "premise": ""}
        self.assertEqual(render_book.book_premise(story), "")

    def test_whitespace_only_returns_empty(self):
        story = {**_STORY_BASE, "premise": "   "}
        self.assertEqual(render_book.book_premise(story), "")


class TestBuildImagePromptPremiseInjection(unittest.TestCase):
    """build_image_prompt() — premise injected immediately after STYLE_ANCHOR."""

    def _out(self, mode: str, story: dict = None) -> str:
        if story is None:
            story = _story_with_premise()
        return render_book.build_image_prompt(_PAGE, story, mode)

    def _anchor_with_premise(self, story: dict = None) -> str:
        if story is None:
            story = _story_with_premise()
        return f"{_anchor(story)}. {_PREMISE}"

    # ------------------------------------------------------------------
    # Premise placement: must appear immediately after STYLE_ANCHOR
    # ------------------------------------------------------------------

    def test_overlay_premise_after_anchor(self):
        out = self._out("overlay")
        self.assertIn(self._anchor_with_premise(), out,
                      "premise must sit immediately after STYLE_ANCHOR in overlay mode")

    def test_native_premise_after_anchor(self):
        out = self._out("native")
        self.assertIn(self._anchor_with_premise(), out,
                      "premise must sit immediately after STYLE_ANCHOR in native mode")

    def test_long_premise_after_anchor(self):
        out = self._out("long")
        self.assertIn(self._anchor_with_premise(), out,
                      "premise must sit immediately after STYLE_ANCHOR in long mode")

    # ------------------------------------------------------------------
    # Native mode: NATIVE_TEXT_DIRECTIVE must remain the final instruction
    # (premise must NOT appear after it — would risk model lettering premise)
    # ------------------------------------------------------------------

    def test_native_premise_not_at_end(self):
        out = self._out("native").rstrip()
        self.assertFalse(out.endswith(_PREMISE),
                         "in native mode the lettering directive must stay last, not the premise")

    # ------------------------------------------------------------------
    # Back-compat: absent/empty premise → byte-identical output
    # ------------------------------------------------------------------

    def test_absent_premise_no_change_overlay(self):
        with_story = _story_with_premise()
        without_story = _story_without_premise()
        self.assertNotEqual(
            render_book.build_image_prompt(_PAGE, with_story, "overlay"),
            render_book.build_image_prompt(_PAGE, without_story, "overlay"),
            "sanity: outputs must differ when premise present vs absent",
        )

    def test_empty_premise_identical_to_absent(self):
        absent_story = _story_without_premise()
        empty_story = {**_STORY_BASE, "premise": ""}
        self.assertEqual(
            render_book.build_image_prompt(_PAGE, absent_story, "overlay"),
            render_book.build_image_prompt(_PAGE, empty_story, "overlay"),
            "empty premise must produce same output as absent premise",
        )

    def test_whitespace_only_premise_identical_to_absent(self):
        absent_story = _story_without_premise()
        ws_story = {**_STORY_BASE, "premise": "  \n  "}
        for mode in ("overlay", "native", "long"):
            self.assertEqual(
                render_book.build_image_prompt(_PAGE, absent_story, mode),
                render_book.build_image_prompt(_PAGE, ws_story, mode),
                f"whitespace-only premise must produce same output as absent ({mode})",
            )

    # ------------------------------------------------------------------
    # Premise verbatim: content appears unchanged
    # ------------------------------------------------------------------

    def test_premise_content_verbatim(self):
        for mode in ("overlay", "native", "long"):
            out = self._out(mode)
            self.assertIn(_PREMISE, out,
                          f"premise text must appear verbatim in {mode} mode")


if __name__ == "__main__":
    unittest.main()
