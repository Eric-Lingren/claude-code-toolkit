---
name: improve-agent-benchmarks
description: Cheap iterative improvement of a shared agent. Runs the next stage for the chosen agent (contract, cases, iterate). Contract stage drafts a cited, history-validated contract.json with an Opus subagent, shows each check with its source, historical pass rate and real failing examples, and freezes it on approval. Use when the user invokes /improve-agent-benchmarks [agent-name].
model: sonnet
effort: high
---

# improve-agent-benchmarks

Usage: `/improve-agent-benchmarks [agent-name]`. No flags. The only argument is the optional agent name. The user never picks a stage: the skill runs the next stage recorded in the queue for that agent.

All mechanical work lives in `~/.dotfiles/claude-code-shared/scripts/agent-eval/`. Call the scripts, do not redo their work in prose. Grading is deterministic (`grade.py`); no LLM ever scores an agent output. Custom grading code never goes in an agent folder.

Data locations (scripts resolve them): queue `.claude/agent-bench/queue.json`, history and drafts `.claude/agent-bench/<agent>/`, frozen `claude-code-shared/evals/<agent>/contract.json` and `baseline.json`.

## Step 1: pick the agent

If an agent name was given, use it. Otherwise run:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/rank.py 4
```

This first syncs the queue with `claude-code-shared/agents/` (recursive): any agent .md not yet in the queue is added at stage `contract` with zero history. It then ranks agents by historical failure rate times spawn count (passing and never-spawned agents sit at the bottom) and prints the top 4 as tab-separated `rank, agent, stage, failure_rate, spawns, failed, reason`.

Show those 4 in chat with failure rate, spawn count and reason, then ask which agent with AskUserQuestion: the 4 as options, plus free text so any typed agent name is accepted. Never ask for a stage.

<!-- preflight:start -->
## Start-of-run checks (every invocation, before any stage work)

As soon as the agent is known, and before Step 2 routes to a stage, run (zero tokens, no model calls):

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/preflight.py <agent>
```

It harvests the agent's recorded spawns, then does three checks. Relay its full output in chat. With no frozen `contract.json` yet it says so and does nothing else.

1. Confirmation. Each kept fix that is on main is graded against production spawns made after its commit, using only the history-gradable checks (shape, quote, side effect). Each fix is reported `confirmed`, `unconfirmed` or `regressed`. For `regressed` it prints the exact `git revert <sha>`: show it to the user, never run it yourself. Role checks (for example `verdict_equals`) cannot be confirmed from history and are labeled so.
2. Staleness. Every cited file is compared with its fingerprint in `contract.json`. A change made by commits from this skill (subject `agent-bench(<agent>): ...`) is fine and silent. Any other change (uncommitted edit, hand commit) triggers a review of only the checks that cite that file: citation status, before/after historical pass rates, and the file diff. Exit code 3 and `LIVE_RUNS: BLOCKED` mean no live run (the baseline run, the iterate stage) may start. Show the review, then ask the user (AskUserQuestion: approve / hold). Only on approval run `preflight.py approve <agent>`, which refreshes the fingerprints in `contract.json` (commit that file). `approve` refuses when an edit broke a citation; then the contract needs fixing first. `iterate.py` enforces the same gate itself.
3. End state. An agent whose contract checks all pass on planted cases and on history is flagged `end_state` in the queue and `rank.py` sorts it below every other agent. It reopens (flag cleared, reason printed) when a cited file changes by hand, a fix regresses, or production failures appear.

Then continue to Step 2 unless the user holds on a stale-contract review.
<!-- preflight:end -->

## Step 2: queue entry

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/bench_queue.py ensure <agent>
```

This creates the entry at stage `contract` if missing (and adds any agent missing from the queue, so this holds even when a name was typed and the ranking step was skipped). Read the printed `stage` and run exactly that stage, then stop (one stage per invocation): `contract` runs the Contract stage below, `cases` runs the Cases stage, `iterate` runs the Iterate stage. If the preflight reported an `END STATE` (the agent is passing), say it has no failing checks and stop. After the stage finishes, run the Summary page step.

## Contract stage

### 1. Harvest history (free)

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/harvest.py <agent>
```

### 2. Draft with Opus

Run the source bundler and capture its output:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/contract_sources.py <agent>
```

Spawn one Agent with `model: "opus"` (general-purpose). Give it the bundler output and these rules, and have it write `.claude/agent-bench/<agent>/draft.contract.json` (resolve to the repo root):

- Three sources only: `role` (the agent's .md), `caller` (each consumer and what it parses), `side_effect` (files written, required tool calls).
- Every check carries `source: {"tag": "role|caller|side_effect", "file": "<path relative to claude-code-shared/>", "line": "N" or "N-M", "quote": "<verbatim text from those lines>"}`. A check with no citable source must not be written.
- Check types: exactly these 8 built-ins, with the params documented in the `grade.py` header: `json_parse`, `schema`, `quote_in_input`, `tool_called`, `tool_not_called`, `file_written`, `verdict_equals`, `no_prose`. Use `when` for checks that only apply to some outputs (for example a side effect only on a pass verdict).
- If a rule cannot be expressed by a built-in, write the check with `"type": "proposed:<name>"` and a `"proposal"` string explaining the rule. Do not write grading code.
- Where two sources disagree (for example the role file says bare JSON, a caller parses loosely), do not choose. Add an entry to `conflicts`: `{"id", "description", "options": [{"label", "check": {<full check with its own source>}}, ...]}` with at least two options.
- Output shape: `{"agent": "<agent>", "checks": [...], "conflicts": [...]}`.

### 3. Validate and show the approval screen

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/contract_review.py <agent>
```

The script drops checks whose citation is not real (missing file, line out of range, quote not found), runs `grade.py`'s engine over the harvested history, and prints per check: source tag, file:line, historical pass rate, and 1 or 2 real failing examples. It also lists source disagreements, proposed new types and dropped checks. Relay the full screen to the user in chat (do not rely on collapsed tool output).

### 4. Resolve before approval

- Each source disagreement: ask the user to pick a winner, then run `contract_resolve.py <agent> <conflict-id> <option-number>`.
- Each proposed new type: needs explicit user approval. If approved, add the type to the shared `grade.py` (and its header), change the check's type to the new name, and re-run step 3. If refused, remove the check from the draft.
- Re-run `contract_review.py` after any change so the user sees the final screen.

### 5. Approve and freeze

Ask the user to approve the contract (AskUserQuestion: approve / change something). Only on approval:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/contract_freeze.py <agent>
```

This writes `contract.json` with a sha256 fingerprint of every cited file, saves the historical pass rates as `baseline.json` (the free baseline, recomputed by the same engine as `grade.py`), and advances the queue entry to stage `cases`. It refuses while conflicts or proposed types remain. Report the frozen check count and baseline to the user. Commit only `contract.json` and `baseline.json`; history, drafts and fixtures are never committed.

<!-- cases-stage:start -->
## Cases stage

Runs when the queue stage printed in Step 2 is `cases`. Scripts are in `~/.dotfiles/claude-code-shared/scripts/agent-eval/`. Fixtures live in `claude-code-shared/evals/<agent>/fixtures/` (gitignored); they are never committed.

### 1. Import legacy cases (once, only if the agent has them)

If `claude-code-shared/evals/<agent>/cases.jsonl` exists in the old shape (artifact-grounding-judge, persona-accuracy), run once:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/cases_import.py <agent>
```

It keeps every legacy case and its expected answer as an `imported` case and moves the old file to `cases.legacy.jsonl` (the folder's `build_cases.py` and `run-eval.mjs` read that name).

### 2. Freeze real inputs

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/harvest.py <agent>
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/cases_freeze.py <agent>
```

Picks 4 to 6 recent, varied spawns, freezes each prompt and the files it referenced, and writes `cases.lock.json` (sha256 per file) plus `real` cases with the agent's recorded answer as the expected answer. Files that are gone are rebuilt from the spawn's own reads or the parent session log; otherwise the spawn is skipped and the reason is printed. Relay the printed list (cases, skips, `judgment_agent`) to the user in chat.

### 3. Planted defects (only when the script printed `judgment_agent: yes`)

Spawn one Agent with `model: "sonnet"`. Give it the frozen real cases (`cases.jsonl`, each `fixtures/<id>/prompt.txt` and files) and the agent's contract. Have it write `.claude/agent-bench/<agent>/proposals.json`: a JSON list, one entry per planted case, 1 to 2 per real case, each a single find/replace edit that should change the correct answer:
`{"id", "base": "<real case id>", "target": "prompt" or a path under that case's frozen files, "find": "<exact text>", "replace": "<text>", "expected": {<expected answer for the edited input, same shape as the base case's expected>}, "note": "<why the answer changes>"}`. The subagent proposes only; it never edits fixtures.

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/cases_plant.py <agent>
```

The script rejects any edit whose `find` matches zero times or two-plus times (reason printed, never written) and prints a diff of each accepted edit against its clean base. Relay rejections and diffs to the user. If rejected proposals leave too few planted cases, send the reasons back to the Sonnet subagent for one corrected round.

### 4. Verify and advance

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/cases_finish.py advance <agent>
```

Re-hashes every fixture against `cases.lock.json`, checks every case has an expected answer and every planted edit still applies, then advances the queue entry to `iterate`. Commit `cases.jsonl`, `cases.lock.json` and any `cases.legacy.jsonl`; never fixtures, proposals or queue state.
<!-- cases-stage:end -->

<!-- iterate-stage:start -->
## Iterate stage

Runs when the queue stage printed in Step 2 is `iterate`. One invocation is exactly one iteration, then stop; the next iteration is a new invocation. Requires a frozen `contract.json` and cases.

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/iterate.py <agent>
```

The script does everything mechanical:

1. Uses the baseline live traces (made once if absent, from up to 6 real cases plus every planted case), picks the worst failing check (stuck checks skipped), targets up to 3 of its failing cases and up to 2 all-passing sentinel cases.
2. Makes one Sonnet analyzer call that reads only the failing traces and proposes one fix, a script fix considered before a prompt edit. The edit goes to the agent file or a shared script; `find` text must match exactly once.
3. Live-runs only the targets and sentinels with `run.mjs` (sandboxed; the real `unified-learnings.jsonl` is hash-checked), then re-runs the flipped cases a second time.
4. Keep rule: at least half the targets pass the check on both runs (2 of 2), no sentinel regresses on any check, tokens per case within 1.5x baseline (pass `--approve-tokens` only if the user approved a higher cost). Otherwise the edit is reverted with `git checkout` and the script proves the tree is clean.
5. A kept fix is committed by `iterate_commit.py`: one commit `agent-bench(<agent>): <check> fix`, only the agent edit, `contract.json`, `cases.jsonl` and `scores.json`, pushed straight to main via `~/.dotfiles/.scripts/gxpush --push-only --auto`. Refuses off main. `--dry-run` prints the message, file list and commands without running them. Never run the live push without the user's approval of pushing to main.
6. History is `.claude/agent-bench/<agent>/iterations.json` (attempt, decision, reasons, kept SHA). Two reverted fixes in a row on one check mark it stuck; the next invocation moves to the next failing check.

Relay to the user in chat: the check chosen, the analyzer's kind and rationale, per-case results, the decision with reasons, the SHA (or revert), and if a check just became stuck, both attempts.

`iterate.py decide <agent> <n>` recomputes the decision for iteration n from its recorded runs. `iterate.py revert <agent> <n>` reverts iteration n's edit.
<!-- iterate-stage:end -->

<!-- summary:start -->
## Summary page (every invocation)

As the last step of every invocation, whatever stage ran, write the plain-English summary page and print its path:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/agent-eval/summary.py <agent>
```

It writes `.claude/agent-bench/<agent>/summary.html`: stage, the check worked on, checks flipped, kept or reverted, commit SHA, 2-of-2 confirmation, stuck checks, and current pass rates from `scores.json`. Hand the user that path. It is separate from hillclimb and `eval-report.sh`. Never commit the page.
<!-- summary:end -->

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `improve-agent-benchmarks`.
<!-- skill-done: improve-agent-benchmarks -->
<!-- learning-capture:end -->
