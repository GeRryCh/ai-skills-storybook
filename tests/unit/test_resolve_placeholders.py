"""Unit tests for render_book.resolve_cast_placeholders — id→name substitution.

Pure transform (no disk/network/env). Warns to stderr on unknown tokens.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import render_book

_STORY = {
    "cast": [
        {"id": "pip", "name": "Pip"},
        {"id": "major-oak", "name": "Major Oak"},
    ]
}


class TestResolveCastPlaceholders(unittest.TestCase):
    """render_book.resolve_cast_placeholders — substitution and unknown-token handling."""

    def _resolve(self, text: str, story: dict = _STORY) -> str:
        return render_book.resolve_cast_placeholders(text, story)

    # ------------------------------------------------------------------
    # Happy-path substitution
    # ------------------------------------------------------------------

    def test_single_known_token(self):
        self.assertEqual(self._resolve("<pip> runs"), "Pip runs")

    def test_multiple_known_tokens(self):
        self.assertEqual(
            self._resolve("<pip> shelters under <major-oak>"),
            "Pip shelters under Major Oak",
        )

    def test_no_placeholders_unchanged(self):
        self.assertEqual(self._resolve("plain text"), "plain text")

    def test_preserves_surrounding_punctuation(self):
        self.assertEqual(
            self._resolve("Once, <pip> found a storm."),
            "Once, Pip found a storm.",
        )

    def test_repeated_token_substituted_each_time(self):
        result = self._resolve("<pip> and <pip> together")
        self.assertEqual(result, "Pip and Pip together")

    # ------------------------------------------------------------------
    # Fallback: name absent → id used as display name
    # ------------------------------------------------------------------

    def test_name_absent_falls_back_to_id(self):
        story = {"cast": [{"id": "nameless"}]}  # no 'name' key
        result = self._resolve("<nameless> walks", story)
        self.assertEqual(result, "nameless walks")

    # ------------------------------------------------------------------
    # Unknown token: stripped of brackets, warning emitted to stderr
    # ------------------------------------------------------------------

    def test_unknown_token_stripped(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = self._resolve("<ghost> appears")
        self.assertEqual(result, "ghost appears")

    def test_unknown_token_warning_to_stderr(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._resolve("<ghost> appears")
        self.assertIn("ghost", buf.getvalue())

    def test_empty_cast_all_tokens_stripped(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = self._resolve("<pip> walks", {"cast": []})
        self.assertEqual(result, "pip walks")
        self.assertIn("pip", buf.getvalue())

    def test_mixed_known_and_unknown(self):
        """Known token substituted; unknown token stripped and warned."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = self._resolve("<pip> meets <phantom>", _STORY)
        self.assertEqual(result, "Pip meets phantom")
        self.assertIn("phantom", buf.getvalue())
        self.assertNotIn("pip", buf.getvalue(), "No warning for known token 'pip'")


if __name__ == "__main__":
    unittest.main()
