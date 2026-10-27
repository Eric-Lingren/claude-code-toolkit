---
name: find-work
description: List active worktrees (and their Linear URLs) powered by gxlist. Falls back to listing spike/*, feat/*, and fix/* branches when gxlist is unavailable. Use when user wants to find previous spikes, feature branches, fix branches, or in-progress research branches.
model: haiku
effort: low
invokedBy: human
---

# Find Work

List active worktrees grouped by state (READY / BUILDING / PICKED), including Linear ticket titles and clickable URLs, powered by the `gxlist` inventory engine.

## Process

1. Run the `find-work.sh` script from this skill's directory:

   ```bash
   bash "$(dirname "$0")/scripts/find-work.sh"
   ```

   The script delegates to `~/.dotfiles/.scripts/gxlist`, which outputs worktrees grouped by state with Linear URLs. Pass `--all` to show worktrees across all repos (not just the current one). If `gxlist` is not installed, the script falls back to listing `spike/*`, `feat/*`, and `fix/*` branches from the current repo.

2. If no worktrees (or branches in fallback mode) are found, the script prints a message and exits.

3. Display the script output to the user.

4. If the user wants to switch to one, run `git switch {branch-name}`.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `find-work`.
<!-- skill-done: find-work -->
<!-- learning-capture:end -->
