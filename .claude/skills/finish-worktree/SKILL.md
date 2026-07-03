---
name: finish-worktree
description: >
  Finish a task worktree in a bare-repo + worktree layout: rebase the task branch
  onto main (resolving any merge conflicts), fast-forward main, remove the worktree,
  delete the branch, and mark the Linear issue done. Use when invoked as
  `/finish-worktree <task-id>` (e.g. `/finish-worktree PER-8`) or when the user asks
  to "finish", "wrap up", "land", or "close out" a task worktree/branch identified by
  a Linear task id. Expects exactly one local branch whose name starts with the task id.
---

# Finish Worktree

Integrate a task branch into `main` and clean up. The argument `$ARGUMENTS` is the
Linear task id (e.g. `PER-8`). Run the steps **in order**. If a STOP condition is hit,
stop immediately and report what blocked you — do not force anything.

## Steps

1. **Repo root.** `cd` to the repo root = parent of `git rev-parse --git-common-dir`
   (the dir containing `.bare` and the worktrees). Run all git commands from here.

2. **Resolve the branch.** List candidates:
   ```
   git for-each-ref --format='%(refname:short)' "refs/heads/${ARGUMENTS}*"
   ```
   - 0 matches → STOP: "No branch starting with `${ARGUMENTS}`."
   - >1 matches → STOP: list them and ask which one.
   - Exactly 1 → call it `BRANCH`.

3. **Locate worktrees** from `git worktree list --porcelain`:
   - `WT_DIR` = the worktree whose `branch` is `refs/heads/${BRANCH}`.
   - `MAIN_WT` = the worktree whose `branch` is `refs/heads/main`.
   - If `main` is not checked out in any worktree → STOP: cannot integrate.

4. **Check the worktree is clean.**
   ```
   git -C "$WT_DIR" status --porcelain
   ```
   Any output (modified, staged, or untracked files) → STOP: show the output and ask
   the user whether to commit, stash, or discard before finishing. Do not proceed —
   a dirty tree would either break the rebase (step 5) or block worktree removal
   (step 8) after main has already moved.

5. **Rebase onto main.**
   ```
   git -C "$WT_DIR" rebase main
   ```
   If it stops on conflicts, **resolve them immediately and inline** — do NOT abort:
   - For each conflicted file (`git -C "$WT_DIR" status`), open it and resolve the
     `<<<<<<< / ======= / >>>>>>>` markers faithfully, preserving the intent of BOTH
     sides. Never blindly pick one side; understand what each change is for.
   - `git -C "$WT_DIR" add <resolved-files>`
   - `git -C "$WT_DIR" rebase --continue`
   - Repeat until the rebase completes cleanly.

6. **Fast-forward main.**
   ```
   git -C "$MAIN_WT" merge --ff-only "$BRANCH"
   ```
   (After the rebase, `BRANCH` is ahead of `main` linearly, so this fast-forwards.)

7. **Accumulate permission settings into the repo root.** If the worktree granted any
   new permissions during the task, fold them back into the root `settings.local.json`
   so future worktrees inherit them. Must run while `WT_DIR` still exists (before step 8).
   Skip silently if the worktree has no settings file.
   ```
   WT_SETTINGS="$WT_DIR/.claude/settings.local.json"
   ROOT_SETTINGS=".claude/settings.local.json"   # cwd is repo root (step 1)
   if [ -f "$WT_SETTINGS" ]; then
     [ -f "$ROOT_SETTINGS" ] || echo '{}' > "$ROOT_SETTINGS"
     jq -s '
       .[0] as $r | .[1] as $w |
       $r
       | .permissions = (($r.permissions // {}) * ($w.permissions // {}))
       | .permissions.allow = ((($r.permissions.allow // []) + ($w.permissions.allow // [])) | unique)
       | (if (($r.permissions.deny // []) + ($w.permissions.deny // []) | length) > 0
            then .permissions.deny = ((($r.permissions.deny // []) + ($w.permissions.deny // [])) | unique) else . end)
       | (if (($r.permissions.ask  // []) + ($w.permissions.ask  // []) | length) > 0
            then .permissions.ask  = ((($r.permissions.ask  // []) + ($w.permissions.ask  // [])) | unique) else . end)
     ' "$ROOT_SETTINGS" "$WT_SETTINGS" > "$ROOT_SETTINGS.tmp" && mv "$ROOT_SETTINGS.tmp" "$ROOT_SETTINGS"
   fi
   ```
   Root is the base; `permissions.allow/deny/ask` become the deduped union of root +
   worktree. Other top-level keys in root are preserved.

8. **Remove the worktree.** Make sure your cwd is the repo root (NOT inside `WT_DIR`),
   then:
   ```
   git worktree remove "$WT_DIR"
   ```

9. **Delete the branch.**
   ```
   git branch -d "$BRANCH"
   ```
   Safe `-d`: it is now fully merged into `main`.

10. **Post a task report, then mark the Linear issue done** via the Linear MCP:
   - `mcp__claude_ai_Linear__get_issue` for `${ARGUMENTS}` to get the issue + its team.
   - **Write a short task report as a comment** with `mcp__claude_ai_Linear__save_comment`
     BEFORE changing the status. Keep it brief (3–6 lines): what was done, key changes,
     and the commits integrated. Get the commit list from the main worktree —
     `git -C "$MAIN_WT" log --oneline ORIG_HEAD..main` (ORIG_HEAD is set by the
     fast-forward in step 6) — and anything notable from the session (decisions,
     follow-ups, skipped items).
   - `mcp__claude_ai_Linear__list_issue_statuses` for that team; pick the
     completed/"Done" status.
   - `mcp__claude_ai_Linear__save_issue` to set the issue to that status.

11. **Report** a one-line summary: branch integrated, worktree removed, branch deleted,
    report commented on Linear `${ARGUMENTS}`, issue marked done.
