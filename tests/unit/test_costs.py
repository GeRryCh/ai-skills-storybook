"""Unit tests for PER-35 cost-accounting helpers.

Tests the pure pricing math and ledger I/O added to render_book.py and
make_style_sheet.py:
  - gemini_call_cost(usage_metadata, model) -> (usd, tokens, estimated)  [render_book.py only]
  - openai_call_cost(usage, model) -> (usd, tokens, estimated)           [both scripts]
  - append_cost_record / read_cost_ledger / summarize_cost_records / format_cost_line
    [both scripts — identical copies, tested against both to guard drift]

Zero-API rule: no test may call Gemini or OpenAI. All response objects are
SimpleNamespace fakes; no network I/O.
"""
import json
import sys
import tempfile
import types as pytypes
import unittest
from enum import Enum
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _support  # noqa: F401 — populates sys.path with script dirs

import make_style_sheet
import render_book

COST_MODULES = (render_book, make_style_sheet)


# ---------------------------------------------------------------------------
# Fake response builders
# ---------------------------------------------------------------------------

class FakeMediaModality(str, Enum):
    """Stand-in for google.genai.types.MediaModality.

    Must stay a `str`-mixin Enum, exactly like the real class — that mixin is the
    whole point. Python 3.11 made Enum.__str__ win over it, so `str(member)`
    returns 'FakeMediaModality.IMAGE', not 'IMAGE'. gemini_call_cost read the
    modality with str() and therefore matched nothing, zeroing every image-output
    token in production (PER-98) while these tests passed against bare-string
    fakes. Fakes must carry the enum, never a plain 'IMAGE' string.
    """

    IMAGE = "IMAGE"
    TEXT = "TEXT"


def _fake_usage_metadata(prompt=0, candidates=0, thoughts=0, details=None):
    """Build a fake Gemini GenerateContentResponseUsageMetadata.

    `details`, when given, is a list of (modality, token_count) pairs. A plain
    string modality is upgraded to FakeMediaModality so every detail-based test
    exercises the enum the API actually returns; pass a raw string explicitly
    (see test_modality_accepts_plain_string) to cover the loose path.
    """
    d = None
    if details is not None:
        d = [
            pytypes.SimpleNamespace(
                modality=FakeMediaModality(m) if isinstance(m, str) else m,
                token_count=t,
            )
            for m, t in details
        ]
    return pytypes.SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        candidates_tokens_details=d,
        thoughts_token_count=thoughts,
    )


def _fake_openai_usage(text_in=0, image_in=0, output=0):
    """Build a fake OpenAI images.edit/generate response `usage` object."""
    return pytypes.SimpleNamespace(
        input_tokens_details=pytypes.SimpleNamespace(text_tokens=text_in, image_tokens=image_in),
        output_tokens=output,
        total_tokens=text_in + image_in + output,
    )


# ---------------------------------------------------------------------------
# gemini_call_cost (render_book.py only — make_style_sheet.py never calls Gemini)
# ---------------------------------------------------------------------------

class TestGeminiCallCost(unittest.TestCase):

    def test_flash_1k_image_matches_googles_published_figure(self):
        """1120 image-output tokens on flash == Google's published $0.067/1K-image."""
        um = _fake_usage_metadata(prompt=0, candidates=1120, details=[("IMAGE", 1120)])
        usd, tokens, estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        self.assertAlmostEqual(usd, 0.0672, places=6)
        self.assertEqual(tokens["output_image"], 1120)
        self.assertFalse(estimated)

    def test_pro_1k_2k_image_matches_googles_published_figure(self):
        """1120 image-output tokens on pro == Google's published $0.134/1K-2K-image."""
        um = _fake_usage_metadata(prompt=0, candidates=1120, details=[("IMAGE", 1120)])
        usd, _tokens, estimated = render_book.gemini_call_cost(um, "gemini-3-pro-image")
        self.assertAlmostEqual(usd, 0.1344, places=6)
        self.assertFalse(estimated)

    def test_input_tokens_priced_at_input_rate(self):
        um = _fake_usage_metadata(prompt=2331, candidates=1680, details=[("IMAGE", 1680)])
        usd, tokens, _estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        expected = round((2331 * 0.50 + 1680 * 60.00) / 1_000_000, 6)
        self.assertEqual(usd, expected)
        self.assertEqual(tokens["input"], 2331)

    def test_thoughts_billed_at_output_text_rate(self):
        """Pro model's thinking-step tokens bill at the text/thinking output rate."""
        um = _fake_usage_metadata(prompt=0, candidates=1120, thoughts=1000, details=[("IMAGE", 1120)])
        usd, tokens, _estimated = render_book.gemini_call_cost(um, "gemini-3-pro-image")
        expected = (1120 * 120.00 + 1000 * 12.00) / 1_000_000
        self.assertAlmostEqual(usd, expected, places=6)
        self.assertEqual(tokens["thoughts"], 1000)

    def test_text_and_image_output_split_when_details_present(self):
        um = _fake_usage_metadata(prompt=0, candidates=1200, details=[("IMAGE", 1120), ("TEXT", 80)])
        usd, tokens, estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        self.assertEqual(tokens["output_image"], 1120)
        self.assertEqual(tokens["output_text"], 80)
        expected = (1120 * 60.00 + 80 * 3.00) / 1_000_000
        self.assertAlmostEqual(usd, expected, places=6)
        self.assertFalse(estimated)

    def test_modality_read_by_value_not_str(self):
        """PER-98 regression. MediaModality is a str-mixin Enum; Python 3.11 made
        Enum.__str__ win over the mixin, so str(member) is 'X.IMAGE', not 'IMAGE'.
        Reading it with str() matched nothing, zeroed image_tokens, and priced
        every generated image at $0 while still reporting estimated=False."""
        entry = pytypes.SimpleNamespace(
            modality=FakeMediaModality.IMAGE, token_count=1120
        )
        # The behaviour that broke it — kept here so the cause stays legible.
        self.assertNotEqual(str(FakeMediaModality.IMAGE).upper(), "IMAGE")
        self.assertEqual(render_book._modality_name(entry), "IMAGE")

    def test_modality_accepts_plain_string(self):
        """The .value lookup must fall back to the raw object, so a loosely-built
        response (or an SDK that switches to plain strings) still prices."""
        entry = pytypes.SimpleNamespace(modality="image", token_count=1120)
        self.assertEqual(render_book._modality_name(entry), "IMAGE")

    def test_modality_missing_attribute_is_empty(self):
        self.assertEqual(render_book._modality_name(pytypes.SimpleNamespace()), "")

    def test_real_sdk_modality_enum_is_read_correctly(self):
        """Guard against the installed google-genai changing the enum's shape.
        Import only — no client, no network (zero-API rule)."""
        try:
            from google.genai.types import MediaModality
        except ImportError:
            self.skipTest("google-genai not installed")
        entry = pytypes.SimpleNamespace(modality=MediaModality.IMAGE, token_count=1680)
        self.assertEqual(render_book._modality_name(entry), "IMAGE")

    def test_image_output_is_priced_not_zeroed(self):
        """End-to-end guard on the symptom PER-98 produced: a successful 1K flash
        render was recorded at input-only cost with estimated=False, so it read as
        exact and nearly free. 1120 image tokens must cost $0.0672, not ~$0.00075."""
        um = _fake_usage_metadata(prompt=1501, candidates=1501, details=[("IMAGE", 1120)])
        usd, tokens, estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        self.assertEqual(tokens["output_image"], 1120)
        self.assertFalse(estimated)
        input_only = 1501 * 0.50 / 1_000_000
        self.assertGreater(usd, input_only * 50)
        self.assertEqual(usd, round((1501 * 0.50 + 1120 * 60.00) / 1_000_000, 6))

    def test_missing_details_falls_back_to_estimated_image_rate(self):
        """No candidates_tokens_details -> whole candidates_token_count treated as
        image output, and estimated=True so callers can flag the figure."""
        um = _fake_usage_metadata(prompt=0, candidates=1120, details=None)
        usd, tokens, estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        self.assertTrue(estimated)
        self.assertEqual(tokens["output_image"], 1120)
        self.assertEqual(tokens["output_text"], 0)
        self.assertAlmostEqual(usd, 0.0672, places=6)

    def test_blocked_response_with_no_candidates_is_not_estimated(self):
        """A PROHIBITED_CONTENT/SAFETY/RECITATION block has candidates_token_count=0
        and no details -> nothing was inferred (there was no output to split), so
        estimated must stay False. Regression guard: this used to be hardcoded True,
        which would permanently hedge a book's whole cost total behind one blocked
        page even though every other call had an exact modality split."""
        um = _fake_usage_metadata(prompt=500, candidates=0, details=None)
        usd, tokens, estimated = render_book.gemini_call_cost(um, "gemini-3.1-flash-image")
        self.assertFalse(estimated)
        self.assertEqual(tokens["output_image"], 0)
        expected = round(500 * 0.50 / 1_000_000, 6)
        self.assertEqual(usd, expected)

    def test_unknown_model_returns_none_usd_but_keeps_tokens(self):
        um = _fake_usage_metadata(prompt=0, candidates=1120, details=[("IMAGE", 1120)])
        usd, tokens, estimated = render_book.gemini_call_cost(um, "some-future-model")
        self.assertIsNone(usd)
        self.assertEqual(tokens["output_image"], 1120)
        self.assertFalse(estimated)  # details were present; only the price is missing

    def test_none_usage_metadata_returns_none_and_empty_tokens(self):
        usd, tokens, estimated = render_book.gemini_call_cost(None, "gemini-3.1-flash-image")
        self.assertIsNone(usd)
        self.assertEqual(tokens, {})
        self.assertFalse(estimated)


# ---------------------------------------------------------------------------
# openai_call_cost — identical in both scripts, tested against both
# ---------------------------------------------------------------------------

class TestOpenAICallCost(unittest.TestCase):

    def test_gpt_image_2_cost(self):
        usage = _fake_openai_usage(text_in=100, image_in=1500, output=1056)
        expected = (100 * 5.00 + 1500 * 8.00 + 1056 * 30.00) / 1_000_000
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                usd, tokens, estimated = mod.openai_call_cost(usage, "gpt-image-2")
                self.assertAlmostEqual(usd, expected, places=6)
                self.assertEqual(tokens, {"input_text": 100, "input_image": 1500, "output": 1056})
                self.assertFalse(estimated)

    def test_unknown_model_returns_none_usd(self):
        usage = _fake_openai_usage(text_in=10, image_in=10, output=10)
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                usd, _tokens, _estimated = mod.openai_call_cost(usage, "gpt-image-3-hypothetical")
                self.assertIsNone(usd)

    def test_none_usage_returns_none_and_empty_tokens(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                usd, tokens, estimated = mod.openai_call_cost(None, "gpt-image-2")
                self.assertIsNone(usd)
                self.assertEqual(tokens, {})
                self.assertFalse(estimated)


# ---------------------------------------------------------------------------
# append_cost_record / read_cost_ledger / summarize_cost_records / format_cost_line
# ---------------------------------------------------------------------------

class TestLedgerRoundTrip(unittest.TestCase):

    def setUp(self):
        # Both scripts' run-level tallies are module-level lists; clear between
        # tests so one test's records don't leak into another's assertions.
        render_book._RUN_COST_RECORDS.clear()
        make_style_sheet._RUN_COST_RECORDS.clear()

    def test_append_cost_record_writes_one_jsonl_line_and_updates_run_tally(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as td:
                ledger_path = Path(td) / "costs.jsonl"
                mod.append_cost_record(
                    ledger_path,
                    script=mod.__name__ + ".py",
                    vendor="gemini",
                    model="gemini-3.1-flash-image",
                    target=Path("raw-page-01.png"),
                    ok=True,
                    usd=0.0672,
                    estimated=False,
                    tokens={"input": 0, "output_image": 1120, "output_text": 0, "thoughts": 0},
                )
                lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                rec = json.loads(lines[0])
                self.assertEqual(rec["vendor"], "gemini")
                self.assertEqual(rec["target"], "raw-page-01.png")
                self.assertEqual(rec["usd"], 0.0672)
                self.assertIn("ts", rec)
                self.assertEqual(rec["pricing_as_of"], mod.PRICING_AS_OF)
                # In-memory run tally sees the same record.
                run_records = mod._RUN_COST_RECORDS
                self.assertEqual(len(run_records), 1)
                self.assertEqual(run_records[0]["usd"], 0.0672)

    def test_append_cost_record_is_append_only(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as td:
                ledger_path = Path(td) / "costs.jsonl"
                for i in range(3):
                    mod.append_cost_record(
                        ledger_path, script="x", vendor="gemini", model="m",
                        target=Path(f"page-{i}.png"), ok=True, usd=0.01, estimated=False,
                        tokens={},
                    )
                lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 3)

    def test_read_cost_ledger_missing_file_returns_empty(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                self.assertEqual(mod.read_cost_ledger(Path("/nonexistent/costs.jsonl")), [])

    def test_read_cost_ledger_skips_malformed_lines(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory() as td:
                ledger_path = Path(td) / "costs.jsonl"
                ledger_path.write_text(
                    '{"usd": 0.1}\nNOT JSON\n{"usd": 0.2}\n\n', encoding="utf-8"
                )
                records = mod.read_cost_ledger(ledger_path)
                self.assertEqual([r["usd"] for r in records], [0.1, 0.2])

    def test_summarize_cost_records_mixed(self):
        records = [
            {"usd": 0.10, "estimated": False},
            {"usd": None, "estimated": False},   # unpriced model
            {"usd": 0.20, "estimated": True},    # estimated split
        ]
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                total, calls, unpriced, estimated = mod.summarize_cost_records(records)
                self.assertAlmostEqual(total, 0.30, places=6)
                self.assertEqual(calls, 3)
                self.assertEqual(unpriced, 1)
                self.assertTrue(estimated)

    def test_summarize_cost_records_empty(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                self.assertEqual(mod.summarize_cost_records([]), (0.0, 0, 0, False))

    def test_format_cost_line_plain(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                line = mod.format_cost_line("Cost this run", 4.17, 41, 0, False)
                self.assertEqual(line, "Cost this run: $4.17  (41 calls)")

    def test_format_cost_line_singular_call(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                line = mod.format_cost_line("Cost this run", 0.10, 1, 0, False)
                self.assertIn("(1 call)", line)

    def test_format_cost_line_unpriced_suffix(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                line = mod.format_cost_line("Cost this run", 4.17, 41, 2, False)
                self.assertIn("2 unpriced", line)

    def test_format_cost_line_estimated_prefix(self):
        for mod in COST_MODULES:
            with self.subTest(module=mod.__name__):
                line = mod.format_cost_line("Cost this run", 4.17, 41, 0, True)
                self.assertTrue(line.startswith("Cost this run: ~$4.17"))


# ---------------------------------------------------------------------------
# Cross-script sync guard — the two PRICING tables must never drift.
# ---------------------------------------------------------------------------

class TestPricingTablesInSync(unittest.TestCase):

    def test_pricing_dicts_match(self):
        self.assertEqual(render_book.PRICING, make_style_sheet.PRICING)

    def test_pricing_as_of_matches(self):
        self.assertEqual(render_book.PRICING_AS_OF, make_style_sheet.PRICING_AS_OF)


if __name__ == "__main__":
    unittest.main()
