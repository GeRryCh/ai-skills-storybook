"""Unit tests for PER-87: auto-injected prompt guards.

Zero API calls. Verifies:
  - item 1: no-duplicate-characters guard, derived from pages[].cast character-kind
    entries (display names, never ids; objects/locations never counted; unknown ids
    skipped; empty/no-character pages get no guard)
  - item 2: cast[].persistent_details appended as a continuity clause for on-page
    entries that set it; multiple entries join; absent field -> no clause
  - item 3: scene_text ("suppress"/"allow") default-on suppression of invented scene
    lettering, with a native-mode carve-out for the model's own story text; "allow"
    emits no replacement clause; resolve_scene_text() precedence
  - PER-105: NO_FRAME_DIRECTIVE (unconditional — no story field gates it, unlike
    scene_text) is present in build_page_guards() output on every page, including
    a scenery-only page with no cast, no persistent_details, and scene_text: "allow"
  - guard placement: injected after the (premise-bearing) anchor, before the
    mode-specific directive; native mode keeps NATIVE_TEXT_DIRECTIVE the final token
  - back-compat: a page/story using none of the PER-87 fields differs from a
    guards-free baseline only by the default suppress clause + PER-105's
    unconditional no-frame directive
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import render_book

_STORY_BASE = {
    "style_guide": {"medium": "soft watercolor"},
    "cast": [
        {"id": "eva", "name": "Eva", "kind": "character",
         "persistent_details": "red hair ribbon"},
        {"id": "grandpa", "name": "Grandpa Vagif", "kind": "character"},
        {"id": "german", "name": "German", "kind": "character",
         "persistent_details": "dark baseball cap with sunglasses resting on the brim"},
        {"id": "red-umbrella", "name": "Red Umbrella", "kind": "object"},
        {"id": "eiffel-tower", "name": "Eiffel Tower", "kind": "location"},
    ],
}


def _story() -> dict:
    import copy
    return copy.deepcopy(_STORY_BASE)


def _page(cast: list[str], **extra) -> dict:
    return {
        "image_prompt": "a quiet afternoon scene",
        "text": "Once upon a time.",
        "text_placement": "bottom",
        "cast": cast,
        **extra,
    }


# ---------------------------------------------------------------------------
# Item 1: no-duplicate-characters guard
# ---------------------------------------------------------------------------

class TestDuplicateGuard(unittest.TestCase):

    def test_zero_characters_no_guard(self):
        self.assertEqual(render_book._duplicate_guard(_page([]), _story()), "")

    def test_zero_characters_objects_and_locations_only(self):
        # Non-character cast entries never count toward the guard.
        page = _page(["red-umbrella", "eiffel-tower"])
        self.assertEqual(render_book._duplicate_guard(page, _story()), "")

    def test_single_character_singular_phrasing(self):
        out = render_book._duplicate_guard(_page(["eva"]), _story())
        self.assertIn("Exactly one named character", out)
        self.assertIn("Eva", out)
        self.assertIn(
            "Unnamed background figures such as crowds or passers-by are allowed "
            "and are not counted",
            out,
        )

    def test_three_characters_matches_issue_example_shape(self):
        out = render_book._duplicate_guard(_page(["eva", "grandpa", "german"]), _story())
        self.assertIn("Exactly 3 named characters", out)
        self.assertIn("no duplicates", out)
        self.assertIn("one Eva", out)
        self.assertIn("one Grandpa Vagif", out)
        self.assertIn("one German", out)

    def test_objects_and_locations_excluded_from_count(self):
        # 1 character + 1 object + 1 location -> guard still says "one", not "3".
        out = render_book._duplicate_guard(
            _page(["eva", "red-umbrella", "eiffel-tower"]), _story()
        )
        self.assertIn("Exactly one named character", out)

    def test_unknown_id_skipped(self):
        out = render_book._duplicate_guard(_page(["nonexistent", "eva"]), _story())
        self.assertIn("Exactly one named character", out)

    def test_uses_display_name_never_id(self):
        out = render_book._duplicate_guard(_page(["eva"]), _story())
        self.assertNotIn("<eva>", out)
        self.assertNotIn("eva,", out)  # id form would be lowercase, unlike "Eva"


# ---------------------------------------------------------------------------
# Item 2: persistent_details continuity clause
# ---------------------------------------------------------------------------

class TestPersistentDetailsGuard(unittest.TestCase):

    def test_no_entry_sets_it_no_clause(self):
        # grandpa has no persistent_details.
        self.assertEqual(
            render_book._persistent_details_guard(_page(["grandpa"]), _story()), ""
        )

    def test_single_entry(self):
        out = render_book._persistent_details_guard(_page(["german"]), _story())
        self.assertIn("Continuity details that must stay visible", out)
        self.assertIn("German — dark baseball cap with sunglasses resting on the brim", out)

    def test_multiple_entries_joined(self):
        out = render_book._persistent_details_guard(_page(["eva", "german"]), _story())
        self.assertIn("Eva — red hair ribbon", out)
        self.assertIn("German — dark baseball cap with sunglasses resting on the brim", out)

    def test_entry_not_on_page_contributes_nothing(self):
        # german has persistent_details, but isn't in this page's cast.
        out = render_book._persistent_details_guard(_page(["grandpa"]), _story())
        self.assertNotIn("German", out)

    def test_empty_page_cast_no_clause(self):
        self.assertEqual(render_book._persistent_details_guard(_page([]), _story()), "")


# ---------------------------------------------------------------------------
# Item 3: scene_text suppression
# ---------------------------------------------------------------------------

class TestSceneTextGuard(unittest.TestCase):

    def test_suppress_default_non_native(self):
        for mode in ("overlay", "long"):
            out = render_book._scene_text_guard("suppress", mode)
            self.assertEqual(out, render_book.SCENE_TEXT_SUPPRESS_DIRECTIVE)

    def test_suppress_native_carve_out_differs(self):
        native_out = render_book._scene_text_guard("suppress", "native")
        overlay_out = render_book._scene_text_guard("suppress", "overlay")
        self.assertNotEqual(native_out, overlay_out)
        self.assertIn("story text specified below", native_out)

    def test_allow_emits_nothing(self):
        for mode in ("overlay", "native", "long"):
            self.assertEqual(render_book._scene_text_guard("allow", mode), "")


class TestResolveSceneText(unittest.TestCase):

    def test_default_is_suppress(self):
        self.assertEqual(render_book.resolve_scene_text({}), "suppress")

    def test_story_field_wins_over_default(self):
        self.assertEqual(
            render_book.resolve_scene_text({"scene_text": "allow"}), "allow"
        )

    def test_page_field_beats_story_field(self):
        story = {"scene_text": "allow"}
        page = {"scene_text": "suppress"}
        self.assertEqual(render_book.resolve_scene_text(story, page), "suppress")

    def test_cli_beats_page_and_story(self):
        story = {"scene_text": "allow"}
        page = {"scene_text": "allow"}
        self.assertEqual(
            render_book.resolve_scene_text(story, page, cli="suppress"), "suppress"
        )

    def test_page_none_skips_per_page_resolution(self):
        self.assertEqual(
            render_book.resolve_scene_text({"scene_text": "allow"}, None), "allow"
        )


# ---------------------------------------------------------------------------
# Guard placement inside build_image_prompt
# ---------------------------------------------------------------------------

class TestGuardPlacementInBuildImagePrompt(unittest.TestCase):

    def _out(self, mode: str, page: dict, story: dict, scene_text: str = "suppress") -> str:
        return render_book.build_image_prompt(page, story, mode, scene_text)

    def test_guards_present_in_all_three_modes(self):
        story = _story()
        page = _page(["eva", "grandpa", "german"])
        for mode in ("overlay", "native", "long"):
            out = self._out(mode, page, story)
            self.assertIn("Exactly 3 named characters", out,
                          f"duplicate guard missing in {mode} mode")

    def test_long_mode_cover_uses_overlay_call(self):
        # render_page calls build_image_prompt(page, story, "overlay", scene_text) for
        # the long-mode cover — same function/mode as the plain overlay path, so guard
        # behaviour there is exercised by the "overlay" mode case above.
        story = _story()
        page = _page(["eva"])
        out = self._out("overlay", page, story)
        self.assertIn("Exactly one named character", out)

    def test_native_mode_directive_stays_last_token(self):
        story = _story()
        page = _page(["eva", "german"])
        out = self._out("native", page, story).rstrip()
        # NATIVE_TEXT_DIRECTIVE ends with the literal story text in quotes.
        self.assertTrue(out.endswith('"Once upon a time."'),
                        "native lettering directive must remain the final token")
        # The scene-text guard and duplicate guard must precede it, not follow it.
        guard_idx = out.index("Exactly 2 named characters")
        directive_idx = out.index("Render this exact story text")
        self.assertLess(guard_idx, directive_idx,
                        "guards must be injected before NATIVE_TEXT_DIRECTIVE")

    def test_native_scene_text_carve_out_present(self):
        story = _story()
        page = _page([])
        out = self._out("native", page, story)
        self.assertIn("story text specified below", out)

    def test_allow_removes_scene_text_clause_in_every_mode(self):
        story = _story()
        page = _page([])
        for mode in ("overlay", "native", "long"):
            out = self._out(mode, page, story, scene_text="allow")
            self.assertNotIn("no lettering", out)
            self.assertNotIn("no readable words", out)

    def test_default_scene_text_param_is_suppress(self):
        # build_image_prompt's scene_text defaults to "suppress" when omitted, matching
        # every existing call site that doesn't thread a resolved value explicitly.
        story = _story()
        page = _page([])
        out = render_book.build_image_prompt(page, story, "overlay")
        self.assertIn("No written signs, no lettering", out)

    def test_no_frame_directive_present_in_all_three_modes_unconditionally(self):
        # PER-105: unlike scene_text, nothing gates this — present even with
        # scene_text: "allow" and an empty cast (no other guard would fire).
        story = _story()
        page = _page([])
        for mode in ("overlay", "native", "long"):
            out = self._out(mode, page, story, scene_text="allow")
            self.assertIn(render_book.NO_FRAME_DIRECTIVE, out, f"missing in {mode} mode")

    def test_no_frame_directive_also_in_text_bg_prompt(self):
        # The one prompt builder that takes no guards at all (no STYLE_ANCHOR
        # either) — PER-105 must be added there explicitly, not folded into
        # build_page_guards, so it's easy to miss. Guard against that.
        out = render_book.build_text_bg_prompt(_story())
        self.assertIn(render_book.NO_FRAME_DIRECTIVE, out)


# ---------------------------------------------------------------------------
# Back-compat: stories/pages using none of the new fields
# ---------------------------------------------------------------------------

class TestBackCompat(unittest.TestCase):

    def test_no_new_fields_differs_only_by_suppress_clause(self):
        # A page shaped like pre-PER-87 authoring (no character cast, no
        # persistent_details anywhere, story doesn't set scene_text) should carry
        # exactly the default suppress guard and nothing else new — except
        # PER-105's unconditional no-frame directive, which no field opts out of.
        story = _story()
        page = _page([])  # scenery-only: no duplicate guard, no persistent-details clause
        out = render_book.build_image_prompt(page, story, "overlay")
        self.assertNotIn("named character", out)
        self.assertNotIn("Continuity details", out)
        self.assertIn(render_book.SCENE_TEXT_SUPPRESS_DIRECTIVE, out)
        self.assertIn(render_book.NO_FRAME_DIRECTIVE, out)

    def test_build_page_guards_empty_cast_is_suppress_plus_no_frame_only(self):
        # PER-105 replaces the old "exactly the suppress clause" contract: the
        # no-frame directive is now unconditional, so an otherwise-empty page's
        # guards are exactly these two clauses, joined by build_page_guards'
        # single-space separator — nothing more, nothing less.
        story = _story()
        page = _page([])
        guards = render_book.build_page_guards(page, story, "overlay", "suppress")
        expected = f"{render_book.SCENE_TEXT_SUPPRESS_DIRECTIVE} {render_book.NO_FRAME_DIRECTIVE}"
        self.assertEqual(guards, expected)


if __name__ == "__main__":
    unittest.main()
