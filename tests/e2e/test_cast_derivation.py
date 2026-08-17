"""E2E: 'Cast on this page' derives from image_prompt <id> tokens.

Guards _castIdsFromPrompt + _syncPageCast: the read-only cast view updates
as the user edits image_prompt. Hero = first character-kind mention; location/
other kinds get .kind-badge.

Fixture: gazelle-valley (every page: pip=character + gazelle-valley=location).
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _support import EditorServer
from playwright.sync_api import sync_playwright, expect


class TestCastDerivation(unittest.TestCase):
    """Cast-on-page view: hero badge, kind badge, empty state, initial render."""

    @classmethod
    def setUpClass(cls):
        # Start server first so playwright is never opened if the server fails.
        # gazelle-valley: every page has pip (character) + gazelle-valley (location).
        cls._server = EditorServer("gazelle-valley").start()
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

    def _set_prompt(self, value: str, page_idx: int = 0) -> None:
        """Fill the prompt textarea and blur it to trigger _syncPageCast."""
        ta = self._pw.locator(f"#pg-prompt-{page_idx}")
        ta.click()
        ta.fill(value)
        ta.press("Tab")  # blur → change event → _syncPageCast

    def _cast_section(self, page_idx: int = 0):
        return self._pw.locator(f"#pg-cast-{page_idx}")

    # ------------------------------------------------------------------

    def test_single_character_gets_hero_badge(self):
        """Typing only <pip> → pip entry has .hero-badge."""
        self._set_prompt("<pip> runs in the sun")
        section = self._cast_section()
        section.wait_for(state="visible")
        expect(section.locator(".hero-badge")).to_be_visible()

    def test_location_gets_kind_badge_loc(self):
        """<pip> + <gazelle-valley>: gazelle-valley entry gets .kind-badge 'LOC'."""
        self._set_prompt("<pip> explores <gazelle-valley>")
        section = self._cast_section()
        section.wait_for(state="visible")
        kind_badge = section.locator(".kind-badge").first
        expect(kind_badge).to_be_visible()
        self.assertEqual(kind_badge.text_content().strip(), "LOC")

    def test_hero_is_first_character_kind_even_if_location_appears_first(self):
        """Location id first, then character id: character still gets .hero-badge."""
        # _castIdsFromPrompt derives order from first-appearance, but hero is the first
        # *character-kind* entry, not the first entry overall.
        self._set_prompt("<gazelle-valley> has <pip> in it")
        section = self._cast_section()
        section.wait_for(state="visible")
        hero_entries = section.locator(".cast-entry").filter(
            has=self._pw.locator(".hero-badge")
        )
        expect(hero_entries).to_contain_text("Pip")

    def test_empty_prompt_shows_empty_state(self):
        """Clearing the prompt removes all cast entries; .cast-empty appears."""
        self._set_prompt("")
        section = self._cast_section()
        section.wait_for(state="visible")
        expect(section.locator(".cast-empty")).to_be_visible()

    def test_initial_render_reflects_story_json(self):
        """On load, cast-on-page already derived from the existing image_prompt."""
        # gazelle-valley page 0 prompt has both <pip> and <gazelle-valley>
        section = self._cast_section()
        section.wait_for(state="visible")
        count = section.locator(".cast-entry").count()
        self.assertEqual(count, 2, f"Expected 2 cast entries on page 0 after load, got {count}")


class TestCastDerivationLaneCapSave(unittest.TestCase):
    """PER-97: hand-typing a 6th character <id> directly into the textarea
    (bypassing the @-picker entirely) must still be caught — the picker guard
    alone would be cosmetic, since the real chokepoint is validate_story() on
    save (edit_story.py's PUT /api/story → 422).

    Fixture: crowded-cast (page 0 already has 5 characters — the hard cap —
    plus 1 object; 'fern' is the 6th character, not yet mentioned).
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

    def test_hand_typed_sixth_character_blocks_save(self):
        ta = self._pw.locator("#pg-prompt-0")
        ta.click()
        ta.press("End")
        ta.type(" <fern>")  # 6th character-kind mention, typed directly — no picker
        ta.press("Tab")    # blur → change → _syncPageCast + markDirty (arms auto-save)

        # Cast-on-page view reflects all 7 entries immediately (6 characters + lantern),
        # confirming page.cast already grew past the cap before we ever try to save.
        section = self._pw.locator("#pg-cast-0")
        expect(section.locator(".cast-entry")).to_have_count(7)

        # Let the 1500ms auto-save debounce (editor.html markDirty) settle before our
        # own explicit save click. Otherwise an in-flight background-save response
        # (routed to the quiet auto-save hint, not the panel) could be the one
        # `expect_response` below captures instead of the click's own response.
        self._pw.wait_for_timeout(1700)

        with self._pw.expect_response("**/api/story") as resp_info:
            self._pw.locator("#btn-save").click()
        self.assertEqual(
            resp_info.value.status, 422,
            "A page over the character-lane hard cap must be refused, not saved",
        )

        panel = self._pw.locator("#status-panel")
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("hard cap is 5")


if __name__ == "__main__":
    unittest.main()
