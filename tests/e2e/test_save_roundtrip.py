"""E2E: save round-trip — edits persist after page reload.

Tests PUT /api/story: edit a field → save → reload → value present.
No paid endpoints called (zero-API rule).
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _support import EditorServer
from playwright.sync_api import sync_playwright, expect


class TestSaveRoundtrip(unittest.TestCase):
    """Save edits → reload → values persisted via PUT /api/story."""

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
        self._pw.goto(self._server.url)
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)

    def tearDown(self):
        self._pw.close()

    # ------------------------------------------------------------------

    def _save_and_assert_200(self) -> None:
        """Click #btn-save; assert HTTP 200 from PUT /api/story."""
        with self._pw.expect_response("**/api/story") as resp_info:
            self._pw.locator("#btn-save").click()
        status = resp_info.value.status
        self.assertEqual(status, 200, f"Save returned HTTP {status}")

    # ------------------------------------------------------------------

    def test_page_text_persists_after_reload(self):
        """Edit page text, save, reload — the new text is still there."""
        new_text = "E2E test was here (text)."
        ta = self._pw.locator("#pg-text-0")
        ta.click()
        ta.fill(new_text)
        ta.press("Tab")  # blur → markDirty

        self._save_and_assert_200()

        self._pw.reload()
        self._pw.wait_for_selector("#pg-text-0", state="visible", timeout=10_000)
        saved = self._pw.locator("#pg-text-0").input_value()
        self.assertEqual(saved, new_text)

    def test_image_prompt_persists_after_reload(self):
        """Edit image_prompt (no <id> tokens to keep it simple), save, reload."""
        new_prompt = "E2E test prompt, no placeholders here."
        ta = self._pw.locator("#pg-prompt-0")
        ta.click()
        ta.fill(new_prompt)
        ta.press("Tab")

        self._save_and_assert_200()

        self._pw.reload()
        self._pw.wait_for_selector("#pg-prompt-0", state="visible", timeout=10_000)
        saved = self._pw.locator("#pg-prompt-0").input_value()
        self.assertEqual(saved, new_prompt)


if __name__ == "__main__":
    unittest.main()
