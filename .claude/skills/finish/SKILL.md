---
name: finish
description: >
  Dispatch a fire-and-forget background agent to finish the current task worktree:
  rebases the task branch onto main, fast-forwards main, removes the worktree,
  deletes the branch, and marks the Linear issue done. Use when invoked as
  `/finish` (no arg — derives task id from the current branch) or `/finish PER-X`,
  or when the user says "finish this worktree", "land this task", "wrap up and dispatch",
  "close out this worktree". Requires the session to be running inside a task worktree
  created by scripts/new-worktree.sh. Spawns a detached background agent via
  scripts/finish-worktree.sh (which runs /finish-worktree under the hood).
---

# Finish (dispatcher)

Dispatch a detached background agent to integrate and clean up the current task worktree.
The argument `$ARGUMENTS` is an optional Linear task id override (e.g. `PER-8`).
Run the steps **in order**. If a STOP condition is hit, stop and report — do not proceed.

## Steps

1. **Derive task id.**
   - If `$ARGUMENTS` is non-empty and matches `^[A-Z][A-Z0-9]*-[0-9]+` → use it as `TASK_ID`.
   - Otherwise run `git branch --show-current` and extract the leading id with regex
     `^([A-Z][A-Z0-9]*-[0-9]+)` (the branch naming convention from new-worktree.sh is
     `<TASK-ID>-<kebab-title>`).
   - No match in either path → STOP: "No task id found. Not a task worktree, or provide
     the id explicitly: `/finish PER-X`."

2. **Pre-flight: clean worktree check.**
   ```
   git status --porcelain
   ```
   Any output (staged, modified, or untracked files) → STOP: show the output and ask
   the user whether to commit, stash, or discard before finishing. Do NOT proceed —
   the background agent's finish-worktree skill will also stop on dirty, but it can't
   interactively ask the user from a detached session.

3. **Locate the finish script.**
   ```
   ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
   FINISH_SCRIPT="$ROOT/scripts/finish-worktree.sh"
   ```
   If `$FINISH_SCRIPT` does not exist → STOP: "scripts/finish-worktree.sh not found at
   `$ROOT/scripts/`. This repo may not use the bare+worktree layout."

4. **Dispatch the background agent.**
   ```bash
   bash "$FINISH_SCRIPT" "$TASK_ID"
   ```
   The script starts a detached background session named `<id-lc>:finish` that does the
   actual finishing work, so it returns quickly. Wait for the Bash call to return
   (a few seconds at most) before reporting.

5. **Report.**
   One-liner summary:
   - Dispatched background session `<task-id-lc>:finish`.
   - This worktree directory will be deleted by it shortly.
   - End this conversation when ready; monitor with `claude agents` or `claude attach <id>`.
