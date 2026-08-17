"""E2E: @-mention cast picker — PER-62 regression guard.

Guards against the display:none re-application bug:
  Bad:  style.display = ''    → CSS display:none re-applies, popup invisible
  Fix:  style.display = 'block' → overrides the CSS rule, popup visible

Fixture: pip-storm (cast: pip=character, major-oak=location, page 0 = pip only).
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _support import EditorServer
from playwright.sync_api import sync_playwright, expect


class TestMentionPicker(unittest.TestCase):
    """@-mention picker: visibility, filtering, keyboard nav, click, focus retention."""

    FIXTURE = "pip-storm"

    @classmethod
    def setUpClass(cls):
        # Start server first so that playwright is never opened if the server fails.
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
        self._pw.wait_for_selector("#pg-prompt-0", state="visible", timeout=10_000)

    def tearDown(self):
        self._pw.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ta(self):
        """Return the first page's image-prompt textarea locator."""
        return self._pw.locator("#pg-prompt-0")

    def _pop(self):
        return self._pw.locator("#mention-pop")

    def _clear_and_type(self, text: str):
        ta = self._ta()
        ta.click()
        ta.fill("")
        ta.type(text)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_popup_hidden_on_load(self):
        """Mention popup starts hidden before any @ is typed."""
        expect(self._pop()).not_to_be_visible()

    def test_popup_visible_after_at_sign(self):
        """Typing '@' makes the mention popup visible (PER-62 regression guard).

        Verifies style.display='block' (not '') is used in _showMentionPop so
        the CSS display:none is properly overridden.
        """
        self._clear_and_type("@")
        expect(self._pop()).to_be_visible()

    def test_filter_by_partial_id(self):
        """Typing '@pi' filters rows to cast members matching 'pi' (pip matches)."""
        self._clear_and_type("@pi")
        expect(self._pop()).to_be_visible()
        rows = self._pw.locator("#mention-pop .mention-row")
        # At least one matching row
        self.assertGreaterEqual(rows.count(), 1)
        # The first matching row must be 'Pip'
        first_name = rows.first.locator(".m-name").text_content()
        self.assertIn("Pip", first_name)

    def test_enter_inserts_id_placeholder(self):
        """ArrowDown then Enter inserts an <id> placeholder into the textarea."""
        ta = self._ta()
        ta.click()
        ta.fill("")
        ta.type("@")
        expect(self._pop()).to_be_visible()
        ta.press("ArrowDown")
        ta.press("Enter")
        # Popup should close after selection
        expect(self._pop()).not_to_be_visible()
        # Textarea should contain a valid <id> token
        value = ta.input_value()
        self.assertRegex(value, r"<[a-z][a-z0-9-]*>",
                         f"Expected <id> placeholder in textarea, got: {value!r}")

    def test_escape_hides_popup(self):
        """Escape closes the mention popup without inserting anything."""
        self._clear_and_type("@")
        expect(self._pop()).to_be_visible()
        self._ta().press("Escape")
        expect(self._pop()).not_to_be_visible()

    def test_click_row_inserts_placeholder(self):
        """Clicking a .mention-row inserts the displayed <id> into the textarea."""
        ta = self._ta()
        ta.click()
        ta.fill("")
        ta.type("@")
        expect(self._pop()).to_be_visible()
        row = self._pw.locator("#mention-pop .mention-row").first
        # The .m-id span shows e.g. "<pip>"
        id_display = row.locator(".m-id").text_content().strip()  # e.g. "<pip>"
        row.click()
        expect(self._pop()).not_to_be_visible()
        value = ta.input_value()
        self.assertIn(id_display, value,
                      f"Expected {id_display!r} in textarea, got: {value!r}")

    def test_focus_retained_after_insert(self):
        """After @-pick, focus stays on the prompt textarea (no mid-type focus loss)."""
        ta = self._ta()
        ta.click()
        ta.fill("")
        ta.type("@")
        expect(self._pop()).to_be_visible()
        ta.press("Enter")
        focused_id = self._pw.evaluate("document.activeElement.id")
        self.assertEqual(focused_id, "pg-prompt-0",
                         f"Focus moved away from textarea (activeElement.id={focused_id!r})")


class TestMentionPickerLaneCap(unittest.TestCase):
    """PER-97: character-lane-full rows are shown disabled, with a reason line.

    Fixture: crowded-cast (page 0 = 5 characters + 1 object → lane full;
    page 1 = 4 characters → under cap, nothing blocked).
    """

    FIXTURE = "crowded-cast"

    @classmethod
    def setUpClass(cls):
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
        self._pw.wait_for_selector("#pg-prompt-0", state="visible", timeout=10_000)

    def tearDown(self):
        self._pw.close()

    # ------------------------------------------------------------------

    def _ta(self, page_idx: int = 0):
        return self._pw.locator(f"#pg-prompt-{page_idx}")

    def _pop(self):
        return self._pw.locator("#mention-pop")

    def _type_at(self, text: str, page_idx: int = 0):
        ta = self._ta(page_idx)
        ta.click()
        ta.press("End")
        ta.type(text)

    # ------------------------------------------------------------------

    def test_unmentioned_character_row_is_blocked_at_cap(self):
        """Page 0 is at the 5-character cap; 'fern' (Fern) is not yet on the
        page — its row must be disabled and clicking it must not insert."""
        self._type_at(" @fern")
        expect(self._pop()).to_be_visible()
        row = self._pw.locator("#mention-pop .mention-row").first
        self.assertIn("blocked", row.get_attribute("class") or "")
        before = self._ta().input_value()
        row.click(force=True)  # blocked row has no click handler; force past pointer-events
        after = self._ta().input_value()
        self.assertNotIn("<fern>", after,
                          f"Blocked row must not insert — before={before!r} after={after!r}")

    def test_already_mentioned_character_row_stays_selectable(self):
        """'ash' is already on page 0's cast — re-mentioning it is a no-op for
        the count (_castIdsFromPrompt dedupes), so its row must stay enabled."""
        self._type_at(" @ash")
        expect(self._pop()).to_be_visible()
        row = self._pw.locator("#mention-pop .mention-row").first
        self.assertNotIn("blocked", row.get_attribute("class") or "")
        row.click()
        expect(self._pop()).not_to_be_visible()  # _selectMentionItem hides it synchronously
        self.assertIn("<ash>", self._ta().input_value())

    def test_object_row_stays_selectable_at_character_cap(self):
        """The object lane is independent — 'lantern' must never be blocked
        by a full character lane."""
        self._type_at(" @lantern")
        expect(self._pop()).to_be_visible()
        row = self._pw.locator("#mention-pop .mention-row").first
        self.assertNotIn("blocked", row.get_attribute("class") or "")
        row.click()
        expect(self._pop()).not_to_be_visible()
        self.assertIn("<lantern>", self._ta().input_value())

    def test_note_visible_when_lane_full(self):
        """Unfiltered '@' on the at-cap page shows the character-lane-full reason line."""
        self._type_at(" @")
        expect(self._pop()).to_be_visible()
        note = self._pw.locator("#mention-pop .mention-note")
        expect(note).to_be_visible()
        self.assertIn("Character lane full", note.text_content())

    def test_under_cap_page_nothing_blocked(self):
        """Page 1 has 4 characters (under the 5 cap) — no row is disabled, no note."""
        self._type_at(" @", page_idx=1)
        expect(self._pop()).to_be_visible()
        self.assertEqual(
            self._pw.locator("#mention-pop .mention-row.blocked").count(), 0
        )
        self.assertEqual(self._pw.locator("#mention-pop .mention-note").count(), 0)


if __name__ == "__main__":
    unittest.main()
