"""Unit tests for render_book.select_refs — two-lane cap, auto-upgrade, drop-priority.

select_refs(candidates, model) -> (effective_model, selected, dropped_labels)
is fully pure (no disk/network/env). Candidates are (label, path, lane) triples
where lane is "character" or "object" (PER-83: two independent lanes, not one
flat cap — character lane 4 flash / 5 pro, object lane 10 on both models, 14
total). These tests run with no API key.
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

    # ------------------------------------------------------------------
    # Combined TOTAL_REF_CAP backstop — only bites when both lanes are maxed
    # ------------------------------------------------------------------

    def test_five_characters_ten_objects_pro_trims_total_cap(self):
        """5 characters (pro cap) + 10 objects (object cap) = 15 > TOTAL_REF_CAP
        (14): trims 1 object off the tail of the object lane to fit."""
        c = _char_cands(5) + _obj_cands(10)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(len(selected), 14)
        self.assertEqual(dropped, ["obj-9"], "Lowest-priority (last) object trimmed for the total cap")


if __name__ == "__main__":
    unittest.main()
