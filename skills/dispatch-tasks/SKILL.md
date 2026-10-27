---
name: dispatch-tasks
description: Orchestrate a task file by partitioning items by task_type and running one branch per invocation. Routes code items to build-code (inline), reply items to relay (inline), and triage items to export-tasks (agent). Re-entrant — run once per branch until all items are done. Use when user wants to dispatch a mixed task file, route deferred triage items to destinations, or run one pass of the pipeline.
model: sonnet
effort: medium
invokedBy: human
---

# Dispatch Tasks

Route and execute tasks from a task file by partitioning items into branches by `task_type`, running exactly one branch per invocation. The task JSON is the durable state machine — runners mark items done in the JSON as they complete.

**Re-entrant:** run `/dispatch-tasks <path>` once per branch until all items are dispatched.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version: `"2"`)
**Routing config:** `~/.dotfiles/claude-code-shared/resources/task-routing.json`
**Role:** consumer and orchestrator

**Step-0 — validate input before processing:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <input-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not process the file.

## Process

### 1. Load the task file

If a path argument was passed (e.g. `/dispatch-tasks docs/tasks/20260606-0129-my-feature.json`), use it directly.

Otherwise, list all `*.json` files in `docs/tasks/` and ask the user to choose.

Route the task file path through resolve-ref.sh before reading (see `resources/resolve-ref-pattern.md`): Run `bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh $(basename <path>)`. On archive hit (output starts with `ARCHIVE:`), use the extracted content. On not-found (exit non-zero), surface the diagnostic and ask "Continue anyway?" — bypass rebuilds context from conversation.

Read the task file. Read `~/.dotfiles/claude-code-shared/resources/task-routing.json` to get the runners map.

### 2. Partition items by task_type

Group `tasks[]` items into branches keyed by `task_type`. Exclude items with `status` of `done` or `merged`.

Do **not** exclude `blocked` items by status. A `blocked` item is a *parked* item from a prior run, not a terminal one. Re-evaluate it below; its dependencies may have landed since it was parked.

Items without a `task_type` field are treated as `code` (legacy compatibility).

The candidates above form this run's **executable set**. Only park an item when a dependency lives *outside* that set. An intra-branch dependency chain is left intact and handed to the branch runner, which sequences it live in `id` order (build-code checks each task's `blocked_by` at execution time, so a dependency that ran earlier this run is already `done` when its dependent is reached). This is what lets one code-branch invocation drain a full chain AFK, instead of one link per run.

For each candidate item, inspect `blocked_by`:
- **All blockers `done`/`merged`** → item is eligible. If its current status is `blocked`, flip it back to `not_started` (unstick it) and write immediately.
- **Some blocker unsatisfied, but every unsatisfied blocker is in the same branch and in this run's executable set** → keep the item in its branch. Do not park it. The runner sequences the chain.
- **Some unsatisfied blocker is outside this run's executable set** (a different branch, a HITL/deferred item, or anything not running this invocation) → mark the item `blocked` in the JSON (write immediately), remove it from its branch for this run, and add it to the end-of-run blocked summary.

**Ordering assumption:** the runner drains an intra-branch chain only when `id` order respects dependency order (a task's blockers have lower ids). Task producers already emit ids in dependency order, so this holds. If a chain is authored out of order, the runner halts on the out-of-order link and that link reports blocked — re-run to continue.

### 3. Announce the plan, then gate (or auto-proceed)

Print the full plan before running anything. Do not skip this step.

```
Dispatch plan — docs/tasks/<filename>

 Branch   Runner         Mode           Items
 ───────  ─────────────  ─────────────  ─────
 code     build-code     inline-skill   5
 reply    relay          inline-skill   4
 triage   export-tasks   spawn-agent    3

Default execution order: code first (lands fixes + commits), then reply
(posts responses citing the fixing commit), then triage (mechanical export).

Reply to confirm, or tell me a different order / which branch to run.
```

Read the `mode` and `runner` from `task-routing.json` for each branch. Show only branches that have eligible items.

**Triage routing at a glance.** If the `triage` branch has eligible items, print a per-item routing table directly beneath the plan, *before* the gate. This resolves each item's destination up front so the user sees where every triage item lands before confirming, instead of waiting for the spawned `export-tasks` dry-run. Resolve each triage item exactly the way `export-tasks` does (see `agents/egress/export-tasks.md` step 3):

- Read `destinations[item.domain]` from `task-routing.json`.
- **Flat destination** (has a top-level `adapter`, e.g. a client domain like `acme`): every deliverable routes to that adapter regardless of `deliverable`.
- **Split destination** (has `code` / `non-code` sub-keys, e.g. `personal`, `spawned-sapien`): route by `item.deliverable`.

Map the resolved adapter to a friendly `Routes to` label: `github-issues` → `GitHub Issues`, `linear` → `Linear`, `notion` → `Notion (<domain>)`. The `Type` column restates the AFK/HITL nature the deliverable encodes: `code` deliverable is AFK work (an issue/ticket an agent can later build), `non-code` is HITL work (a human task parked in Notion). Include the item's `title` field as a `Title` column so the user can identify what each row represents without cross-referencing the task file.

```
Triage routing at a glance — 7 items

 ID      Title                                           Deliverable  Type   Routes to
 ──────  ──────────────────────────────────────────────  ───────────  ─────  ─────────────────────
 T-0136  Some task title here                            code         AFK    GitHub Issues
 T-0137  Another task title                              code         AFK    GitHub Issues
 T-0138  Yet another task title                          code         AFK    GitHub Issues
 T-0139  One more task title                             code         AFK    GitHub Issues
 T-0140  A non-code task title                           non-code     HITL   Notion (spawned-sapien)
 T-0141  Another non-code task title                     non-code     HITL   Notion (spawned-sapien)
 T-0142  Final non-code task title                       non-code     HITL   Notion (spawned-sapien)
```

If any item resolves to a hard wall crossing (a `corporate`-wall domain routed anywhere outside its own entry, including another client's, or any other domain's item routed into a corporate entry — same rule as `export-tasks` step 4), append a `WARNING:` line per offending item. This is preview only. `export-tasks` still runs its own dry-run and wall check before any write; this table does not authorize writes.

**Single-branch fast path:** If exactly one branch has eligible items, print the plan and proceed directly to step 4 without waiting for confirmation. No ordering decision exists. Print `Auto-proceeding (only one branch eligible).` and continue in the same turn.

**Multi-branch gate:** If two or more branches have eligible items, this is a hard HITL gate. STOP here and end your turn. Do not proceed to step 4 in the same turn. Executing multiple branches has ordering implications (reply must follow code) and may involve irreversible external writes (triage exports), so the user must get a real chance to intervene or reorder.

Only after the user replies in a **new message** do you continue to step 4. Interpret their reply:
- Explicit acceptance ("yes", "go", "ok", "run it", empty confirm) → run in default order.
- A different order or a named branch → honor it.
- "Don't run" / "just create the tasks" / any decline → stop. Do not run any branch.

**Reply-after-code ordering is load-bearing.** `reply` items are `blocked_by` their `code` fix task, so relay can cite the commit that landed the fix. If the reply branch is run before the code branch completes, those reply items report `blocked` (per step 2) and auto-defer to a later invocation — no reply is posted ahead of its fix. Keep `code` before `reply` unless the user has a specific reason to override.

Record the confirmed order. Do not prompt again per-run.

### 4. Run one branch

Run branches in the confirmed order, **one branch per invocation**. Each invocation of `/dispatch-tasks` runs exactly one branch, then stops.

**Select the first pending branch** (ordered per step 3 confirmation). A branch is pending if it has eligible items and has not been run in this invocation.

#### code branch (inline-skill mode)

Call the `build-code` skill using the Skill tool:

```
Skill("build-code", args="<task-file-path>")
```

build-code handles branching, TDD loop, blocker detection, HITL pausing, and status updates inline. It is interactive — it may pause for user input.

After build-code returns:
- Read the task file and collect all items in the `code` branch now marked `done`.
- If build-code halted early (any item still `not_started` or `in_progress`): note which items are pending and include them in the end-of-run summary.

#### reply branch (inline-skill mode)

Call the `relay` skill using the Skill tool:

```
Skill("relay", args="<task-file-path>")
```

relay reads every eligible `reply` task, stitches the blocking code task's `commit` into each draft as a permalink, presents the combined drafts for final HITL approval, and (once built) posts the threaded replies and resolves threads. relay is currently a **copy-only stub** — it prints the drafts for manual paste and marks nothing done until it is built out. It is interactive; it may pause for user approval.

After relay returns:
- Read the task file and collect all `reply` items now marked `done`.
- Reply items still `blocked` (their code fix not yet `done`/`merged`) or `not_started` (copy-only stub): note them in the end-of-run summary. Re-run once the code branch has landed.

#### triage branch (spawn-agent mode)

Spawn `export-tasks` as an isolated agent using the Agent tool:

```
Agent(subagent_type="export-tasks", prompt="<task-file-path>")
```

export-tasks handles destination resolution, dry-run review, external writes, and status/export_url writeback. It runs in its own context window with no memory of this session.

After the agent returns:
- Read the task file and collect all items in the `triage` branch now marked `done`.
- If any items remain `not_started` or `blocked`: note them in the end-of-run summary.

### 5. End-of-invocation summary

After the branch completes, read the task file and print a status summary:

```
Branch complete — triage / export-tasks

 ID      Title                                       Result
 ──────  ──────────────────────────────────────────  ──────
 T-0010  Round-trip read side                        done
 T-0011  Additional runners                           done
 T-0012  Remaining destination adapters               blocked (export-tasks: placeholder DB ID)

Remaining branches:
  code (build-code, inline-skill) — 5 items

Re-run to continue:
  /dispatch-tasks docs/tasks/<filename>
```

If all branches across all invocations are now `done` or `merged`, print a final summary instead:

```
All branches dispatched — docs/tasks/<filename>

 ID      Title                                   Status
 ──────  ──────────────────────────────────────  ──────
 T-0001  Bootstrap schema                        done
 T-0010  Round-trip read side                    done

All items dispatched. Run /run-task-followups for manual follow-ups.
```

### 6. Output handoff block

After the summary, always output:

```
Next steps:
```

Then run:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py dispatch-tasks
```

Append that output under the Next steps header. If more branches remain, prepend a note: "Re-run `/dispatch-tasks <file>` to process the next branch." Do not hardcode skill names.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `dispatch-tasks`.
<!-- skill-done: dispatch-tasks -->
  - `/run-task-followups` — all branches are done and FU-001 cleanup is ready
  - `/to-e2e-tasks` — want e2e coverage for the completed changes (optional)
<!-- learning-capture:end -->
