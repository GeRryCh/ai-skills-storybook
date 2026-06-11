"""Unit tests for PER-67 OpenAI fallback helpers in render_book.py.

Tests the three pure helpers added for the gpt-image-2 fallback:
  - _is_prohibited_block(response) -> bool
  - aspect_to_size(aspect) -> str
  - get_api_key() -> str | None

Zero-API rule: no test may call Gemini or OpenAI.
"""
import os
import sys
import types as pytypes
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import render_book


# ---------------------------------------------------------------------------
# Helpers to build fake Gemini response objects
# ---------------------------------------------------------------------------

def _fake_response(finish_reason=None, block_reason=None, has_candidates=True):
    """Build a minimal fake Gemini response for testing _is_prohibited_block."""
    resp = pytypes.SimpleNamespace()
    if has_candidates and finish_reason is not None:
        candidate = pytypes.SimpleNamespace(finish_reason=finish_reason)
        resp.candidates = [candidate]
    elif has_candidates:
        # candidate with no finish_reason
        candidate = pytypes.SimpleNamespace(finish_reason=None)
        resp.candidates = [candidate]
    else:
        resp.candidates = []
        if block_reason is not None:
            feedback = pytypes.SimpleNamespace(block_reason=block_reason)
            resp.prompt_feedback = feedback
        else:
            resp.prompt_feedback = None
    return resp


def _enum_like(name: str):
    """Return an object whose .name attribute is the given string, mimicking an enum."""
    obj = pytypes.SimpleNamespace()
    obj.name = name
    return obj


# ---------------------------------------------------------------------------
# Tests: _is_prohibited_block
# ---------------------------------------------------------------------------

class TestIsProhibitedBlock(unittest.TestCase):

    def test_prohibited_content_finish_reason_enum(self):
        """finish_reason enum with name 'PROHIBITED_CONTENT' → True."""
        resp = _fake_response(finish_reason=_enum_like("PROHIBITED_CONTENT"))
        self.assertTrue(render_book._is_prohibited_block(resp))

    def test_prohibited_content_finish_reason_string(self):
        """finish_reason as plain string 'PROHIBITED_CONTENT' → True."""
        resp = _fake_response(finish_reason="PROHIBITED_CONTENT")
        self.assertTrue(render_book._is_prohibited_block(resp))

    def test_stop_finish_reason(self):
        """finish_reason STOP → False (normal completion)."""
        resp = _fake_response(finish_reason=_enum_like("STOP"))
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_safety_finish_reason(self):
        """finish_reason SAFETY → False (different safety category, keeps retry path)."""
        resp = _fake_response(finish_reason=_enum_like("SAFETY"))
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_recitation_finish_reason(self):
        """finish_reason RECITATION → False."""
        resp = _fake_response(finish_reason=_enum_like("RECITATION"))
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_none_finish_reason(self):
        """finish_reason is None (image parts present, no block) → False."""
        resp = _fake_response(finish_reason=None)
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_no_candidates_no_block(self):
        """Empty candidates list with no block_reason → False."""
        resp = _fake_response(has_candidates=False)
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_no_candidates_prohibited_block_reason(self):
        """Empty candidates with prompt_feedback.block_reason=PROHIBITED_CONTENT → True."""
        resp = _fake_response(has_candidates=False, block_reason=_enum_like("PROHIBITED_CONTENT"))
        self.assertTrue(render_book._is_prohibited_block(resp))

    def test_no_candidates_other_block_reason(self):
        """Empty candidates with prompt_feedback.block_reason=SAFETY → False."""
        resp = _fake_response(has_candidates=False, block_reason=_enum_like("SAFETY"))
        self.assertFalse(render_book._is_prohibited_block(resp))

    def test_no_candidates_prohibited_block_string(self):
        """Empty candidates with block_reason as plain string 'PROHIBITED_CONTENT' → True."""
        resp = _fake_response(has_candidates=False, block_reason="PROHIBITED_CONTENT")
        self.assertTrue(render_book._is_prohibited_block(resp))


# ---------------------------------------------------------------------------
# Tests: aspect_to_size
# ---------------------------------------------------------------------------

class TestAspectToSize(unittest.TestCase):

    def test_none_returns_auto(self):
        self.assertEqual(render_book.aspect_to_size(None), "auto")

    def test_empty_string_returns_auto(self):
        self.assertEqual(render_book.aspect_to_size(""), "auto")

    def test_square(self):
        self.assertEqual(render_book.aspect_to_size("1:1"), "1024x1024")

    def test_portrait_ratios(self):
        for ratio in ("2:3", "3:4", "4:5", "9:16"):
            with self.subTest(ratio=ratio):
                self.assertEqual(render_book.aspect_to_size(ratio), "1024x1536")

    def test_landscape_ratios(self):
        for ratio in ("3:2", "4:3", "5:4", "16:9", "21:9"):
            with self.subTest(ratio=ratio):
                self.assertEqual(render_book.aspect_to_size(ratio), "1536x1024")

    def test_unknown_ratio_returns_auto(self):
        self.assertEqual(render_book.aspect_to_size("7:3"), "auto")

    def test_whitespace_stripped(self):
        self.assertEqual(render_book.aspect_to_size("  1:1  "), "1024x1024")


# ---------------------------------------------------------------------------
# Tests: get_api_key
# ---------------------------------------------------------------------------

class TestGetApiKey(unittest.TestCase):

    def setUp(self):
        # Save current env state.
        self._saved = {
            "STORYBOOK_SKILL_OPENAI_API_KEY": os.environ.pop("STORYBOOK_SKILL_OPENAI_API_KEY", None),
            "OPENAI_API_KEY": os.environ.pop("OPENAI_API_KEY", None),
        }

    def tearDown(self):
        # Restore env state so tests don't bleed into each other.
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_keys_returns_none(self):
        self.assertIsNone(render_book.get_api_key())

    def test_openai_key_only(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-openai"
        self.assertEqual(render_book.get_api_key(), "sk-test-openai")

    def test_project_key_only(self):
        os.environ["STORYBOOK_SKILL_OPENAI_API_KEY"] = "sk-test-project"
        self.assertEqual(render_book.get_api_key(), "sk-test-project")

    def test_project_key_wins_over_openai_key(self):
        """STORYBOOK_SKILL_OPENAI_API_KEY takes priority over OPENAI_API_KEY."""
        os.environ["STORYBOOK_SKILL_OPENAI_API_KEY"] = "sk-project-wins"
        os.environ["OPENAI_API_KEY"] = "sk-openai-loses"
        self.assertEqual(render_book.get_api_key(), "sk-project-wins")


if __name__ == "__main__":
    unittest.main()
