---
paths:
  - "skills/storybook-story/scripts/edit_story.py"
  - "skills/storybook-story/assets/editor.html"
---

# Editor behavioral contract

## Round-trip contract

- Preserve unknown keys at **all** levels of `story.json` — the editor must never drop fields it doesn't recognise.
- Optional fields must **never be materialised** when absent — a no-edit save must be semantically stable. Accepting a default ≠ writing the default value into the file.
- Use `json.dump(indent=2, ensure_ascii=False)` — same formatter as `make_style_sheet.py`.
- Validate against `story_schema.json` before writing, with separate error (blocking) vs. warning (non-blocking) tiers.
- 409 conflict guard: if `story.json` changes on disk while the editor is open, the save returns an error and a Reload button rather than silently clobbering the new content.

## Full-quiescence gate (409)

Any running job (consolidation, regen-all, any page render, any sheet regen) blocks sheet-regenerate and sheet-select calls. Reason: `make_style_sheet.py` rewrites `story.json` on completion; concurrent jobs would corrupt each other's write. Same gate applies to page-mutating endpoints while consolidation runs.

## Lock discipline

`_consolidate_job` (module-level dict) is always mutated in place under `_regen_lock` (same lock as `_regen_jobs`). The worker thread **never** holds the lock across `subprocess.run` — status polls (also locked) would deadlock otherwise.

## Adopt-on-mutate invariant

Before any mutating op (regenerate, select) touches the canonical slot, the current canonical is copied into history if its hash is not already present. GETs (`/api/versions`, `/api/sheet/versions`) are pure — no disk mutation on read.

## Per-page bg preserved across regens

`page-NN-long-bg.png` is copied to history for completeness but **never deleted** by regenerate, so `render_book.py`'s `if not bg_path.exists()` guard reuses it — no surprise extra paid bg call.

## Never say "reversible" for long body text pages

Long-body text-page archival gap (pre-existing): history identity = preview hash = the art page; a text-page recomposite whose art hash is already in history archives nothing before overwriting. Do not use "reversible" in UI copy for long body.

## Flush before regenerate

Any regenerate/consolidate action must flush unsaved edits first (same dirty-check as the page regenerate button) so scripts read the latest `story.json`.
