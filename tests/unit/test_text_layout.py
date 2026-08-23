"""Unit tests for overlay_text.py's one box model (PER-104).

Pure geometry (no disk I/O except two end-to-end composite calls that write to
a tempdir). Asserts the invariants the ticket promises, not pixel-exact output:
same font size regardless of word count (panel height follows content instead),
scale invariance across page sizes (the PER-106 upscale unblock), the overflow
guard firing only as a last resort and reporting what it dropped, and all three
text alignments landing at the correct edge of the text column.
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401

import edit_story
import overlay_text as ot
from PIL import Image

FONT_REF = ot._resolve_font_ref("reader")  # bundled Andika — deterministic, no system-font variance


def _words(n: int) -> str:
    """n distinct-ish words, cheap to wrap (short word-wrap keeps tests fast)."""
    base = (
        "The meadow held its breath as clouds gathered low and grey above the "
        "hawthorn hedge while every small creature turned to look up at the sky"
    ).split()
    return " ".join((base * ((n // len(base)) + 1))[:n])


class TestSameFontSizeDifferentLength(unittest.TestCase):
    """The ticket's headline symptom: font size must not depend on word count."""

    def test_short_and_long_text_pick_same_font_size(self):
        lay = ot.LayoutSettings().scaled(2048, 2048)
        max_box_h = int(2048 * lay.max_panel_fraction)
        size_short, _, _, box_h_short, dropped_short = ot.measure_text_block(
            "Short.", FONT_REF, 2048, 2048, lay, max_box_h
        )
        size_long, _, _, box_h_long, dropped_long = ot.measure_text_block(
            _words(200), FONT_REF, 2048, 2048, lay, max_box_h
        )
        self.assertEqual(size_short, size_long)
        self.assertEqual(size_short, ot.DEFAULT_FONT_SIZE)
        self.assertEqual(dropped_short, 0)
        self.assertEqual(dropped_long, 0)
        self.assertGreater(box_h_long, box_h_short, "more text must make a taller panel")

    def test_panel_height_grows_monotonically_with_word_count(self):
        lay = ot.LayoutSettings().scaled(2048, 2048)
        max_box_h = int(2048 * lay.max_panel_fraction)
        heights = []
        sizes = []
        for n in (10, 50, 100):
            size, _, _, box_h, dropped = ot.measure_text_block(
                _words(n), FONT_REF, 2048, 2048, lay, max_box_h
            )
            sizes.append(size)
            heights.append(box_h)
            self.assertEqual(dropped, 0)
        self.assertEqual(sizes, [ot.DEFAULT_FONT_SIZE] * 3, "guard must not fire for ordinary lengths")
        self.assertLess(heights[0], heights[1])
        self.assertLess(heights[1], heights[2])


class TestScaleInvariance(unittest.TestCase):
    """Same text at 1024/2048/4096 must occupy the same font and panel *fraction*
    (min(w,h)/reference_size scaling — the PER-106 upscale/1K-fallback unblock)."""

    def test_font_and_panel_fraction_stable_across_resolutions(self):
        text = _words(200)
        fractions = []
        for dim in (1024, 2048, 4096):
            lay = ot.LayoutSettings().scaled(dim, dim)
            max_box_h = int(dim * lay.max_panel_fraction)
            size, _, _, box_h, dropped = ot.measure_text_block(text, FONT_REF, dim, dim, lay, max_box_h)
            self.assertEqual(dropped, 0)
            fractions.append((size / dim, box_h / dim))

        font_fracs = [f for f, _ in fractions]
        panel_fracs = [p for _, p in fractions]
        # Font fraction should be near-exact (integer px rounding only).
        self.assertAlmostEqual(font_fracs[0], font_fracs[1], delta=0.01)
        self.assertAlmostEqual(font_fracs[1], font_fracs[2], delta=0.01)
        # Panel fraction: wrap-point granularity differs slightly by resolution
        # (more available px per line at higher res can shift a word to the next
        # line), so allow more slack than the font fraction.
        self.assertAlmostEqual(panel_fracs[0], panel_fracs[1], delta=0.05)
        self.assertAlmostEqual(panel_fracs[1], panel_fracs[2], delta=0.05)


class TestOverflowGuard(unittest.TestCase):
    """Font shrinks in exactly one circumstance: the panel would otherwise exceed
    its boundary — and even then, failure is loud, never silent (PER-104's fold-in
    of the old silent-clip bug)."""

    def test_guard_does_not_fire_when_panel_fits(self):
        lay = ot.LayoutSettings().scaled(2048, 2048)
        size, _, _, box_h, dropped = ot.measure_text_block(
            _words(50), FONT_REF, 2048, 2048, lay, max_box_h=int(2048 * lay.max_panel_fraction)
        )
        self.assertEqual(size, lay.font_size)
        self.assertEqual(dropped, 0)

    def test_guard_shrinks_to_floor_and_reports_drop_when_forced(self):
        lay = ot.LayoutSettings().scaled(2048, 2048)
        # A tiny boundary forces the guard to its floor regardless of word count —
        # deterministic and fast, unlike growing the text itself (word-wrap cost
        # scales with word count; thousands of words takes real wall-clock time).
        size, lines, line_h, box_h, dropped = ot.measure_text_block(
            _words(200), FONT_REF, 2048, 2048, lay, max_box_h=200
        )
        self.assertEqual(size, lay.min_font_size)
        self.assertGreater(dropped, 0)
        self.assertLessEqual(box_h, 200)

    def test_end_to_end_warn_and_composite_never_blocks(self):
        """A page that overflows still gets a file on disk (warn-and-composite,
        never fail-the-book), and stderr names it with the stable marker."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.png"
            Image.new("RGB", (600, 600), (200, 200, 200)).save(src)
            out = Path(tmp) / "overflow-out.png"
            tiny = ot.LayoutSettings(max_panel_fraction=0.03)

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                result = ot.overlay(src, _words(200), "bottom", out, layout=tiny)

            self.assertTrue(result.exists())
            stderr = buf.getvalue()
            self.assertIn("TEXT-OVERFLOW:", stderr)
            self.assertIn(out.name, stderr)
            self.assertIn("dropped", stderr)

    def test_no_warning_printed_when_nothing_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.png"
            Image.new("RGB", (600, 600), (200, 200, 200)).save(src)
            out = Path(tmp) / "ok-out.png"

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                ot.overlay(src, "Short caption.", "bottom", out)
            self.assertNotIn("TEXT-OVERFLOW:", buf.getvalue())


class TestMaxPanelFraction(unittest.TestCase):
    """The boundary applies to every growth anchor (band top/bottom, text-page
    center) — same setting, asymmetry is in where the free space lands."""

    def _measure(self, anchor_max_box_h_fraction, h=2048):
        lay = ot.LayoutSettings(max_panel_fraction=anchor_max_box_h_fraction).scaled(h, h)
        max_box_h = int(h * lay.max_panel_fraction)
        return ot.measure_text_block(_words(150), FONT_REF, h, h, lay, max_box_h)

    def test_tighter_boundary_shrinks_font_more(self):
        size_loose, _, _, box_h_loose, _ = self._measure(0.9)
        size_tight, _, _, box_h_tight, _ = self._measure(0.2)
        self.assertLessEqual(size_tight, size_loose)
        self.assertLessEqual(box_h_tight, int(2048 * 0.2) + 1)


class TestAlignment(unittest.TestCase):
    """PER-100: all three alignments defined against the text column [h_pad, w - h_pad]."""

    def _ink_bbox(self, align):
        font = ot._load_font(FONT_REF, 30)
        layer = ot._draw_text_lines(400, 200, ["Hi"], font, 40, 10, 20, 360, align, "dark")
        return layer.split()[-1].getbbox()  # alpha channel bbox

    def test_left_starts_at_h_pad(self):
        bbox = self._ink_bbox("left")
        self.assertAlmostEqual(bbox[0], 20, delta=3)

    def test_right_ends_at_w_minus_h_pad(self):
        bbox = self._ink_bbox("right")
        self.assertAlmostEqual(bbox[2], 400 - 20, delta=3)

    def test_center_is_between_left_and_right(self):
        left = self._ink_bbox("left")
        center = self._ink_bbox("center")
        right = self._ink_bbox("right")
        self.assertGreater(center[0], left[0])
        self.assertLess(center[2], right[2])


class TestLayoutDefaultsInSync(unittest.TestCase):
    """story_schema.json's 'layout' defaults (read by editor.html for its
    placeholders — never hard-coded there) must equal overlay_text.py's own
    DEFAULT_* constants (the values actually used when a key is absent). Same
    duplicated-constant discipline as test_lane_caps_in_sync.py — see
    .claude/rules/script-authoring.md."""

    def setUp(self):
        self.layout_schema = edit_story.load_schema()["properties"]["layout"]["properties"]

    def test_reference_size(self):
        self.assertEqual(self.layout_schema["reference_size"]["default"], ot.REFERENCE_SIZE)

    def test_font_size(self):
        self.assertEqual(self.layout_schema["font_size"]["default"], ot.DEFAULT_FONT_SIZE)

    def test_min_font_size(self):
        self.assertEqual(self.layout_schema["min_font_size"]["default"], ot.DEFAULT_MIN_FONT_SIZE)

    def test_padding_h(self):
        self.assertEqual(self.layout_schema["padding"]["properties"]["h"]["default"], ot.DEFAULT_PAD_H)

    def test_padding_v(self):
        self.assertEqual(self.layout_schema["padding"]["properties"]["v"]["default"], ot.DEFAULT_PAD_V)

    def test_radius(self):
        self.assertEqual(self.layout_schema["radius"]["default"], ot.DEFAULT_RADIUS)

    def test_feather(self):
        self.assertEqual(self.layout_schema["feather"]["default"], ot.DEFAULT_FEATHER)

    def test_max_panel_fraction(self):
        self.assertEqual(self.layout_schema["max_panel_fraction"]["default"], ot.DEFAULT_MAX_PANEL_FRACTION)


class TestOverflowWarningsByPage(unittest.TestCase):
    """edit_story._overflow_warnings_by_page — splits one regenerate-all run's
    combined stdout (many pages, one subprocess, no --only) back out per page,
    so the editor can attach each page's own warnings rather than showing every
    page the whole book's overflow list."""

    def setUp(self):
        self.f = edit_story._overflow_warnings_by_page

    def test_single_page_warning(self):
        stdout = (
            "=== Page 4 (text-mode: overlay) ===\n"
            "TEXT-OVERFLOW: page-04.png — configured 48px, used 22px, 2 line(s) dropped\n"
            "  Done: pages/page-04.png\n"
        )
        result = self.f(stdout)
        self.assertEqual(list(result.keys()), [4])
        self.assertEqual(len(result[4]), 1)

    def test_multiple_pages_grouped_separately(self):
        stdout = (
            "TEXT-OVERFLOW: page-03.png — configured 48px, used 30px, 1 line(s) dropped\n"
            "TEXT-OVERFLOW: page-07-long-text.png — configured 48px, used 22px, 4 line(s) dropped\n"
        )
        result = self.f(stdout)
        self.assertEqual(set(result.keys()), {3, 7})
        self.assertIn("page-03.png", result[3][0])
        self.assertIn("page-07-long-text.png", result[7][0])

    def test_leading_zero_stripped_from_page_number(self):
        stdout = "TEXT-OVERFLOW: page-04-native.png — configured 48px, used 22px, 1 line(s) dropped\n"
        result = self.f(stdout)
        self.assertEqual(list(result.keys()), [4])

    def test_no_warnings_empty_dict(self):
        self.assertEqual(self.f("=== Page 1 ===\n  Done: pages/page-01.png\n"), {})

    def test_cover_long_mode_page_number_one(self):
        stdout = "TEXT-OVERFLOW: page-01-long.png — configured 96px, used 22px, 1 line(s) dropped\n"
        result = self.f(stdout)
        self.assertEqual(list(result.keys()), [1])


class TestResolveLayoutFlags(unittest.TestCase):
    """render_book.resolve_layout_flags — precedence and omission (PER-104)."""

    def setUp(self):
        import render_book
        self.render_book = render_book

    def test_no_layout_no_font_size_emits_nothing(self):
        self.assertEqual(self.render_book.resolve_layout_flags({}, {}), [])

    def test_book_layout_font_size_only(self):
        flags = self.render_book.resolve_layout_flags({"layout": {"font_size": 44}}, {})
        self.assertEqual(flags, ["--font-size", "44"])

    def test_page_font_size_overrides_book(self):
        flags = self.render_book.resolve_layout_flags(
            {"layout": {"font_size": 44}}, {"font_size": 96}
        )
        self.assertEqual(flags, ["--font-size", "96"])

    def test_cli_font_size_overrides_page_and_book(self):
        flags = self.render_book.resolve_layout_flags(
            {"layout": {"font_size": 44}}, {"font_size": 96}, cli_font_size=12
        )
        self.assertEqual(flags, ["--font-size", "12"])

    def test_absent_font_size_inherits_overlay_text_default(self):
        # No book layout.font_size, no page font_size, no CLI -> no --font-size
        # flag at all, so overlay_text.py's own default silently applies.
        flags = self.render_book.resolve_layout_flags({"layout": {"radius": 10}}, {})
        self.assertNotIn("--font-size", flags)
        self.assertIn("--radius", flags)

    def test_padding_maps_to_pad_h_pad_v(self):
        flags = self.render_book.resolve_layout_flags(
            {"layout": {"padding": {"h": 30, "v": 55}}}, {}
        )
        self.assertIn("--pad-h", flags)
        self.assertIn("30", flags)
        self.assertIn("--pad-v", flags)
        self.assertIn("55", flags)


if __name__ == "__main__":
    unittest.main()
