#!/usr/bin/env bash
# regen.sh — Regenerate all pip-storm fixture artifacts from scratch.
#
# Requires: OPENROUTER_API_KEY in env, uv on PATH.
# Cost: ~10 image API calls (1 ref + 1 stylesheet + 4 overlay raw + 4 native).
# Run from the repo root.
#
# To regenerate just one piece, delete the relevant output and re-run:
#   rm tests/fixtures/pip-storm/refs/pip-ref.png && uv run tests/gen_ref.py
#   rm tests/fixtures/pip-storm/style-sheet.png && (cd … && uv run …/make_style_sheet.py …)
#   rm tests/fixtures/pip-storm/pages/page-0N.png && (cd … && uv run …/render_book.py … --only N)
#
# IMPORTANT: After regeneration, normalize story.json paths to relative values
# before committing — see step (5) in this file.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="$REPO_ROOT/tests/fixtures/pip-storm"
STYLESHEET_SCRIPT="$REPO_ROOT/skills/storybook-stylesheet/scripts/make_style_sheet.py"
RENDER_SCRIPT="$REPO_ROOT/skills/storybook-render/scripts/render_book.py"

echo "=== Step 1: reference character image ==="
uv run "$REPO_ROOT/tests/gen_ref.py"

echo "=== Step 2: style sheet (stage 2) ==="
# Run from fixture dir so relative ref paths in story.json resolve correctly.
(cd "$FIXTURE_DIR" && uv run "$STYLESHEET_SCRIPT" --story story.json)

echo "=== Step 3: overlay pages (stage 3, text_mode=overlay) ==="
(cd "$FIXTURE_DIR" && uv run "$RENDER_SCRIPT" --story story.json --text-mode overlay --resolution 1K)

echo "=== Step 4: native pages (stage 3, text_mode=native) ==="
(cd "$FIXTURE_DIR" && uv run "$RENDER_SCRIPT" --story story.json --text-mode native --resolution 1K)

echo ""
echo "=== Step 5: normalize story.json paths before committing ==="
echo "make_style_sheet.py rewrites style_sheet_path to an absolute path."
echo "Edit story.json manually (or run the sed below) to restore relative paths:"
echo ""
echo "  python3 -c \""
echo "import json, pathlib"
echo "p = pathlib.Path('$FIXTURE_DIR/story.json')"
echo "s = json.loads(p.read_text())"
echo "s['style_sheet_path'] = 'style-sheet.png'"
echo "s['characters'][0]['ref_image'] = 'refs/pip-ref.png'"
echo "s['character_refs'] = ['refs/pip-ref.png']"
echo "p.write_text(json.dumps(s, indent=2, ensure_ascii=False) + '\n')"
echo "\""
echo ""
echo "=== All done. Verify with: ==="
echo "  ls $FIXTURE_DIR/pages/"
echo "  git check-ignore -v $FIXTURE_DIR/pages/page-01.png"
echo "  git status $FIXTURE_DIR"
