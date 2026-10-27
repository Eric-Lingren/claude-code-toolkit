---
name: improve-skill-benchmarks
description: >
  Self-improving skill evaluator. Runs a target skill against synthetic scenarios,
  scores output with 5-tier rubric via multi-judge LLM panel, and iteratively improves
  the skill through a learning loop. Also analyzes skill structure for optimization
  opportunities. Use when user says "improve skill", "eval skill", "run evals",
  "optimize skill", "self-improve", or invokes /improve-skill-benchmarks.
model: sonnet
effort: high
invokedBy: human
---

# Improve Skill

You are running an automated skill improvement loop. Follow this process exactly.

## Arguments

The user provides: `/improve-skill-benchmarks <skill-name>` with optional `--reset` flag.

Parse the skill name from ARGUMENTS. If `--reset` is present, delete the runs directory for this skill before proceeding.

## Step 1: Locate the target skill

Search this directory for a folder matching `<skill-name>`:
`~/.dotfiles/claude-code-shared/skills/<skill-name>/SKILL.md`

If not found, tell the user and stop.

Read the target SKILL.md. Store its full contents for later use.

Derive the runs directory for this skill: `~/.dotfiles/claude-code-shared/skills/improve-skill-benchmarks/runs/<skill-name>/`. All eval artifacts (eval.json, learnings.md, scores.json) live here, not in the target skill's own directory.

## Step 2: Check for existing evals

Run the scaffold script to check/create the runs directory:
`~/.dotfiles/claude-code-shared/skills/improve-skill-benchmarks/scripts/scaffold-evals.sh <skill-name> [--reset]`

Then check for `<runs-dir>/eval.json`.

- If it exists and `--reset` was NOT passed, skip to **Step 4**.
- Otherwise, proceed to **Step 3**.

## Step 3: Scaffold eval.json

This step creates the evaluation definition. Two phases.

### Phase A: Auto-generate from SKILL.md

Read the target SKILL.md and extract every concrete behavioral rule, constraint, or requirement. For each one, create a candidate assertion with a 5-tier rubric. See `resources/eval-json-example.md` for the assertion and scenario JSON formats.

Each rubric tier must be specific and anchored to observable behavior. No vague language like "mostly" or "partially."

Each assertion may include two optional scoring fields:
- `weight` (number, default `1.0`): multiplier applied to the assertion's score when computing the weighted average. Use `2.0` for critical behaviors, `0.5` for low-priority style checks.
- `hard_gate` (boolean, default `false`): if `true`, a score of 25 or below on this assertion forces the entire scenario score to zero, regardless of other cells. Use for must-never-fail behaviors (e.g. schema validation, no-fabrication).

Built-in assertions always use weight `1.0` and `hard_gate: false`.

**Always include the 3 built-in consistency assertions.** Read them from `resources/builtin-assertions.md` in this skill's directory. The exact names are `number_consistency`, `no_contradictions`, `plan_cohesion`. Never rename or substitute them. Copy the EXACT rubric descriptions from `resources/builtin-assertions.md` verbatim. Never adapt rubric text to the target skill domain context.

Also generate 5 synthetic scenarios. Each scenario should:
- Be a realistic task the skill would handle
- Vary in complexity (simple to complex)
- Cover different aspects of the skill's behavior

### Phase B: Grill user on gaps

Present the auto-generated assertions and scenarios to the user. Ask exactly these 2 questions:

1. "Here are the assertions and rubrics I extracted. Any behaviors I missed? Any rubric anchors need adjustment?"
2. "Here are the 5 scenarios. Do these cover enough variety?"

Do not ask additional questions. Incorporate feedback, then write the final eval.json to `<runs-dir>/eval.json`.

## Step 4: Run the improvement loop

Read these files:
- `<target-skill-dir>/SKILL.md` (current skill definition)
- `<runs-dir>/eval.json` (scenarios + assertions)
- `<runs-dir>/learnings.md` (if exists, prior learnings)
- `<runs-dir>/scores.json` (if exists, prior scores)

Initialize iteration counter at 1. Max iterations: 3.

**File re-read guard:** SKILL.md, eval.json, and scores.json are read once before the loop begins and do not change during iteration (unless a promotion fires in Step 4e). Do not re-read them each iteration. Re-read learnings.md after Step 4d. Re-read SKILL.md after Step 4e only if a promotion was made.

### For each iteration:

#### 4a-4b-2: Run iteration runner

Spawn one Agent (subagent_type: "general-purpose", model: "sonnet") per iteration using the role defined in `resources/iteration-runner-prompt.md`. Pass it:
- `SKILL_MD`: current SKILL.md contents
- `LEARNINGS_MD`: current learnings.md contents (empty string if none)
- `EVAL_JSON`: full eval.json object
- `ITERATION`: current iteration number

The iteration runner internally executes all scenario simulations (sonnet), all judge panels (haiku), and the consistency cross-check (sonnet). It returns a compact JSON object: `{iteration, cell_scores[], failure_reasons[]}`.

Session model receives only this compact JSON. Raw scenario execution outputs do not accumulate in session context. For every entry in `failure_reasons`, show the reason string in the iteration report. Do not skip, even when the cause is obvious from the scenario context.

#### 4c: Generate report

Build a report with:

1. **Header:** `Iteration N/3 - Score: X/100 - prev: Y/100`
   - Score = weighted average of all snapped cell scores across all (scenario, assertion) pairs.
   - For each cell: `cell_contribution = score * assertion.weight` (default weight 1.0 if absent).
   - Apply hard gates first: if any assertion with `hard_gate: true` scores 25 or below in a scenario, all cells in that scenario are set to 0 before computing the average.
   - Weighted average = sum(cell_contribution) / sum(weights across all cells). State this single number in the header and nowhere else.
   - When showing cell sums, list each addend (with its weight multiplier) explicitly before summing. Do not estimate for matrices with more than 10 cells.

2. **Matrix table:** A scenario x assertion grid showing individual cell tier scores.

3. **Failure details:** For each cell scoring 25 or below, include scenario name, assertion name, score, collected judge reasons, and a specific improvement suggestion.

4. **Low scores:** For each cell scoring 50, same format as failures.

#### 4d: Update learnings.md

For each cell scoring 50 or below, append a dated entry to `<runs-dir>/learnings.md`:

```markdown
## <date> - Iteration <N>
- **<assertion_name>** [<score>]: <what went wrong and how to fix it>
```

#### 4e: Check for promotions

Review scores.json history. If any assertion has scored 25 or below in 2 consecutive iterations (current + previous, can span runs), promote the fix:

1. Read current SKILL.md
2. Identify where the improvement should be inserted
3. Edit SKILL.md to incorporate the fix
4. Remove the promoted learning from learnings.md

#### 4f: Prune stale learnings

Remove any learning entries older than 2 iterations that have not been associated with a low score in recent runs.

#### 4g: Check exit conditions

Exit conditions are strict. Apply them in order. Never override based on judgment.

- If average score >= 85, stop the loop immediately. Exit reason: "strong_score".
- If iteration >= 3 and score delta < 5 compared to previous iteration, stop. Exit reason: "plateau".
- If iteration counter reaches 3, stop. Exit reason: "max_iterations".
- Otherwise, increment iteration counter and repeat from 4a.

## Step 5: Structural analysis

After the eval loop completes, analyze the target skill's directory structure against this checklist. Each item is binary pass/fail.

### Checklist items (exactly these 7)

**1. Token weight:** Run `scripts/count-tokens.sh <skill-name>` to get the token estimate. Pass if output <= 5000. Fail if over. Report the count.

**2. Inline knowledge extraction:** Scan SKILL.md for prose blocks over 200 words. Pass if none found. Fail if found. List candidates with suggested filenames.

**3. Example extraction:** Scan SKILL.md for inline code examples or sample outputs longer than 10 lines. Pass if none found. Fail if found.

**4. Missing resource files:** Identify concepts SKILL.md references 3+ times but never defines. Pass if none. Fail if found.

**5. Script candidates:** Identify deterministic steps (file scaffolding, directory creation, JSON validation, file listing/counting) that could be shell scripts. Pass if none. Fail if found.

**6. Misplaced files:** Read the directory conventions from `~/.dotfiles/claude-code-shared/resources/skill-directory-conventions.md`. Scan all non-SKILL.md files at the skill root. Classify each by file type and check if it belongs in a canonical subdirectory (`scripts/`, `resources/`, or `assets/`). Pass if all files are in their canonical locations. Fail if any file is misplaced. List each misplaced file with its current location and target directory.

**7. Stale references:** After identifying misplaced files, run `scripts/check-stale-refs.sh <old-path>` for each misplaced file's current path. The script outputs `file:line:match` hits, one per line. Pass if no hits. Fail if hits exist. List each reference from the script output.

### Auto-fix for items 6 and 7

If either item 6 or 7 fails, present the user with a single confirmation prompt listing all proposed changes:

1. Files to move (old path -> new path)
2. References to update (file:line, old ref -> new ref)

On confirmation:
- Create any missing subdirectories
- Move each file to its canonical location
- Update every reference found by the grep across all of `~/.dotfiles/claude-code-shared/`
- Re-verify no broken references remain after the moves

Do not proceed without user confirmation. Do not move files one at a time. Batch all moves and reference updates into a single operation.

### Structural score

Score = (items passed / 7) * 100. This score is independent of the behavioral score.

## Step 6: Architecture audit

Follow the steps in `resources/architecture-audit-steps.md`.

## Step 7: Write scores.json

Append the run result to `<runs-dir>/scores.json`. See `resources/scores-json-schema.md` for the expected format.

## Step 8: Final report

Display results to the user with exactly these 9 sections:

1. **Run summary:** Start score, end score, iterations run, exit reason
2. **Score progression:** Each iteration's average score on one line
3. **Final matrix:** The last iteration's full scenario x assertion grid with tier scores
4. **Failure details:** From the last iteration, cells scoring 25 or below with suggestions
5. **Low scores:** Cells scoring 50 with suggestions
6. **Structural analysis:** Structural score (X/100), each checklist item pass/fail with detail, recommendations
7. **Architecture analysis:** Architecture score (X/100), per-signal A1–A8 pass/fail table, confirmed findings with lifecycle labels (NEW / PERSISTING / RESOLVED), propose-only recommendations
8. **Changes made:** Learnings added, promotions made, learnings pruned
9. **Recommendation:** What to focus on next

## Important rules

- Run all iterations autonomously. Do not pause for user approval between iterations.
- Only pause during Step 3 (eval scaffolding) to get user input on assertions, rubrics, and scenarios.
- Use `model: "haiku"` for judge subagents to minimize token cost.
- Use `model: "sonnet"` for skill execution subagents for balanced quality/cost.
- Spawn all 3 judges for a cell in parallel.
- Spawn all 3 architecture panel judges for a finding in parallel (Step 6b).
- Architecture pillar runs always. It has no opt-out flag.
- Architecture scoring stays on the session model (Step 6c). The auditor agent detects and reports only.
- When editing SKILL.md for promotions, make minimal surgical changes. Do not rewrite the whole file.
- If learnings.md does not exist, create it on first write.
- If scores.json does not exist, create it with an empty `runs` array.
- Keep judge prompts minimal. The judge scores against rubric anchors only.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `improve-skill-benchmarks`.
<!-- skill-done: improve-skill-benchmarks -->
<!-- learning-capture:end -->
