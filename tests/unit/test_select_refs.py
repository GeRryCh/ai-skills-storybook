"""Unit tests for render_book.select_refs — two-lane cap, auto-upgrade, drop-priority.

select_refs(candidates, model) -> (effective_model, selected, dropped_labels)
is fully pure (no disk/network/env). Candidates are (label, path, lane) triples
where lane is "character" or "object" (PER-83: two independent lanes, not one
flat cap — character lane 4 flash / 5 pro, object lane 10 flash / 6 pro
(PER-96), 14 total on flash / 11 on pro). These tests run with no API key.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import render_book

FLASH = render_book.FLASH_IMAGE_MODEL   # "gemini-3.1-flash-image"  character cap 4
PRO = render_book.PRO_IMAGE_MODEL       # "gemini-3-pro-image"      character cap 5


def _char_cands(n: int, prefix: str = "char") -> list[tuple[str, str, str]]:
    """n dummy character-lane (label, path, lane) triples in priority order."""
    return [(f"{prefix}-{i}", f"/fake/{prefix}-{i}.png", "character") for i in range(n)]


def _obj_cands(n: int, prefix: str = "obj") -> list[tuple[str, str, str]]:
    """n dummy object-lane (label, path, lane) triples in priority order."""
    return [(f"{prefix}-{i}", f"/fake/{prefix}-{i}.png", "object") for i in range(n)]


class TestSelectRefs(unittest.TestCase):
    """render_book.select_refs — per-lane caps, flash→pro auto-upgrade, drop ordering."""

    def test_zero_candidates_flash(self):
        model, selected, dropped = render_book.select_refs([], FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(selected, [])
        self.assertEqual(dropped, [])

    def test_four_characters_flash_no_upgrade(self):
        """Exactly at the flash character-lane cap: stays flash, nothing dropped."""
        c = _char_cands(4)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(selected, [(l, p) for l, p, _ in c])
        self.assertEqual(dropped, [])

    def test_five_characters_flash_upgrades_to_pro(self):
        """5 characters exceeds flash character-lane cap (>4): auto-upgrades to
        pro, 0 dropped (PER-58/PER-83)."""
        c = _char_cands(5)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO, "Expected flash→pro upgrade at 5 characters")
        self.assertEqual(selected, [(l, p) for l, p, _ in c], "All 5 characters fit under pro cap")
        self.assertEqual(dropped, [])

    def test_six_characters_flash_upgrades_and_drops_one(self):
        """6 characters at flash: upgrades to pro, last character dropped (pro cap 5)."""
        c = _char_cands(6)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c[:5]])
        self.assertEqual(dropped, ["char-5"])

    def test_five_characters_pro_stays_pro(self):
        """At the pro character-lane cap: stays pro, nothing dropped."""
        c = _char_cands(5)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c])
        self.assertEqual(dropped, [])

    def test_six_characters_pro_drops_one(self):
        """One over the pro character-lane cap: stays pro, tail dropped."""
        c = _char_cands(6)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c[:5]])
        self.assertEqual(dropped, ["char-5"])

    def test_unknown_model_uses_fallback_character_cap_four(self):
        """Unknown model string: no upgrade check, fallback character-lane cap = 4."""
        c = _char_cands(6)
        model, selected, dropped = render_book.select_refs(c, "unknown-model")
        self.assertEqual(model, "unknown-model", "Unknown model must not be upgraded")
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(dropped), 2)

    def test_unknown_model_uses_fallback_object_cap_six(self):
        """Unknown model string: fallback object-lane cap = 6 (PER-96), same
        conservative floor as MAX_CHARACTER_LANE for characters."""
        c = _obj_cands(8)
        model, selected, dropped = render_book.select_refs(c, "unknown-model")
        self.assertEqual(model, "unknown-model")
        self.assertEqual(len(selected), 6)
        self.assertEqual(dropped, ["obj-6", "obj-7"])

    def test_drop_order_preserves_tail(self):
        """Dropped character labels are exactly the tail of the priority-ordered list."""
        c = _char_cands(7, prefix="priority")
        # 7 at flash → upgrades to pro (character cap 5), drops 2 from the tail
        _, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual([lbl for lbl, _ in selected], [f"priority-{i}" for i in range(5)])
        self.assertEqual(dropped, ["priority-5", "priority-6"])

    def test_three_characters_at_pro_no_drop(self):
        """Well under the character cap: nothing dropped."""
        c = _char_cands(3)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c])
        self.assertEqual(dropped, [])

    def test_upgrade_boundary_exactly_four_characters_flash_no_upgrade(self):
        """The upgrade condition is character count > 4, not >=4. Exactly 4
        characters at flash must NOT upgrade."""
        c = _char_cands(4)
        model, _, _ = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH, "4 characters at flash must NOT trigger upgrade")

    # ------------------------------------------------------------------
    # Object lane — independent cap, never triggers auto-upgrade
    # ------------------------------------------------------------------

    def test_ten_objects_flash_no_upgrade_no_drop(self):
        """Object lane at its cap (10): stays flash (object overflow never
        upgrades), nothing dropped."""
        c = _obj_cands(10)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(selected, [(l, p) for l, p, _ in c])
        self.assertEqual(dropped, [])

    def test_eleven_objects_flash_no_upgrade_drops_one(self):
        """11 objects exceeds the object-lane cap (10): stays flash — object
        overflow never triggers auto-upgrade — but the tail object is dropped."""
        c = _obj_cands(11)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH, "Object-lane overflow must not upgrade the model")
        self.assertEqual(selected, [(l, p) for l, p, _ in c[:10]])
        self.assertEqual(dropped, ["obj-10"])

    def test_six_objects_pro_no_drop(self):
        """Pro's object-lane cap is 6 (PER-96), not 10. Exactly at cap: nothing
        dropped."""
        c = _obj_cands(6)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c])
        self.assertEqual(dropped, [])

    def test_seven_objects_pro_drops_one(self):
        """One over pro's object-lane cap (6): stays pro — object overflow
        never upgrades — tail object dropped."""
        c = _obj_cands(7)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, [(l, p) for l, p, _ in c[:6]])
        self.assertEqual(dropped, ["obj-6"])

    def test_four_characters_ten_objects_flash_fourteen_total_no_drop(self):
        """4 characters + 10 objects = 14 total (both lane caps exactly hit) on
        flash: no upgrade, nothing dropped — the full documented envelope."""
        c = _char_cands(4) + _obj_cands(10)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(len(selected), 14)
        self.assertEqual(dropped, [])

    def test_boris_page_02_shape_two_characters_three_objects_flash(self):
        """The confirmed PER-83 regression case: 2 characters + 2 objects + 1
        location (object lane) = 5 mixed refs. Under the old flat cap this
        dropped the lowest-priority ref (the location); with lanes it fits on
        flash with zero drops — 2 in the character lane, 3 in the object lane."""
        c = _char_cands(2) + _obj_cands(3)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH, "2 chars + 3 object-lane refs must stay on flash")
        self.assertEqual(len(selected), 5)
        self.assertEqual(dropped, [])

    def test_six_characters_two_objects_flash_upgrades_drops_one_character(self):
        """6 characters (over character cap) + 2 objects (under object cap) on
        flash: upgrades to pro (character overflow), drops 1 character; objects
        untouched."""
        c = _char_cands(6) + _obj_cands(2)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO)
        self.assertEqual(len(selected), 7, "5 chars (pro cap) + 2 objects")
        self.assertEqual(dropped, ["char-5"])

    def test_fifth_character_upgrade_shrinks_object_lane(self):
        """PER-96 regression guard for the accepted trade-off (decision 1 in the
        PER-96 plan): 5 characters + 8 objects on flash upgrades to pro to keep
        all 5 characters, but pro's smaller object cap (6, not 10) then drops 2
        objects that would have survived on flash. The upgrade rule stays
        character-lane-only — it does not weigh the object-lane cost."""
        c = _char_cands(5) + _obj_cands(8)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO, "5 characters must still trigger the upgrade")
        self.assertEqual(
            [lbl for lbl, _ in selected],
            [f"char-{i}" for i in range(5)] + [f"obj-{i}" for i in range(6)],
            "All 5 characters kept; only 6 of 8 objects fit pro's object-lane cap",
        )
        self.assertEqual(dropped, ["obj-6", "obj-7"])

    # ------------------------------------------------------------------
    # Combined TOTAL_REF_CAP backstop — defensive only; no current model
    # combination reaches it (flash 4+10=14, pro 5+6=11) per PER-96
    # ------------------------------------------------------------------

    def test_five_characters_ten_objects_pro_object_cap_trims_not_total_cap(self):
        """5 characters (pro cap) + 10 objects on pro: PER-96 shrank the pro
        object-lane cap to 6, so the object lane itself now trims 4 objects
        before TOTAL_REF_CAP (14) is ever in play — 5 + 6 = 11, well under it."""
        c = _char_cands(5) + _obj_cands(10)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(len(selected), 11, "5 characters + pro's 6-slot object lane")
        self.assertEqual(
            dropped, ["obj-6", "obj-7", "obj-8", "obj-9"],
            "Object-lane cap (6), not the total cap, is what trims here",
        )

    def test_total_ref_cap_backstop_still_reachable(self):
        """TOTAL_REF_CAP is unreachable with the shipped per-model caps (flash
        4+10=14, pro 5+6=11 — see the two tests above). It survives in
        select_refs as a guard against future cap edits (CLAUDE.md, module
        docstring), so exercise the branch directly by widening the pro
        object cap past what TOTAL_REF_CAP allows — proving the backstop
        still trims correctly if a lane cap is ever edited to overshoot 14."""
        orig_pro_cap = render_book.OBJECT_LANE_CAP[PRO]
        render_book.OBJECT_LANE_CAP[PRO] = 12
        try:
            c = _char_cands(5) + _obj_cands(12)
            model, selected, dropped = render_book.select_refs(c, PRO)
            self.assertEqual(model, PRO)
            self.assertEqual(len(selected), render_book.TOTAL_REF_CAP)
            self.assertEqual(dropped, ["obj-9", "obj-10", "obj-11"])
        finally:
            render_book.OBJECT_LANE_CAP[PRO] = orig_pro_cap


if __name__ == "__main__":
    unittest.main()
