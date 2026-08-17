#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""
Run the unit test suite (zero API key, zero browser).

Usage:
  uv run tests/run_unit.py                               # all unit tests
  uv run tests/run_unit.py tests/unit/test_select_refs.py  # one file
  uv run tests/run_unit.py tests/unit/test_select_refs.py tests/unit/test_validate_story.py
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_DIR = REPO_ROOT / "tests" / "unit"

# Allow test files to do `import _support`
if str(UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(UNIT_DIR))


def main() -> None:
    loader = unittest.TestLoader()

    if len(sys.argv) > 1:
        # Subset: file paths given as arguments
        suite = unittest.TestSuite()
        for arg in sys.argv[1:]:
            module_name = Path(arg).stem   # e.g. "test_select_refs"
            suite.addTests(loader.loadTestsFromName(module_name))
    else:
        suite = loader.discover(str(UNIT_DIR), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
