"""E2E: PER-35 top-bar cost readout.

Tests that GET /api/status's `costs` block actually reaches and renders in
the sticky top bar next to Save — the JSON-only check in test_costs.py (unit)
never exercises the DOM. No paid endpoints called (zero-API rule); the
ledger is hand-written directly into the temp fixture dir, never produced by
a real API call.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _support import EditorServer
from playwright.sync_api import sync_playwright


class TestCostReadout(unittest.TestCase):
    """Top-bar cost readout: empty with no ledger, sums correctly once one exists."""

    FIXTURE = "pip-storm"

    @classmethod
    def setUpClass(cls):
        # Start server first so playwright is never opened if the server fails.
        cls._server = EditorServer(cls.FIXTURE).start()
        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._playwright.stop()
        cls._server.stop()

    def setUp(self):
        self._pw = self._browser.new_page()

    def tearDown(self):
        self._pw.close()

    # ------------------------------------------------------------------

    def test_no_ledger_readout_is_empty_not_zero_dollars(self):
        """A book that has never been rendered shows nothing, not '$0.00'.

        Explicitly removes any ledger a previous test in this class may have
        written — tests share one server/tmp fixture dir and unittest doesn't
        guarantee alphabetical-is-declaration-order, so this can't rely on
        running before the ledger-writing tests.
        """
        ledger = self._server.story_path.parent / "costs.jsonl"
        ledger.unlink(missing_ok=True)
        self._pw.goto(self._server.url)
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)
        readout = self._pw.locator("#cost-readout")
        self.assertEqual(readout.inner_text(), "")

    def test_ledger_sum_renders_in_readout_and_tooltip(self):
        """A hand-written costs.jsonl (never a real API call) sums correctly."""
        ledger = self._server.story_path.parent / "costs.jsonl"
        ledger.write_text(
            '{"ts":"2026-08-13T14:00:00+02:00","script":"make_style_sheet.py",'
            '"vendor":"openai","model":"gpt-image-2","target":"style-sheet-pip.png",'
            '"ok":true,"usd":0.05,"estimated":false,"pricing_as_of":"2026-08-13","tokens":{}}\n'
            '{"ts":"2026-08-13T14:05:00+02:00","script":"render_book.py",'
            '"vendor":"gemini","model":"gemini-3.1-flash-image","target":"raw-page-01.png",'
            '"ok":true,"usd":0.10,"estimated":false,"pricing_as_of":"2026-08-13","tokens":{}}\n',
            encoding="utf-8",
        )
        self._pw.goto(self._server.url)
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)
        readout = self._pw.locator("#cost-readout")
        self.assertEqual(readout.inner_text(), "$0.15")
        title = readout.get_attribute("title")
        self.assertIn("make_style_sheet.py", title)
        self.assertIn("render_book.py", title)
        self.assertIn("2 calls total", title)

    def test_estimated_record_prefixes_tilde(self):
        """A record flagged estimated=true prefixes the readout with '~'."""
        ledger = self._server.story_path.parent / "costs.jsonl"
        ledger.write_text(
            '{"ts":"2026-08-13T14:00:00+02:00","script":"render_book.py",'
            '"vendor":"gemini","model":"gemini-3.1-flash-image","target":"raw-page-01.png",'
            '"ok":true,"usd":0.0672,"estimated":true,"pricing_as_of":"2026-08-13","tokens":{}}\n',
            encoding="utf-8",
        )
        self._pw.goto(self._server.url)
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)
        readout = self._pw.locator("#cost-readout")
        self.assertTrue(readout.inner_text().startswith("~$"))

    def test_unpriced_record_shown_in_readout(self):
        """A record with usd=null (unrecognised model) is surfaced, not silently dropped."""
        ledger = self._server.story_path.parent / "costs.jsonl"
        ledger.write_text(
            '{"ts":"2026-08-13T14:00:00+02:00","script":"render_book.py",'
            '"vendor":"gemini","model":"some-future-model","target":"raw-page-01.png",'
            '"ok":true,"usd":null,"estimated":false,"pricing_as_of":"2026-08-13","tokens":{}}\n',
            encoding="utf-8",
        )
        self._pw.goto(self._server.url)
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)
        readout = self._pw.locator("#cost-readout")
        self.assertIn("1 unpriced", readout.inner_text())


if __name__ == "__main__":
    unittest.main()
