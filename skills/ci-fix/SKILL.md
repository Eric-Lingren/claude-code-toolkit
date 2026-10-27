---
name: ci-fix
description: Diagnose and fix CircleCI pipeline failures on the current branch. Pulls failing job logs via CircleCI MCP, applies all code fixes, verifies locally for non-hook-covered steps, and auto-pushes via gxpush. One shot per invocation.
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

# CI Fix

A one-shot skill that diagnoses CircleCI failures on the current branch, applies
all code fixes, and pushes. Semantic fixes are flagged in an inline summary for
post-hoc review. Run once per pipeline failure; re-invoke if a second wave of
failures appears after the push.

---

## Step 0: Auth gate

Before doing anything else, verify CircleCI MCP is available and authenticated.

Call `mcp__circleci__list_skills` (or the equivalent `list_skills` tool in the
CircleCI MCP server). If the call fails, returns an auth error, or the tool is
unavailable:

**Hard refuse. Print:**

```
ci-fix requires the CircleCI MCP server to be authenticated.

To fix:
  1. Open /mcp in a new Claude Code session.
  2. Select the CircleCI MCP server.
  3. Complete the OAuth flow.
  4. Return here and re-run /ci-fix.

Stopping — no changes made.
```

Do not proceed past this step until CircleCI MCP responds successfully.

---

## Step 1: Load the debug-failing-run playbook

Immediately after confirming auth, fetch the CircleCI MCP's own diagnostic
playbook so you follow its prescribed ordering throughout the run.

```
list_skills  →  find the skill named "debug-failing-run" (or closest match)
get_skill(name="debug-failing-run")  →  read and internalize its steps
```

Keep this playbook active throughout Steps 3–4. If CircleCI MCP has no such
skill, continue without it — the steps below are the fallback ordering.

---

## Step 2: Detect branch and resolve project slug

**Detect current branch:**

```bash
git branch --show-current
```

**Resolve CI project slug** — check sources in this order:

1. Read `~/.dotfiles/claude-code-shared/resources/repo-policy.json`. Find the
   entry matching the current repo (match by remote URL via
   `git remote get-url origin`). If that entry has a `ci` field, use
   `ci.project_slug`.

2. If no `ci` field exists: read `.circleci/config.yml` in the project root.
   Extract the project slug from any `circleci.com` URLs or explicit slug
   references in that file.

3. If neither source yields a slug: **stop and report**:
   ```
   Could not resolve a CircleCI project slug for this repo.
   Add a ci field to repo-policy.json or confirm .circleci/config.yml exists.
   Stopping — no changes made.
   ```

The `ci` field shape in repo-policy.json is:
```json
"ci": { "provider": "circleci", "project_slug": "gh/org/repo" }
```

---

## Step 3: Query CircleCI for failing jobs

Use the resolved `project_slug` and `branch` to walk the pipeline.

**Query chain** (follow the debug-failing-run playbook ordering where available):

```
list_runs(project_slug, branch) → pick the latest run
  ↓
list_run_workflows(run_id)       → identify all workflows
  ↓
list_workflow_jobs(workflow_id)  → for each workflow, find FAILED jobs
  ↓
for each failing job:
  get_job(job_number)            → metadata (step breakdown, timing)
  get_job_logs(job_number)       → full log output
  list_job_tests(job_number)     → test results if available (pytest, vitest)
```

Collect all failing jobs before moving to Step 4. Do not fix one job at a time
in isolation — gather the complete failure picture first, then diagnose together
to avoid conflicting fixes.

**If no runs are found** for the current branch: stop and report that no
CircleCI run was found for branch `<branch>`. The user may need to push first.

**If all jobs pass**: print "All jobs passing on <branch> — nothing to fix."
and stop cleanly.

---

## Step 4: Diagnose each failure

For each failing job, classify the failure type from the logs:

| Failure type | Signals |
|---|---|
| **lint** | ruff, biome, flake8 violation output |
| **type error** | pyright, mypy, TypeScript tsc error |
| **test failure** | pytest FAILED / ERRORS, vitest test name + assertion |
| **migration check** | django check --deploy, migration consistency errors |
| **build/config error** | pip-compile hash mismatch, nextjs build error, missing env |
| **dependency error** | pip install failure, npm ci failure |
| **snapshot/tracking gate** | vitest snapshot mismatch, biome-errors rule count drift, coverage threshold breach |
| **other** | anything that doesn't fit the above |

Build a diagnosis summary before touching any code:

```
Job: <job_name>
Type: <failure_type>
Root cause: <one sentence>
Fix approach: <what to change>
Mechanical? <yes/no — mechanical means the fix is deterministic: e.g. auto-fix lint, regenerate lockfile>
```

A fix is **semantic** if it changes test logic, business logic, build
configuration decisions, or migration files. A fix is **mechanical** if it is
deterministic and reviewable without context (auto-fixable lint, type annotation
insertion, lockfile regeneration).

---

## Step 5: Apply fixes

Fix all failing jobs in a single pass. Do not commit or push until all fixes are
applied and verified.

**Hard rule: never weaken a gate to pass CI.** Do not update snapshots,
tracking lists, rule configs, or thresholds to absorb a new failure. Fix the
source code that caused the violation. A snapshot update that changes expected
counts or adds a rule to a tracking budget is not a fix. It buries the failure.

Example: if biome-errors.test.ts fails because a new rule (e.g.
`noNonNullAssertion`) has violations, find and fix the violating code. Do not
add the rule to `BIOME_RULES_TO_TRACK` with a nonzero count. Do not update the
snapshot to accept "new untracked rules."

**Fix approaches by type:**

- **lint**: Apply the auto-fix command for the linter if available (ruff check
  --fix, biome check --apply), then manually fix any remaining violations.
- **type error**: Add or correct type annotations, fix import paths, adjust
  signatures.
- **test failure**: Fix the code under test OR (if test expectations are wrong)
  fix the test. Prefer fixing production code first; fixing tests is semantic.
- **migration check**: Fix model definitions or generate the missing migration.
  Migration generation is semantic — flag it.
- **build/config error**: Fix the config, regenerate lockfiles (pip-compile),
  update env variable references.
- **dependency error**: Update the requirements or package files.
- **snapshot/tracking gate**: Find the source violations that caused the gate to
  trip. Run the relevant linter or checker to list violations. Fix each violation
  in the source code. Confirm the gate passes with the original snapshot/config
  unchanged. If the violation count is small (under ~10), fix all of them. If
  large, stop and report. The gate likely shifted due to a config change, not
  this branch's code.
- **other**: Apply judgment; flag as semantic.

**Multi-job conflicts:** If two jobs suggest contradictory fixes, resolve the
conflict before applying. Note the resolution in the final summary.

---

## Step 6: Local verification

Run local verification only for CI steps that existing hooks do not cover.

**Existing hooks already cover (DO NOT re-run these):**
- Lint (ruff, biome, flake8) — covered by `lint-edited-file.sh` (edit-time)
  and `.pre-commit-config.yaml` (commit-time)
- Format (black, biome format) — same hooks

**Run these locally before pushing:**

| Failure type fixed | Verification command |
|---|---|
| Python test failure | `python -m pytest <affected test file(s)> -x` |
| Type error (Python) | `python -m pyright <affected file(s)>` |
| Type error (TS) | `npx tsc --noEmit` in the frontend workspace |
| Frontend test failure | `npx vitest run <affected test file(s)>` |
| Migration check | `python manage.py migrate --check` and `python manage.py check --deploy` |
| Migration rollback | `python manage.py migrate <app> <prev_migration>` then forward again |
| Nextjs build error | `npx next build` (only if the failure was in the build step) |

Run only the verification commands relevant to the failure types you fixed. If
verification fails: **stop. Do not push.** Print:

```
Local verification failed for <step>.
Output:
  <command output>

Fix the above before pushing. Re-run /ci-fix after resolving.
```

If all relevant verifications pass (or there are no non-hook-covered steps),
continue to Step 7.

---

## Step 7: Push

Push via gxpush with the `--auto` flag, which handles staging, secret scanning,
commit message generation, and push:

```bash
~/.dotfiles/.scripts/gxpush --auto
```

Do not stage files manually or construct a commit message — gxpush --auto owns
that.

If gxpush exits non-zero: stop and surface the full output to the user. Do not
retry automatically.

---

## Step 8: Inline summary

After a successful push, print a structured summary.

**Format:**

```
CI Fix — <branch> — <N> job(s) fixed

Mechanical fixes (M):
  - <job_name>: <one-line description> [e.g. "ruff: 3 unused imports removed"]
  - <job_name>: <one-line description>

Semantic fixes (S) — review recommended:
  - <job_name>: <one-line description>
    Diff:
      <minimal diff snippet showing what changed>
    Reasoning: <why this fix was applied>
```

If there are no semantic fixes, omit that section. If all fixes are semantic,
omit the mechanical section.

---

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `ci-fix`.
<!-- skill-done: ci-fix -->
<!-- learning-capture:end -->
