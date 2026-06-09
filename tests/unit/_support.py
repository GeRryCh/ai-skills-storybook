"""
Shared test support: sys.path setup + fixture helpers.
Import this in every unit test file BEFORE importing scripts under test.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

_SCRIPT_DIRS = [
    REPO_ROOT / "skills" / "storybook-render" / "scripts",
    REPO_ROOT / "skills" / "storybook-story" / "scripts",
    REPO_ROOT / "skills" / "storybook-stylesheet" / "scripts",
]
for _d in _SCRIPT_DIRS:
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)


def fixture_story(name: str) -> dict:
    """Return a parsed copy of the named fixture's story.json."""
    with (FIXTURES / name / "story.json").open(encoding="utf-8") as f:
        return json.load(f)


def fixture_dir(name: str) -> Path:
    """Return the Path to the named fixture directory."""
    return FIXTURES / name
