---
name: sprout-seed
description: >
  End-to-end unattended pipeline: converts a seed file into tasks, executes every
  AFK task via build-code, and returns a pr-code-review of the resulting diff —
  all without user interaction. Use when you want one command to go from seed to
  reviewed PR.
model: sonnet
effort: high
invokedBy: human
---

# Sprout Seed

Run the full seed-to-reviewed-PR pipeline unattended. Three skills execute in
sequence as isolated subagents; no HITL gates interrupt the chain.

## Invocation

```
/sprout-seed docs/seeds/my-feature.json
```

The argument is the path to a ready (non-draft) seed file.

## Architecture

Each skill runs as a **fresh-context subagent** spawned with the Agent tool —
never the Skill tool inline. Context flows only through handoff files written to
`docs/tasks/.sprout/` — the orchestrator never captures or stores a subagent's
full return text. Between phases, the orchestrator reads the compact handoff JSON
and validates it against
`~/.dotfiles/claude-code-shared/contracts/sprout-handoff-schema.json`.

Model and effort for each subagent come from
`~/.dotfiles/claude-code-shared/resources/model-tiers.json`:

| Skill          | Tier | Model  | Effort |
| -------------- | ---- | ------ | ------ |
| `to-tasks`     | T3   | sonnet | high   |
| `build-code`   | T3   | sonnet | high   |
| `pr-code-review` | T3 | sonnet | high   |

## Process

### Step 0: Resolve and validate the seed path

Read the seed file. Confirm `status` is `"ready"` — if it is `"draft"`, stop and
tell the user to resolve open threads before running sprout-seed.

Derive a branch name from the seed filename slug using the conventions in
`~/.dotfiles/claude-code-shared/resources/branching-strategy.md` (typically
`feat/<slug>`).

Create the handoff directory:

```bash
mkdir -p docs/tasks/.sprout
```

#### Ensure `.worktree-meta.json` exists

If `.worktree-meta.json` already exists in the working directory (e.g. from
`gxstart`), leave it as-is. Otherwise create one so `gxlist` and `gxship` work:

```python
import json, os, datetime
meta_path = ".worktree-meta.json"
if not os.path.exists(meta_path):
    # Extract ticket ID from branch name (e.g. KEY-2346 from feat/KEY-2346-foo)
    branch = "<branch_name>"
    import re
    match = re.search(r'([A-Z][A-Z0-9]+-\d+)', branch)
    ticket_id = match.group(1) if match else ""
    meta = {
        "linear_ticket_id": ticket_id,
        "linear_url": f"https://linear.app/issue/{ticket_id}" if ticket_id else "",
        "ticket_title": "<seed title>",
        "branch_name": branch,
        "status": "building",
        "picked_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
```

### Step 1: to-tasks — generate the task file

Spawn a **fresh** `to-tasks` agent using the Agent tool (not the Skill tool):

```
Agent(
  subagent_type="to-tasks",
  prompt=<see prompt template below>
)
```

**to-tasks subagent prompt template:**

```
You are running the to-tasks skill in fully unattended mode.

Seed path: <seed_path>
Branch name: <branch_name>  (use this exact name — do not ask)

Follow ~/.dotfiles/claude-code-shared/skills/to-tasks/SKILL.md exactly, with
these overrides for unattended operation:
- Use the seed path above directly (skip discovery).
- Use the branch name above without prompting (skip the "Branch name?" question).
- If the seed has verification.status "degraded", auto-confirm the quality-gate
  override and continue (do not stop for user input).
- After writing the task file, write the following handoff file to
  docs/tasks/.sprout/phase-1-tasks.json:
    {
      "phase": "phase-1-tasks",
      "paths": ["<task_file_path>"],
      "status": "success",
      "errors": []
    }
  On failure, write:
    {
      "phase": "phase-1-tasks",
      "paths": [],
      "status": "failed",
      "errors": ["<error description>"]
    }

All other to-tasks steps run normally.
```

After the agent completes, read and validate the handoff file:

```bash
cat docs/tasks/.sprout/phase-1-tasks.json
```

Validate against the schema at
`~/.dotfiles/claude-code-shared/contracts/sprout-handoff-schema.json`:
check that `phase`, `paths`, `status`, and `errors` are all present, that
`status` is one of `"success"`, `"failed"`, or `"skipped"`, and that `paths`
and `errors` are arrays of strings. Use jq:

```bash
jq -e '
  (.phase | type) == "string" and
  (.paths | type) == "array" and
  (.status | test("^(success|failed|skipped)$")) and
  (.errors | type) == "array"
' docs/tasks/.sprout/phase-1-tasks.json
```

If the file is absent, invalid, or `status` is `"failed"`, stop and report:
> Step 1 failed: phase-1-tasks.json is missing, invalid, or reports failure.
> errors: <errors array from handoff>

Extract the task file path from `paths[0]`.

### Step 2: build-code — execute all tasks locally

Spawn a **fresh** `build-code` agent using the Agent tool:

```
Agent(
  subagent_type="build-code",
  prompt=<see prompt template below>
)
```

**build-code subagent prompt template:**

```
You are running the build-code skill in fully unattended mode.

Task file: <task_file_path>
Run all not_started tasks (do not ask which task ID).

Follow ~/.dotfiles/claude-code-shared/skills/build-code/SKILL.md exactly, with
these overrides for unattended operation:
- Use the task file path above directly (skip the "Which task file?" question).
- Run all not_started tasks (task ID selection: leave blank / run all).
- Do NOT push or create a PR. All changes stay as local commits only. Skip
  Step 7 entirely. The human decides when to push via gxship or gxpush.
- After the run-complete summary, write the following handoff file to
  docs/tasks/.sprout/phase-2-build.json:
    {
      "phase": "phase-2-build",
      "paths": ["<task_file_path>"],
      "status": "success",
      "errors": []
    }
  On blocker-task failure (halted run), write:
    {
      "phase": "phase-2-build",
      "paths": [],
      "status": "failed",
      "errors": ["build-code halted due to blocker task failure"]
    }

All other build-code steps run normally (wave execution, lint/test gates, etc.).
```

After the agent completes, read and validate the handoff file:

```bash
jq -e '
  (.phase | type) == "string" and
  (.paths | type) == "array" and
  (.status | test("^(success|failed|skipped)$")) and
  (.errors | type) == "array"
' docs/tasks/.sprout/phase-2-build.json
```

If the file is absent, invalid, or `status` is `"failed"`, stop and report:
> Step 2 failed: phase-2-build.json is missing, invalid, or reports failure.
> errors: <errors array from handoff>

### Step 3: pr-code-review — review the diff

Spawn a **fresh** `pr-code-review` agent using the Agent tool:

```
Agent(
  subagent_type="pr-code-review",
  prompt=<see prompt template below>
)
```

**pr-code-review subagent prompt template:**

```
You are running the pr-code-review skill in fully unattended mode.

There is no PR yet. Gather the diff against the merge base with the main branch:
  git diff $(git merge-base HEAD origin/main)..HEAD

Follow ~/.dotfiles/claude-code-shared/skills/pr-code-review/SKILL.md exactly,
using the local diff above instead of gh pr diff.
Run all five dimension agents in parallel, dedup, run the investigator gate, and
write the full formatted findings to docs/tasks/.sprout/phase-3-review-findings.md.

After writing the findings file, run Step 5 from the pr-code-review skill to
generate a review_finding task file from the verified findings. Use the slug
"sprout-review" instead of "pr-review" for the task filename.

Then write the following handoff file to docs/tasks/.sprout/phase-3-review.json:
  {
    "phase": "phase-3-review",
    "paths": ["docs/tasks/.sprout/phase-3-review-findings.md"],
    "task_file": "<path to generated task file, or null if no findings>",
    "status": "success",
    "errors": []
  }
On error or no findings, write:
  {
    "phase": "phase-3-review",
    "paths": [],
    "task_file": null,
    "status": "failed",
    "errors": ["<error description>"]
  }
```

After the agent completes, read and validate the handoff file:

```bash
jq -e '
  (.phase | type) == "string" and
  (.paths | type) == "array" and
  (.status | test("^(success|failed|skipped)$")) and
  (.errors | type) == "array" and
  (.task_file | . == null or (type == "string"))
' docs/tasks/.sprout/phase-3-review.json
```

If the file is absent, invalid, or `status` is `"failed"`, stop and report:
> Step 3 failed: phase-3-review.json is missing, invalid, or reports failure.
> errors: <errors array from handoff>

Extract the findings file path from `paths[0]` and the follow-up task file path from `task_file`.

### Step 4: Return the summary

Print a consolidated summary to the conversation:

```
sprout-seed complete

Seed:       <seed_path>
Task file:  <task_file_path>
Branch:     <branch_name>
Review:     <findings_file_path>
Follow-ups: <follow_up_task_file_path or "none">

All changes are local commits. Run gxpush or gxship when ready to push.
```

If `task_file` is non-null, include the review finding counts and note that the
follow-up task file can be run with `/build-code <path>` or `/sprout-seed` to
iterate on the findings.

The orchestrator writes `.worktree-meta.json` (if absent) and creates the
`.sprout/` directory in Step 0. All other file output is produced by the phase
subagents.

## Error handling

If any step fails, stop immediately and report which step failed and why. Do not
attempt the remaining steps. The partial output (task file path, branch name)
is printed before the error so the user can resume manually if needed.

On failure, the handoff file for the failed phase is available at
`docs/tasks/.sprout/<phase>.json` and contains the error details.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `sprout-seed`.
<!-- skill-done: sprout-seed -->
<!-- learning-capture:end -->
