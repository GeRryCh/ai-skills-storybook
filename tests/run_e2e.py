#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///
"""
Run the e2e browser test suite against the local editor server.

Requires Chromium to be installed once:
  uv run playwright install chromium

Usage:
  uv run tests/run_e2e.py                                    # all e2e tests
  uv run tests/run_e2e.py tests/e2e/test_mention_picker.py   # one file
  uv run tests/run_e2e.py tests/e2e/test_cast_derivation.py tests/e2e/test_save_roundtrip.py
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = REPO_ROOT / "tests" / "e2e"

if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))


def _check_chromium() -> bool:
    """Return True if Chromium can be launched; print install hint and return False otherwise."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception as exc:
        msg = str(exc)
        if any(kw in msg for kw in ("Executable doesn't exist", "not found", "install")):
            print(
                "\nChromium is not installed. Run this once then retry:\n"
                "  uv run playwright install chromium\n",
                file=sys.stderr,
            )
        else:
            print(f"\nChromium launch failed: {exc}\n", file=sys.stderr)
        return False


def main() -> None:
    if not _check_chromium():
        sys.exit(1)

    loader = unittest.TestLoader()

    if len(sys.argv) > 1:
        suite = unittest.TestSuite()
        for arg in sys.argv[1:]:
            module_name = Path(arg).stem
            suite.addTests(loader.loadTestsFromName(module_name))
    else:
        suite = loader.discover(str(E2E_DIR), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
