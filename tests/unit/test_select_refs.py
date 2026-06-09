"""Unit tests for render_book.select_refs — cap, auto-upgrade, drop-priority.

select_refs(candidates, model) -> (effective_model, selected, dropped_labels)
is fully pure (no disk/network/env). These tests run with no API key.
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import render_book

FLASH = render_book.FLASH_IMAGE_MODEL   # "gemini-3.1-flash-image"  cap 4
PRO = render_book.PRO_IMAGE_MODEL       # "gemini-3-pro-image"      cap 5


def _cands(n: int) -> list[tuple[str, str]]:
    """Return n dummy (label, path) candidate pairs in priority order."""
    return [(f"label-{i}", f"/fake/{i}.png") for i in range(n)]


class TestSelectRefs(unittest.TestCase):
    """render_book.select_refs — cap, flash→pro auto-upgrade, drop ordering."""

    def test_zero_candidates_flash(self):
        model, selected, dropped = render_book.select_refs([], FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(selected, [])
        self.assertEqual(dropped, [])

    def test_four_candidates_flash_no_upgrade(self):
        """Exactly at the flash cap: stays flash, nothing dropped."""
        c = _cands(4)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH)
        self.assertEqual(selected, c)
        self.assertEqual(dropped, [])

    def test_five_candidates_flash_upgrades_to_pro(self):
        """5 candidates exceeds flash cap (>4): auto-upgrades to pro, 0 dropped (PER-58)."""
        c = _cands(5)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO, "Expected flash→pro upgrade at 5 candidates")
        self.assertEqual(selected, c, "All 5 candidates fit under pro cap")
        self.assertEqual(dropped, [])

    def test_six_candidates_flash_upgrades_and_drops_one(self):
        """6 candidates at flash: upgrades to pro, last candidate dropped."""
        c = _cands(6)
        model, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, c[:5])
        self.assertEqual(dropped, ["label-5"])

    def test_five_candidates_pro_stays_pro(self):
        """At the pro cap: stays pro, nothing dropped."""
        c = _cands(5)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, c)
        self.assertEqual(dropped, [])

    def test_six_candidates_pro_drops_one(self):
        """One over the pro cap: stays pro, tail dropped."""
        c = _cands(6)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, c[:5])
        self.assertEqual(dropped, ["label-5"])

    def test_unknown_model_uses_fallback_cap_four(self):
        """Unknown model string: no upgrade check, fallback cap = MAX_INPUT_IMAGES (4)."""
        c = _cands(6)
        model, selected, dropped = render_book.select_refs(c, "unknown-model")
        self.assertEqual(model, "unknown-model", "Unknown model must not be upgraded")
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(dropped), 2)

    def test_drop_order_preserves_tail(self):
        """Dropped labels are exactly the tail of the priority-ordered list."""
        labels = [f"priority-{i}" for i in range(7)]
        c = [(lbl, f"/p/{i}.png") for i, lbl in enumerate(labels)]
        # 7 at flash → upgrades to pro (cap 5), drops 2 from the tail
        _, selected, dropped = render_book.select_refs(c, FLASH)
        self.assertEqual([lbl for lbl, _ in selected], labels[:5])
        self.assertEqual(dropped, ["priority-5", "priority-6"])

    def test_three_at_pro_no_drop(self):
        """Well under cap: nothing dropped."""
        c = _cands(3)
        model, selected, dropped = render_book.select_refs(c, PRO)
        self.assertEqual(model, PRO)
        self.assertEqual(selected, c)
        self.assertEqual(dropped, [])

    def test_upgrade_boundary_exactly_four_flash_no_upgrade(self):
        """The upgrade condition is len > flash_cap, i.e. > 4 (not >=4).
        Exactly 4 candidates at flash must NOT upgrade."""
        c = _cands(4)
        model, _, _ = render_book.select_refs(c, FLASH)
        self.assertEqual(model, FLASH, "4 candidates at flash must NOT trigger upgrade")


if __name__ == "__main__":
    unittest.main()
