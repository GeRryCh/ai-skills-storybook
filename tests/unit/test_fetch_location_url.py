"""Unit tests for fetch_location.normalize_url — non-ASCII Commons URL encoding.

Pure transform (no network/disk). Regression guard for PER-89: a raw non-ASCII
URL (e.g. an Azerbaijani Commons title) crashed with UnicodeEncodeError because
http.client encodes the request line as ASCII.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import fetch_location


class TestNormalizeUrl(unittest.TestCase):
    """fetch_location.normalize_url — percent-encoding and idempotency."""

    def test_non_ascii_path_is_encoded(self):
        # The exact title from PER-89 (Azerbaijani characters).
        url = (
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Bakı_şəhəri,İçəri_şəhər,_qoşa_qala_divarları.jpg?width=1600"
        )
        result = fetch_location.normalize_url(url)
        # Every byte must now be ASCII-encodable — this is what crashed before the fix.
        result.encode("ascii")
        self.assertIn("Bak%C4%B1", result)
        self.assertIn("%C5%9F%C9%99h%C9%99ri", result)
        self.assertIn("width=1600", result)

    def test_ascii_url_unchanged(self):
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/Tour_Eiffel.jpg?width=1600"
        self.assertEqual(fetch_location.normalize_url(url), url)

    def test_already_encoded_url_is_idempotent(self):
        url = (
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            "Bak%C4%B1_%C5%9F%C9%99h%C9%99ri.jpg?width=1600"
        )
        result = fetch_location.normalize_url(url)
        self.assertEqual(result, url)
        # Encoding twice must not double-encode the '%' itself.
        self.assertEqual(fetch_location.normalize_url(result), url)

    def test_query_string_preserved(self):
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/plain.jpg?width=1600"
        result = fetch_location.normalize_url(url)
        self.assertTrue(result.endswith("?width=1600"))

    def test_non_ascii_in_query_is_encoded(self):
        url = "https://example.org/path.jpg?title=Bakı"
        result = fetch_location.normalize_url(url)
        result.encode("ascii")
        self.assertIn("title=Bak%C4%B1", result)

    def test_scheme_and_netloc_untouched(self):
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/Bakı.jpg"
        result = fetch_location.normalize_url(url)
        self.assertTrue(result.startswith("https://commons.wikimedia.org/"))


if __name__ == "__main__":
    unittest.main()
