---
name: worktree
description: >
  Resolve or create a git worktree for a branch, list worktrees, or remove a worktree.
  Delegates entirely to the ~/.dotfiles/.scripts/worktree engine — same logic as the `wt`
  shell alias. Use when the user types /worktree <branch>, /worktree ls, /worktree rm <branch>,
  or asks to open/switch to a worktree from inside a Claude session.
model: sonnet
effort: medium
invokedBy: human
---

# Worktree

Thin skill wrapper around the `~/.dotfiles/.scripts/worktree` engine. Both invocation
doors — the `wt` zsh function and this skill — delegate to the same script so behavior is
identical. The only difference: Claude cannot `cd` the calling shell, so this skill prints
the worktree path and asks the user to `cd` there in their terminal.

## Invocation forms

```
/worktree <branch> [base]     resolve or create a worktree for <branch>
/worktree ls                  list worktrees for the current repo
/worktree ls --all            list all worktrees across all repos
/worktree rm <branch>         remove a worktree (keeps the branch)
/worktree rm <branch> --force remove even with uncommitted or unpushed changes
/worktree --help              show engine help
```

## Process

### 1. Parse args

Read the user's message. Extract the subcommand and any arguments exactly as typed
(e.g. `feat/my-branch`, `ls`, `rm fix/old-branch --force`).

If the user provides no args, print the invocation forms above and stop.

### 2. Run the engine

Run the engine script, combining stdout and stderr so all output is visible:

```bash
~/.dotfiles/.scripts/worktree <args> 2>&1
```

The engine writes informational messages to stderr and the resolved worktree path to
stdout. Combining them here is correct — Claude shows both streams to the user.

**Note:** the engine requires a git repository in the current working directory. If the
session's cwd is not a git repo, the engine exits with an error — relay that error message
as-is.

### 3. Relay output and guide cd

Relay the full output verbatim.

For subcommands that resolve or create a worktree (`<branch>` form):
- Extract the last line of the combined output — that is the absolute worktree path.
- Print a cd hint at the end:

```
Run in your terminal to enter the worktree:
  cd <worktree-path>
```

Note: if `wt <branch> --claude` is your preferred flow, run that in the terminal instead —
the zsh function handles the `cd` automatically.

For `ls`, `rm`, and `--help` subcommands, no path extraction or cd hint is needed — just
relay the output.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `worktree`.
<!-- skill-done: worktree -->
<!-- learning-capture:end -->
