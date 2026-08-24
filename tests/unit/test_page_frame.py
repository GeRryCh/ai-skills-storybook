"""Unit tests for PER-105: the deterministic page frame (border).

Zero API calls. Verifies:
  - art_rect(): the artwork rectangle a border insets — full canvas when absent,
    correctly inset and never collapsing the art when present
  - BorderSettings.scaled() / ShadowSettings: reference-unit scaling, same
    uniform factor as LayoutSettings, shadow=None stays None
  - frame_canvas(): margin colour, artwork placement, corner rounding, and the
    no-shadow-means-no-shadow-layer case
  - end-to-end overlay()/text_page() with border=None: byte-identical to the
    same call with no border argument at all (the pre-PER-105 default path)
  - render_book.resolve_border_flags(): presence-is-the-switch semantics,
    per-key omission, page-level 'none' opt-out
  - story_schema.json's 'border' defaults stay in sync with overlay_text.py's
    own DEFAULT_BORDER_*/DEFAULT_SHADOW_* constants (same duplicated-constant
    discipline as TestLayoutDefaultsInSync in test_text_layout.py)
"""
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


class TestArtRect(unittest.TestCase):

    def test_no_border_is_full_canvas(self):
        self.assertEqual(ot.art_rect(2048, 2048, None), (0, 0, 2048, 2048))

    def test_zero_width_is_full_canvas(self):
        border = ot.BorderSettings(width=0).scaled(2048, 2048, ot.REFERENCE_SIZE)
        self.assertEqual(ot.art_rect(2048, 2048, border), (0, 0, 2048, 2048))

    def test_positive_width_insets_all_four_sides_equally(self):
        border = ot.BorderSettings(width=64).scaled(2048, 2048, ot.REFERENCE_SIZE)
        x0, y0, x1, y1 = ot.art_rect(2048, 2048, border)
        self.assertEqual((x0, y0), (64, 64))
        self.assertEqual((2048 - x1, 2048 - y1), (64, 64))

    def test_never_collapses_the_art(self):
        # A pathologically large width must still leave >0px of artwork.
        border = ot.BorderSettings(width=100_000).scaled(200, 200, ot.REFERENCE_SIZE)
        x0, y0, x1, y1 = ot.art_rect(200, 200, border)
        self.assertGreater(x1 - x0, 0)
        self.assertGreater(y1 - y0, 0)

    def test_non_square_canvas_insets_independently(self):
        # reference_size = min(w, h) so scale is exactly 1 — isolates art_rect's
        # own geometry from BorderSettings.scaled()'s separate scale factor.
        border = ot.BorderSettings(width=32).scaled(2048, 1365, 1365)
        x0, y0, x1, y1 = ot.art_rect(2048, 1365, border)
        self.assertEqual(x1 - x0, 2048 - 2 * 32)
        self.assertEqual(y1 - y0, 1365 - 2 * 32)


class TestBorderSettingsScaling(unittest.TestCase):

    def test_scale_matches_layout_scale_factor(self):
        # Same uniform factor as LayoutSettings.scaled(): min(w,h)/reference_size.
        border = ot.BorderSettings(width=64, radius=48).scaled(1024, 1024, 2048)
        self.assertEqual(border.width, 32)   # 64 * (1024/2048)
        self.assertEqual(border.radius, 24)  # 48 * (1024/2048)

    def test_color_is_never_scaled(self):
        border = ot.BorderSettings(color="#FFEDC7").scaled(1024, 1024, 2048)
        self.assertEqual(border.color, "#FFEDC7")

    def test_shadow_none_stays_none(self):
        border = ot.BorderSettings(shadow=None).scaled(1024, 1024, 2048)
        self.assertIsNone(border.shadow)

    def test_shadow_offset_and_blur_scale_opacity_does_not(self):
        shadow = ot.ShadowSettings(offset=12, blur=24, opacity=0.25)
        border = ot.BorderSettings(shadow=shadow).scaled(1024, 1024, 2048)
        self.assertEqual(border.shadow.offset, 6)
        self.assertEqual(border.shadow.blur, 12)
        self.assertEqual(border.shadow.opacity, 0.25)

    def test_shares_layout_reference_size_not_its_own(self):
        # scaled() takes reference_size as an explicit argument (from the book's
        # 'layout' object) rather than carrying one of its own.
        border = ot.BorderSettings(width=100).scaled(512, 512, 1000)
        self.assertEqual(border.width, 51)  # round(100 * 512/1000)


class TestFrameCanvas(unittest.TestCase):
    """reference_size == min(canvas_w, canvas_h) throughout, so .scaled() is a
    no-op (scale=1) and every hand-picked pixel coordinate lines up exactly
    with the unscaled BorderSettings values used to build it."""

    CANVAS = (512, 512)

    def _art(self, w, h, color=(200, 50, 50, 255)):
        return Image.new("RGBA", (w, h), color)

    def _framed(self, border, art_color=(0, 0, 0, 255)):
        w, h = self.CANVAS
        scaled = border.scaled(w, h, min(w, h))
        rect = ot.art_rect(w, h, scaled)
        aw, ah = rect[2] - rect[0], rect[3] - rect[1]
        art = self._art(aw, ah, color=art_color)
        return ot.frame_canvas(art, w, h, rect, scaled), rect

    def test_output_size_matches_canvas_not_art(self):
        canvas, _ = self._framed(ot.BorderSettings(width=64))
        self.assertEqual(canvas.size, self.CANVAS)

    def test_margin_corner_pixel_is_border_color(self):
        canvas, _ = self._framed(ot.BorderSettings(width=64, color="#FFEDC7", radius=0))
        # Top-left canvas corner is pure margin — far from both the art and any
        # rounded-corner falloff at radius=0.
        r, g, b, a = canvas.getpixel((2, 2))
        self.assertEqual((r, g, b), (0xFF, 0xED, 0xC7))

    def test_art_center_pixel_is_art_color(self):
        canvas, _ = self._framed(ot.BorderSettings(width=64, radius=0), art_color=(10, 20, 30, 255))
        r, g, b, a = canvas.getpixel((256, 256))
        self.assertEqual((r, g, b), (10, 20, 30))

    def test_no_shadow_does_not_darken_margin(self):
        border = ot.BorderSettings(width=64, color="#FFFFFF", radius=0, shadow=None)
        canvas, rect = self._framed(border)
        # Just past the art's bottom-right edge, where a shadow (if any) would
        # be darkest — must still read as pure white with no shadow configured.
        px = (rect[2] + 4, rect[3] + 4)
        r, g, b, a = canvas.getpixel(px)
        self.assertEqual((r, g, b), (255, 255, 255))

    def test_shadow_darkens_margin_near_art_edge(self):
        shadow = ot.ShadowSettings(offset=8, blur=4, opacity=0.6)
        border = ot.BorderSettings(width=64, color="#FFFFFF", radius=0, shadow=shadow)
        canvas, rect = self._framed(border)
        # A few px past the art's bottom-right edge, inside the shadow rect
        # (offset 8 past that same edge) but not covered by the art itself.
        px = (rect[2] + 4, rect[3] + 4)
        r, g, b, a = canvas.getpixel(px)
        self.assertLess(r, 255, "pixel near the shadow offset should be darkened")


class TestNoBorderByteIdentity(unittest.TestCase):
    """The load-bearing invariant: border=None (the default) must be provably
    identical to calling overlay()/text_page() without the border argument at
    all — no resampling, no frame step, byte-for-byte the same file."""

    def test_overlay_border_none_matches_no_border_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.png"
            Image.new("RGB", (600, 400), (120, 140, 160)).save(src)
            out_a = Path(tmp) / "a.png"
            out_b = Path(tmp) / "b.png"
            ot.overlay(src, "Once upon a time.", "bottom", out_a)
            ot.overlay(src, "Once upon a time.", "bottom", out_b, border=None)
            self.assertEqual(out_a.read_bytes(), out_b.read_bytes())

    def test_overlay_explicit_zero_width_border_matches_no_border(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.png"
            Image.new("RGB", (600, 400), (120, 140, 160)).save(src)
            out_a = Path(tmp) / "a.png"
            out_b = Path(tmp) / "b.png"
            ot.overlay(src, "Once upon a time.", "bottom", out_a)
            ot.overlay(src, "Once upon a time.", "bottom", out_b, border=ot.BorderSettings(width=0))
            self.assertEqual(out_a.read_bytes(), out_b.read_bytes())

    def test_text_page_border_none_matches_no_border_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            bg = Path(tmp) / "bg.png"
            Image.new("RGB", (600, 400), (200, 200, 200)).save(bg)
            out_a = Path(tmp) / "a.png"
            out_b = Path(tmp) / "b.png"
            ot.text_page(bg, "Some story text.", out_a)
            ot.text_page(bg, "Some story text.", out_b, border=None)
            self.assertEqual(out_a.read_bytes(), out_b.read_bytes())

    def test_bordered_output_differs_and_is_larger_margin_area(self):
        """Sanity check the opposite direction: setting a border DOES change
        the composited bytes (guards against a no-op wiring bug)."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.png"
            Image.new("RGB", (600, 400), (120, 140, 160)).save(src)
            out_plain = Path(tmp) / "plain.png"
            out_framed = Path(tmp) / "framed.png"
            ot.overlay(src, "Once upon a time.", "bottom", out_plain)
            ot.overlay(
                src, "Once upon a time.", "bottom", out_framed,
                border=ot.BorderSettings(width=40, color="#FFEDC7"),
            )
            self.assertNotEqual(out_plain.read_bytes(), out_framed.read_bytes())


class TestResolveBorderFlags(unittest.TestCase):
    """render_book.resolve_border_flags — presence-is-the-switch, per-key
    omission, page-level 'none' opt-out (PER-105)."""

    def setUp(self):
        import render_book
        self.render_book = render_book

    def test_no_border_emits_nothing(self):
        self.assertEqual(self.render_book.resolve_border_flags({}), [])

    def test_empty_border_object_still_enables_frame(self):
        # Presence (even {}) is the switch — every value falls back to
        # overlay_text.py's own defaults via the bare --frame flag.
        flags = self.render_book.resolve_border_flags({"border": {}})
        self.assertEqual(flags, ["--frame"])

    def test_book_border_with_width_only(self):
        flags = self.render_book.resolve_border_flags({"border": {"width": 90}})
        self.assertIn("--frame", flags)
        self.assertIn("--border-width", flags)
        self.assertIn("90", flags)
        self.assertNotIn("--border-color", flags)
        self.assertNotIn("--border-radius", flags)

    def test_full_border_with_shadow(self):
        story = {
            "border": {
                "width": 64, "color": "#FFEDC7", "radius": 48,
                "shadow": {"offset": 12, "blur": 24, "opacity": 0.25},
            }
        }
        flags = self.render_book.resolve_border_flags(story)
        for pair in (
            ("--border-width", "64"), ("--border-color", "#FFEDC7"),
            ("--border-radius", "48"), ("--shadow-offset", "12"),
            ("--shadow-blur", "24"), ("--shadow-opacity", "0.25"),
        ):
            self.assertIn(pair[0], flags)
            self.assertIn(pair[1], flags)
        self.assertIn("--shadow", flags)

    def test_shadow_absent_no_shadow_flags(self):
        flags = self.render_book.resolve_border_flags({"border": {"width": 64}})
        self.assertNotIn("--shadow", flags)
        self.assertNotIn("--shadow-offset", flags)

    def test_page_none_opts_out_even_with_book_border(self):
        story = {"border": {"width": 64}}
        flags = self.render_book.resolve_border_flags(story, {"border": "none"})
        self.assertEqual(flags, [])

    def test_page_unset_inherits_book_border(self):
        story = {"border": {"width": 64}}
        flags = self.render_book.resolve_border_flags(story, {})
        self.assertIn("--frame", flags)

    def test_page_none_with_no_book_border_is_still_empty(self):
        flags = self.render_book.resolve_border_flags({}, {"border": "none"})
        self.assertEqual(flags, [])


class TestRunFrameForwardsLayoutFlags(unittest.IsolatedAsyncioTestCase):
    """render_book.run_frame() must forward layout_flags alongside
    border_flags, exactly like run_overlay/run_text_page (regression guard:
    an earlier version only forwarded border_flags, so a book overriding
    layout.reference_size scaled its border differently on native/long-body-
    art pages — the frame-only surfaces — than on overlay/text-page surfaces,
    reintroducing the exact page-to-page inconsistency PER-105 exists to
    remove)."""

    async def test_layout_flags_reach_the_subprocess_argv(self):
        import render_book

        captured = {}

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def _fake_create_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeProc()

        import asyncio
        import unittest.mock as mock

        with mock.patch.object(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec):
            ok = await render_book.run_frame(
                Path("/tmp/raw.png"), Path("/tmp/final.png"),
                ["--reference-size", "1024"], ["--frame", "--border-width", "64"],
                [],
            )
        self.assertTrue(ok)
        cmd = captured["cmd"]
        self.assertIn("--reference-size", cmd)
        self.assertEqual(cmd[cmd.index("--reference-size") + 1], "1024")
        self.assertIn("--border-width", cmd)

    async def test_no_border_flags_skips_subprocess_entirely(self):
        # The byte-identity fast path (plain file copy) must never touch
        # asyncio.create_subprocess_exec at all -- confirms layout_flags
        # forwarding doesn't accidentally defeat the no-op copy.
        import render_book
        import asyncio
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw.png"
            raw.write_bytes(b"fake-png-bytes")
            final = Path(td) / "final.png"
            with mock.patch.object(asyncio, "create_subprocess_exec", side_effect=AssertionError("should not be called")):
                ok = await render_book.run_frame(raw, final, ["--reference-size", "1024"], [], [])
            self.assertTrue(ok)
            self.assertEqual(final.read_bytes(), raw.read_bytes())


class TestBorderDefaultsInSync(unittest.TestCase):
    """story_schema.json's 'border' defaults (read by editor.html for its
    placeholders — never hard-coded there) must equal overlay_text.py's own
    DEFAULT_BORDER_*/DEFAULT_SHADOW_* constants. Same duplicated-constant
    discipline as TestLayoutDefaultsInSync in test_text_layout.py — see
    .claude/rules/script-authoring.md."""

    def setUp(self):
        schema = edit_story.load_schema()["properties"]["border"]["properties"]
        self.border_schema = schema
        self.shadow_schema = schema["shadow"]["properties"]

    def test_width(self):
        self.assertEqual(self.border_schema["width"]["default"], ot.DEFAULT_BORDER_WIDTH)

    def test_color(self):
        self.assertEqual(self.border_schema["color"]["default"], ot.DEFAULT_BORDER_COLOR)

    def test_radius(self):
        self.assertEqual(self.border_schema["radius"]["default"], ot.DEFAULT_BORDER_RADIUS)

    def test_shadow_offset(self):
        self.assertEqual(self.shadow_schema["offset"]["default"], ot.DEFAULT_SHADOW_OFFSET)

    def test_shadow_blur(self):
        self.assertEqual(self.shadow_schema["blur"]["default"], ot.DEFAULT_SHADOW_BLUR)

    def test_shadow_opacity(self):
        self.assertEqual(self.shadow_schema["opacity"]["default"], ot.DEFAULT_SHADOW_OPACITY)


if __name__ == "__main__":
    unittest.main()
