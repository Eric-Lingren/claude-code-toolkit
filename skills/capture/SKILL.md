---
name: capture
description: >
  Capture an idea, fix or bug as a GitHub issue without breaking flow. Use when the user says
  "capture idea", "capture fix", "capture bug", "capture that", "file that issue",
  "document that idea", "capture flush", "capture review", "capture import", or invokes /capture. Optional type word; a bare
  `capture` asks what the idea is. Builds the issue, shows one compact plain-text preview,
  and files it in the background on y.
model: sonnet
effort: low
invokedBy: both
---

# Capture

Fast in-session capture to GitHub Issues. Eric never names the repo. One preview, one reply,
no popups, no mini grill. All mechanical steps live in
`~/.dotfiles/claude-code-shared/scripts/capture/capture-file.sh` (the driver); this file only
covers the judgment calls.

## Blocking rule

The preview is the only blocking step, and it is plain text. Never use AskUserQuestion, here
or anywhere in this skill. Eric answers with one reply: `y`, `edit` (or a change such as
"make it a fix"), `cancel`, `Nnew` (force a new issue instead of commenting on `#N`).

## Flow (all in the foreground until y)

1. **Parse.** `capture idea|fix|bug <text>`: the type word is optional (`bug` means `fix`).
   Without one, pick `idea` (loose, needs thinking) or `fix` (concrete, something is wrong).
   A bare `capture` with nothing else: ask once, in plain text, what the idea is.
   `capture review` and `capture import`: see their sections below.
   `capture flush`: run `capture-file.sh flush`, print its output, stop.
2. **Choose the target repo by subject.** Toolchain subjects (skills, agents, hooks, scripts,
   claude-code-shared, shell config, cmux, Claude behavior) go to `Eric-Lingren/dotfiles`.
   A clearly project-specific idea proposes that project's repo (for example
   `Eric-Lingren/SpawnedSapien`). Unclear: dotfiles. The target is always shown in the
   preview for confirmation.
3. **Build the capture JSON** in a temp file: `{title, label, sealed, what, why, where, session}`
   per `contracts/capture-issue-schema.json`. `what` is 1-3 lines, `why` is what Eric was
   doing, `where` is repo, branch and relevant file:line, `session` is the session id.
4. **Run the gate:**
   `capture-file.sh prepare <capture.json> --target <org/repo> --source -`
   It validates, resolves labels, runs `wall-check.sh --mode capture`, flushes the outbox
   (it prints a one-line notice if it retried anything) and looks for a duplicate.
   - Exit 0: print its output as the preview, unchanged.
   - Exit 10 (sealed required, corporate source): anonymize the capture, then rerun. No
     client names, file paths, code snippets, ticket IDs, PR links or session footer. Generic
     toolchain terms only (for example "in a large Django/React monorepo, the review watcher
     misses rebases"). Set `sealed: true` with empty `where` and `session`. The preview shows
     the `⚠ sealed (from <repo>)` marker so Eric can inspect the rewrite.
   - Exit 1: blocked, invalid, or the repo is not a capture target. Print the one-line reason
     and stop. Nothing is filed.
5. **Wait for the reply.** `edit` or a change: update the JSON, rerun step 4. `cancel`: stop.

## On y (background)

- New issue (no `≈` match, or `Nnew`): spawn the `export-tasks-github` adapter in the
  background with `{title, description: <rendered body>, org_repo, labels}`. Render the body
  with `scripts/capture/validate-capture.py <capture.json>` and take `labels` from
  `scripts/capture/resolve-labels.sh <org/repo> <label>` (the plan file from step 4 has both).
  Pass its one-line response to `capture-file.sh finish <capture.json> --target <org/repo> <response>`.
- Approved `≈#N` match: run `capture-file.sh file <capture.json> --target <org/repo> --comment N`
  in the background. It posts "seen again: <why>, <date>" on that issue, never across repos.

Either path prints exactly one line: `✓ captured #N [label] <url>` or one `ERROR: ...` line.
On failure the driver already enqueued the capture in the outbox and sent a notification.

## Review (`capture review`)

Shows the pending sweep batch again and files what Eric approves. Never AskUserQuestion.

1. Run `scripts/capture/pending.sh expire`, then `pending.sh list`. Print the list as plain text,
   one line per item, then the reply hint:
   ```
   1. [idea] Review watcher misses rebases
   2. [fix] Banner shows stale count (sealed)
   3. [idea] Pick bucket for seen-again
   y all / 1,3 those, drop rest / n drop all / e2 <change> / ?2 show / 2new force new issue
   ```
   Nothing pending: print `nothing pending` and stop.
2. For each item, run `capture-file.sh prepare` on it (`pending.sh show N` gives the body; the
   item file is `$CAPTURE_PENDING_DIR/<id>.json`). A `≈#N` match is shown on its line. `Nnew`
   forces a new issue.
3. Apply the reply with `scripts/capture/review-apply.sh "<reply>" [--target <org/repo>]
   [--comment <item>=<issue>]...`. It runs the reply grammar via `pending.sh review-reply`,
   files approved items through `capture-file.sh file` (new issue, or a "seen again" comment
   when `--comment` names the approved `≈` match; `Nnew` means omit it), removes filed and
   dropped items, and prints `edit N <change>` or the body for `e`/`?` replies. For `e`, update
   that item's JSON, re-show the list, and wait for the next reply. Unreviewed items stay queued
   (the 14-day expiry applies).
4. Print one `✓ captured #N [label] <url>` or `ERROR: ...` line per filed item.

## Import (`capture import`, one-time)

One-time migration of the Notion scratch pad. Not a permanent subcommand: once Eric says the
dump is done, stop offering it. Nothing is ever deleted from Notion; Eric archives the page.

1. Ask once, in plain text, for the pasted dump.
2. Split it into separate ideas. Label each `idea` or `fix`. Drop duplicates within the dump and
   against open issues (`find-dupes.sh` via `prepare`). Write them as a JSON array of capture
   objects (`contracts/capture-issue-schema.json`). Imported ideas have no session: `where` is
   `Notion scratch pad`, `session` is empty.
3. `scripts/capture/import-batch.sh split <items.json> <out-dir>` writes batches of at most 15.
4. For each batch in order: `import-batch.sh queue <batch.json>`, then show the same compact list
   as Review and take one reply via `review-apply.sh`. Clear the queue (remaining items dropped
   with `n`) before the next batch. Say `batch 2 of 3` between batches.

## Contract

**Format:** capture issue — `contracts/capture-issue-schema.json`
**Role:** producer (builds the capture JSON that `capture-file.sh` files as a GitHub issue)

**Validate the capture JSON** (`capture-file.sh prepare` runs this check itself):
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/capture-issue-schema.json <capture.json>
```

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `capture`.
<!-- skill-done: capture -->
<!-- learning-capture:end -->
