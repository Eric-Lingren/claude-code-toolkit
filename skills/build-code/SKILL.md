---
name: build-code
description: Execute tasks from a tasks JSON file sequentially, one build-runner spawn per task. Handles branching, status updates, blocker detection, leaf/blocker failure policy, and deferred HITL tasks without halting independent work. Use when user wants to run AI tasks from a docs/tasks/ file.
model: sonnet
effort: high
invokedBy: human
---

<!-- tier-delegate: managed by sync-model-tiers.py -->
## Delegate menial lookups to Haiku (cost control)

During this skill, push pure read-only lookups DOWN to a cheap subagent instead
of running them on the current model. This covers: multi-file grep/glob,
"where is X defined / what calls Y", mapping a directory, reading many files to
locate something, or fetching a URL for reference.

Use the Agent tool with the `caveman:cavecrew-investigator` subagent (Haiku,
returns a compressed file:line answer). If that subagent is unavailable, spawn a
general agent with `model: haiku`. Keep all reasoning, decisions, and edits on
the current model. Delegate only the menial searching.
<!-- /tier-delegate -->

# Run Tasks

Execute tasks from a `docs/tasks/` JSON file sequentially. Each AFK task is executed in isolation by the `build-runner` agent — build-code itself never runs `/tdd` inline and never holds a task's full execution trace, only the compact receipt `build-runner` returns. Updates task status in the JSON as work progresses.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version: `"1"`)
**Role:** consumer

**Step-0 — validate input before processing:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <input-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not process the file.

## Process

### 1. Ask for task file and target

Always ask explicitly. Do not infer from context:

1. **Which task file?** List ALL `*.json` files in `docs/tasks/` (use `ls docs/tasks/*.json` or equivalent). Files may use either naming convention:
   - Legacy: `NNNN-slug.json` (e.g. `0002-invite-domain-check.json`)
   - Timestamped: `YYYYMMDD-HHMM-slug.json` (e.g. `20260512-1600-invite-domain-check-pr-fixes.json`)

   Show every file found regardless of prefix format. Present them as numbered options with the full filename.
2. **Which task ID?** Ask for a specific task ID (e.g. `T-0005`) or leave blank to run all `not_started` tasks in order.

Route the chosen path through resolve-ref.sh before reading (see `resources/resolve-ref-pattern.md`): Run `bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh $(basename <path>)`. On archive hit (output starts with `ARCHIVE:`), use the extracted content. On not-found (exit non-zero), surface the diagnostic and ask "Continue anyway?" — bypass rebuilds context from conversation.

Read the chosen JSON file.

**Pre-flight: fire context-loader in the background now.**
Check whether context sources exist:
```bash
[ -f CONTEXT.md ] || ls docs/adr/*.md 2>/dev/null | head -1
```
If exit 0, spawn the `context-loader` agent immediately — do not wait for it yet. Continue to steps 2 and 3 while it runs. If exit non-zero, skip the spawn and use the inline fallback in step 3b.

### 2. Determine the run queue

If a specific task ID was given:
- Run that single task regardless of its current status.
- Still check its `blocked_by` dependencies (see step 4).

If no task ID was given, build the queue:
- Include all tasks with status `not_started`, `failed`, or `blocked`, in `id` order. Resumption is free: a `failed` task from a prior run is retried exactly like a fresh `not_started` task.
- A `blocked` task is a *parked* item, not a terminal one. Re-include it so its `blocked_by` is re-evaluated live at execution time (step 4a). If its blockers have since landed (`done`/`merged`), it runs; if not, step 4a re-parks it. This is what unsticks a task once its dependency completes, instead of excluding it by status forever. Mirrors the same rule in `dispatch-tasks` step 2.
- Skip tasks with status `in_progress`, `done`, `merged`, `needs_eyes`, or `deferred_hitl`.

### 3. Set up branching

Read `branching.strategy` from the JSON:

- **`"single"`** — check out or create `branching.branch` once before starting the queue. All tasks run on this branch.
- **`"per-task"`** — create each task's `branch` field value immediately before that task runs.

### 3b. Build the shared context brief and compute waves (once)

Before the loop starts, if the queue is non-empty:

**Collect context-loader result (pre-fired in step 1):**

If context-loader was spawned in step 1, collect its result now and capture the JSON payload as `context_brief`. If it is still running, wait for it here before proceeding.

If it was not spawned (no context sources found), set `context_brief` to the inline fallback:
```json
{"found":{"context_md":false,"adrs":false,"extra_sources":false},"vocabulary":[],"adrs":[],"sources":[],"missing":["CONTEXT.md","docs/adr/"]}
```

Reuse this same `context_brief` for every `build-runner` spawn in this run — never re-spawn `context-loader` per task.

**Detect tooling (once for the entire run):**
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/build/detect-tooling.py <project_root>
```
Capture the JSON array as `tooling_manifest`. Reuse it for every `build-runner` spawn. Do not re-run detection per task.

**Initialize breadcrumb:**
`breadcrumb = []` (compact receipts from tasks completed so far this run, across all waves).

**Handle HITL tasks upfront:**
Before computing waves, scan the queue for all tasks with `type == "HITL"`. For each:
- Set status to `deferred_hitl` in the JSON.
- Print the task's title, description, and acceptance criteria so the user knows a hands-on action is waiting.
- Add to the end-of-run report's `deferred_hitl` list.
- Remove from the AFK queue (HITL tasks are never waved).

If another queued task's `blocked_by` names a HITL task, that dependent task will correctly report `blocked` in wave blocker evaluation until a human completes the action; that is expected.

**Compute waves from the AFK queue:**

A wave is a maximal set of tasks that can run concurrently given the dependency graph. Compute iteratively:

```
done_ids = set of task IDs already in status "done", "merged", or "needs_eyes" in the JSON
wave_lists = []
remaining = AFK queue (non-HITL tasks)

while remaining is non-empty:
    wave = [t for t in remaining
            if all(b in done_ids for b in t.blocked_by)]
    if wave is empty:
        # Cycle or unresolvable dependency — mark all remaining tasks "blocked" and stop
        break
    wave_lists.append(wave)
    done_ids |= {t.id for t in wave}
    remaining = [t for t in remaining if t not in wave]
```

Tasks with `blocked_by = []` (or whose blockers are all already `done`/`merged`) enter **Wave 1**. Wave N+1 contains tasks whose blockers all appear in waves 1..N. Maximum 4 tasks execute concurrently within any wave.

### 3c. Browser verification run setup (only when any task has `browser_verify`)

Scan the AFK queue. If no task has a `browser_verify` field, skip this entire section and set `browser_run = null`.

If any task has `browser_verify`:

1. **Collect roles and repo:**
   - Gather all unique `role` values from `task.browser_verify.role` across tasks that have `browser_verify`.
   - Derive `repo` (Org/Repo string) from the git remote:
     ```bash
     git remote get-url origin | sed 's|.*github\.com[:/]\(.*\)\.git|\1|; s|.*github\.com[:/]||'
     ```
   - Derive `branch_slug` from the current branch:
     ```bash
     git rev-parse --abbrev-ref HEAD | tr '/' '-'
     ```

2. **Auth ensure:** For each unique role, run:
   ```bash
   python3 ~/.dotfiles/claude-code-shared/scripts/build/browser-auth.py ensure --repo <repo> --role <role>
   ```
   - Exit 0: state is fresh. Continue.
   - Exit 1 (`SKIPPED: auth_expired` in output): log which role expired. Build-code continues — browser-checker will skip affected tasks and report `status: "skipped"`. Collect expired roles in `expired_roles` for the end-of-run summary.

3. **Base server boot:** Compute the merge-base SHA:
   ```bash
   base_sha=$(git merge-base HEAD origin/main)
   ```
   Look up the candidate server's primary port from `resources/app-launch-detection.md`. Use port `<primary_port + 1000>` as the base server's offset port. Then start the base server:
   ```bash
   bash ~/.dotfiles/claude-code-shared/scripts/build/base-server.sh up "$base_sha" <project_root> <offset_port>
   ```
   Capture the URL printed by the script as `base_server_url` (e.g., `http://localhost:4173`).
   - If `base-server.sh up` fails (non-zero exit): set `base_server_url = null`. Note in the end-of-run summary that baselines will fall back to pre-build snapshot path.

4. **Create run directory:**
   ```bash
   run_dir="<project_root>/docs/browser-checks/$(date +%Y%m%d-%H%M)-${branch_slug}"
   mkdir -p "$run_dir"
   ```
   Store the absolute path as `run_dir`.

5. **Verification Report artifact:** Using the Artifact tool (if available in this session), create a new artifact from `~/.dotfiles/claude-code-shared/resources/verification-report-template.html` with the `db` capability enabled. Store the returned artifact ID as `report_artifact_id` and the artifact URL as `report_artifact_url`.
   - If the Artifact tool is not available: set `report_artifact_id = null` and `report_artifact_url = null`.
   - Write the artifact URL to `docs/visual-changes/${branch_slug}/report-url.txt` (create parent directory if needed):
     ```bash
     mkdir -p docs/visual-changes/${branch_slug}
     echo "$report_artifact_url" > docs/visual-changes/${branch_slug}/report-url.txt
     ```
     If `report_artifact_url` is null, skip this write.

Store all gathered values as `browser_run = {base_server_url, repo, run_dir, base_sha, report_artifact_id, report_artifact_url, branch_slug, expired_roles}`.

### 4. Execute waves sequentially; tasks within each wave run in parallel

For each wave in `wave_lists`:

#### a. Blocker pre-check (wave entry)

Before launching any task in the wave, re-read the JSON to confirm each task's `blocked_by` IDs are all `done`, `merged`, or `needs_eyes`. If any blocker is not satisfied:
- Mark that task `blocked` in the JSON.
- If any other queued task in a later wave depends on this task, halt the entire run immediately. Report which task is blocked and why. Do not process further waves.
- Otherwise, park it (add to end-of-run summary) and exclude it from this wave's spawn set.

#### b. Launch the wave concurrently (cap: 4)

For each task in the wave (up to 4 at a time — if the wave has more than 4 tasks, process in batches of 4):

0. Record the shared branch tip once per wave: `wave_base=$(git rev-parse HEAD)`. Step 4d uses it to detect diverged worktrees.
1. Update task status to `in_progress` in the JSON.
2. If `branching.strategy` is `"per-task"`, derive the task's branch name now (do NOT check it out yet — the worktree handles isolation):
   - Read `export_url` from the task object (may be `null` or absent).
   - If `export_url` is a GitHub issue URL (matches `https://github.com/<org>/<repo>/issues/<N>`), extract `<N>` (the last path segment as an integer). Insert a `gh-<N>` segment immediately after the task-ID segment in the branch name. For example: `feat/t-0023-gh-42-bootstrap-auth-schema`. Also set `GITHUB_CLOSES=<N>` in the environment so `gxpush` appends `Closes #<N>` to the PR body automatically.
   - If `export_url` is `null`, absent, or does not match a GitHub issue URL, skip both steps silently.
3. Spawn one `build-runner` agent per task, all in a **single Agent tool call** (so they run concurrently):

```
Agent(subagent_type="build-runner", isolation="worktree",
      prompt="<task object JSON, context_brief, tooling_manifest, breadcrumb, taskfile_basename, project_root, wave_base>")
```

Pass per task:
- `wave_base` — the SHA recorded in step 0. build-runner syncs its worktree onto it before any work, so it never has to pull feature commits in itself.
- `task` — this task's full object (`id`, `title`, `type`, `description`, `acceptance_criteria`, `browser_verify` if present).
- `context_brief` — the brief built once in step 3b.
- `tooling_manifest` — the manifest built once in step 3b.
- `breadcrumb` — the `breadcrumb` list from **prior waves only** (receipts from tasks that completed in earlier waves). Do NOT include same-wave receipts — concurrent tasks must not depend on each other's output.
- `taskfile_basename` — basename of the task file.
- `project_root` — absolute project root path.

When `browser_run` is non-null (set in step 3c), also pass:
- `base_server_url` — `browser_run.base_server_url` (may be null if base server failed to start; build-runner will fall back gracefully).
- `repo` — `browser_run.repo`.
- `run_dir` — `browser_run.run_dir`.
- `base_sha` — `browser_run.base_sha`.
- `report_artifact_id` — `browser_run.report_artifact_id` (may be null if no Verification Report was created).

**When `local_only: true` is set at the task file root**, include this explicit instruction in every build-runner prompt:

> "Do NOT push or create a PR. The task file has `local_only: true` — all changes must remain as local commits only."

build-runner runs the full `/tdd` cycle, the runner-based validation gate, and browser verification (if applicable) internally within its isolated worktree, and writes the full trace to `docs/tasks/.logs/<taskfile-basename>/<task.id>.md`. build-code never sees that trace — only the receipt.

#### c. Collect all receipts for the wave

Wait for all concurrent build-runner agents to complete. For each receipt, parse: `status`, `summary`, `files_touched`, `tests`, `pr`, `log_path`, `follow_ups`. Valid status values are `"done"`, `"failed"`, and `"needs_eyes"`. A `"needs_eyes"` receipt is non-blocking — treat it as successful for wave progression and merging.

#### d. Merge worktree branches sequentially

After all tasks in the wave complete (regardless of individual pass/fail), merge each task's worktree branch into the shared branch **one at a time** in task-ID order:

For each task in the wave (in order):
- If `receipt.status == "failed"`: skip the merge for this task. Its worktree branch is abandoned.
- If `receipt.status == "done"` or `receipt.status == "needs_eyes"`: first check the worktree forked from this wave's base:
  ```bash
  git merge-base --is-ancestor "$wave_base" <task-worktree-branch>
  ```
  If this exits non-zero, the worktree branched from somewhere else (e.g. an advanced `main`). Do NOT merge, since `--no-ff` would pull in unrelated history. Cherry-pick only the task's own commits instead:
  `git cherry-pick $(git rev-list --reverse <task-worktree-branch> ^HEAD ^main)`.
  On cherry-pick conflict, run `git cherry-pick --abort` and treat it like a merge conflict (below).
  After a clean cherry-pick that touched any `package.json`, run `python3 ~/.dotfiles/claude-code-shared/scripts/build/check-json-dupe-keys.py <each touched package.json>`. On non-zero exit, fix the duplicate keys and amend before the next task.
  If it exits 0, confirm the worktree branch has its own commits:
  ```bash
  git log --oneline HEAD..<task-worktree-branch>
  ```
  If the output is empty, the fix was left staged or uncommitted in the worktree. Do NOT merge. Mark this task `failed`, override `receipt.status = "failed"`, and add to `summary`: "Worktree branch has no commits. Changes were not committed." Continue to the next task. Otherwise, attempt `git merge --no-ff <task-worktree-branch>`. Merge messages describe the change only. Never include the task ID (`T-xxxx`) or the worktree branch name.
  - On success: the merge is committed to the shared branch. Record `task_commit=$(git rev-parse HEAD)` for this task right away (after the merge, or after the cherry-pick on that path). Step 4e writes it to the task's `commit` field.
  - On conflict (`git merge` exits non-zero): run `git merge --abort`. Mark this task `failed` in the JSON. Override `receipt.status = "failed"`. Add a note in the task's `summary`: "Merge conflict during wave integration." Continue to the next task — do not halt.

#### e. Write receipts back into the task JSON

For each task in the wave (after its merge attempt):

1. **Write the receipt:**
   - Set `summary`, `files_touched`, `tests`, `log_path` directly from the receipt.
   - If the merge in step 4d succeeded, set `commit` to that task's `task_commit`. This SHA is the task's own fix, so a `reply` task can point a reviewer at exactly the change for its comment.
   - If `receipt.status == "done"`: set task `status` to `done` and `pr` to a suggested `gh pr create` command the user can run (do not run it).
   - If `receipt.status == "needs_eyes"`: set task `status` to `needs_eyes`. Set `pr` to the same suggested command. A `needs_eyes` task is considered successful — it produced code changes, but one or more visual captures need async reviewer attention in the Verification Report.
   - If `receipt.status == "failed"`: set task `status` to `failed`.
   - Write the updated JSON immediately.

2. **Merge follow-ups.** For each item in `receipt.follow_ups`:
   - Deduplicate against existing `follow_ups` in the JSON (skip if a similar title already exists).
   - Assign `"id"` by counting existing `follow_ups` and using the next sequential `FU-XXX` (zero-padded to 3 digits).
   - Append with `"source": "discovered"` and `"trigger_task"` set to this task's ID.
   - Write the updated JSON immediately.

3. **Thread the breadcrumb forward.** On success (`receipt.status == "done"` or `"needs_eyes"`), append a compact entry — `{id, title, summary, files_touched}` — to `breadcrumb`. This breadcrumb is available to all tasks in **subsequent waves** but not to same-wave siblings.

#### f. Apply the AFK obstacle policy for failed tasks

After processing all receipts in the wave:

Tasks with `status == "needs_eyes"` are never failures — skip them in this step entirely.

For each task whose final `status == "failed"`:
- Determine if any task in a later wave depends on this one (a **blocker** task) or not (a **leaf** task).
- **Leaf task failure:** add to the end-of-run report's `failed` list (with `log_path`). Do not halt — continue to the next wave.
- **Blocker task failure:** halt the entire run immediately. Do not process further waves. Frame this as a **scoping signal, not a retry target**: something about this task's acceptance criteria, description, or dependency graph doesn't match reality (an unstated dependency, wrong assumption, or oversized slice), and the recommended next step is to re-grill or re-seed this part of the plan, not to blindly re-run build-code hoping for a different result. Include `log_path` so the user can inspect the full trace before deciding how to re-scope.

#### g. Advance to the next wave

The next wave begins on the merged state left by the current wave. Only tasks whose blockers all ended in `done` or `needs_eyes` status advance to the next wave (the wave algorithm already computed this, but re-confirm at step 4a).

### 4b. Debug cleanup (only when `producer: "debug"`)

Read the root `producer` field of the tasks file. If it is `"debug"` and all fix tasks reached `done`, run the debug end-of-run cleanup automatically — this is AFK work, not a follow-up:

- Execute the "Debug cleanup and post-mortem" runbook from `~/.dotfiles/claude-code-shared/resources/hitl-steps-runbooks.md` (this is debug Phase 5): capture a pre-cleanup test baseline, remove all `[DEBUG-...]` instrumentation, delete throwaway harness files and stale `docs/browser-checks/` run dirs, re-run the suite and confirm no new failures, and state the winning hypothesis in the PR description.
- If cleanup introduces new test failures, treat it as a regression and fix before proceeding.

If `producer` is anything other than `"debug"`, skip this step.

### 4c. Browser verification run teardown (only when `browser_run` is non-null)

If `browser_run` is null, skip this section.

#### a. Shut down the base server

```bash
bash ~/.dotfiles/claude-code-shared/scripts/build/base-server.sh down
```

This is a best-effort call — log any errors but do not halt the run.

#### b. Export Publication

Scan `browser_run.run_dir` for browser-check-result-v2 JSON files (written by build-runner into subdirectories of `run_dir`). Build a flat list of all captures across all result files.

For each capture, apply the export filter:
- **Skip** captures where: `verdict == "expected"` AND the task's `browser_verify.expected_visual_change == "none"`.
- **Include** all other captures (verdict `needs_eyes`, `unexpected`, `regression`, or `expected` with a non-none `expected_visual_change`).

For each included capture, numbered starting at `01`:
```bash
mkdir -p docs/visual-changes/${browser_run.branch_slug}
cp <capture.baseline_png> docs/visual-changes/${browser_run.branch_slug}/<N>-<spec>-<viewport>-before.png
cp <capture.candidate_png> docs/visual-changes/${browser_run.branch_slug}/<N>-<spec>-<viewport>-after.png
```
Where `<N>` is zero-padded to two digits, `<spec>` is the check spec name (slugified), and `<viewport>` is the viewport name (e.g., `desktop`, `mobile`).

Write `docs/visual-changes/${browser_run.branch_slug}/pr-snippet.md`:

```markdown
## Visual changes

| # | Spec | Viewport | Verdict | Before | After |
|---|------|----------|---------|--------|-------|
| 01 | login-flow | desktop | needs_eyes | ![before](01-login-flow-desktop-before.png) | ![after](01-login-flow-desktop-after.png) |
...

> <N_expected> expected, <N_needs_eyes> needs_eyes
```

Count `N_expected` and `N_needs_eyes` across **all** captures in the run (not just exported ones) for the summary line. If no captures qualify for export, still write `pr-snippet.md` with the summary line and an empty table body.

#### c. Ensure report-url.txt is written

If `browser_run.report_artifact_url` is non-null and `docs/visual-changes/${browser_run.branch_slug}/report-url.txt` does not yet exist:
```bash
mkdir -p docs/visual-changes/${browser_run.branch_slug}
echo "$report_artifact_url" > docs/visual-changes/${browser_run.branch_slug}/report-url.txt
```

### 5. End-of-run summary

Print a consolidated status table in the conversation. Every row that reached `done`, `needs_eyes`, `failed`, `deferred_hitl`, or `blocked` gets a `Log` column pointing at its trace (blank for tasks that never reached build-runner, e.g. `blocked` from a dependency check):

```
Run complete — docs/tasks/20260512-1423-user-auth-flow.json

 ID      Title                        Result           Log
 ──────  ───────────────────────────  ──────────────  ─────────────────────────────────
 T-0023  Bootstrap auth schema        done             docs/tasks/.logs/.../T-0023.md
 T-0024  Login endpoint               needs_eyes       docs/tasks/.logs/.../T-0024.md
 T-0025  Design review (HITL)         deferred_hitl    —
 T-0026  Token refresh flow           failed           docs/tasks/.logs/.../T-0026.md
 T-0027  Logout endpoint              blocked          —

Deferred HITL tasks requiring human action:
  T-0025 — Design review: confirm token storage approach

Failed / blocked tasks:
  T-0026 — Token refresh flow: leaf failure, skipped. Inspect docs/tasks/.logs/.../T-0026.md.
  T-0027 — Logout endpoint: blocked by T-0026.

If a blocker task failed (halted the run): frame it as a scoping signal, not a retry target.
Re-grill or re-seed the affected slice before re-running — see docs/tasks/.logs/.../<task>.md for the full trace.

needs_eyes tasks (visual captures awaiting review):
  T-0024 — Login endpoint: 1 needs_eyes capture. Review: docs/visual-changes/feat-login/report-url.txt

Resumption: re-invoking build-code on this file picks up every `not_started` and `failed` task automatically.

Manual follow-ups (2):
  1. Add STRIPE_KEY to Cloudflare [T-0024, discovered]
     a. Go to Cloudflare dashboard > Workers & Pages > your-app > Settings > Variables
     b. Click 'Add variable'
     c. Name: STRIPE_KEY, Value: from Stripe dashboard > API keys
     d. Click 'Encrypt' then 'Save'

  2. Run database migration [T-0023, planned]
     a. Run `npx drizzle-kit push`
     b. Verify tables created with `npx drizzle-kit studio`

Run `/run-task-followups` for interactive walkthrough with step-by-step guidance.
```

Do not write this summary to any file.

### 6. Output handoff block

After the summary, always output:

```
Next steps:
```

Then run:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py build-code
```

Append that output under the Next steps header. Do not hardcode skill names.

### 7. Offer to push and open a PR

**Skip this step entirely if `local_only: true` is set at the task file root.** Do not ask the user about pushing, do not run gxpush, and do not open a PR. Print this note in place of the push prompt: `"local_only: true — push and PR skipped."` Then proceed directly to the learning-capture block.

After printing the summary, ask the user: **"Push and open a PR?"**

If the user says yes (or any affirmative), proceed:

#### a. Generate a PR description

Gather context:
- Current branch: `git rev-parse --abbrev-ref HEAD`
- Commits on branch: `git log main...HEAD --oneline`
- Diff (truncated to first 300 lines): `git diff main...HEAD | head -n 300`
- Linear ticket: extract the first `[A-Za-z]+-[0-9]+` pattern from the branch name (uppercase). This is optional — many projects don't use Linear. If a ticket is found, check CLAUDE.md or `.claude/` config for a Linear workspace URL. If one is present, include `Linear Ticket: [TICKET](<workspace-url>/issue/TICKET)`. If no workspace URL is configured, include the ticket ID as plain text only. If no ticket pattern exists in the branch name, omit this line entirely.

Then write the PR description in this exact format:

```
### <short descriptive title>
<Linear ticket link, if found>

<2-3 sentences: what was broken or missing, and what this PR does to fix it>

**Changes:**
<bullet list of key code changes — skip test files unless they are the point>
```

Rules for the description:
- Under 250 words
- No Testing section, no other sections
- No em dashes — use periods or commas only
- Concise, no run-on sentences

**Browser verification summary line:** If `browser_run` is non-null and any task in the run had a `browser_verify` outcome, append this one-liner to the end of the PR description body:
```
<N> expected, <N> needs_eyes
```
Where the two counts are the totals across all tasks in this run. Omit if `browser_run` is null or no task had `browser_verify`.

#### b. Push and create the PR

1. Run `~/.dotfiles/.scripts/gxpush --pr` via the Bash tool. gxpush runs non-interactively (the Proceed prompt auto-accepts empty input), so the user never sees it live.
2. After the Bash tool returns, print the full gxpush output verbatim to the user. This includes the STAGED, WILL ADD, EXCLUDED, and SECRETS sections so they can see exactly what was committed.
3. Return the PR URL to the user (gxpush prints it after `gh pr create` completes).

#### c. Record the fixing commit on completed code tasks

After the push returns, write the PR URL onto every `code` task this run marked `done`. Each task already carries its own `commit` from step 4e. That per-task SHA is what lets a downstream `reply` task (produced by `/pr-revise`) cite the exact commit that fixed its thread, so the reviewer gets one combined comment instead of a plan-then-commit pair.

For each `code` task now `done`: set `pr` to the PR URL from gxpush. Keep its existing `commit`. Only if `commit` is null, fall back to `git rev-parse HEAD`. Then write the task file. If the user declined the push, leave `pr` null. The per-task `commit` stays, but it is not on the remote yet, so the reply branch will fall back to a PR link or omit the reference.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `build-code`.
<!-- skill-done: build-code -->
  - `/run-task-followups` — all tasks are done and FU-001 cleanup is ready
  - `/to-e2e-tasks` — want e2e coverage for the completed changes (optional)
<!-- learning-capture:end -->
