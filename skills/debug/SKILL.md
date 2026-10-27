---
name: debug
description: Disciplined diagnosis loop for hard bugs and performance regressions. Reproduce → minimise → hypothesise → instrument → fix → regression-test. Use when user says "diagnose this" / "debug this", reports a bug, says something is broken/throwing/failing, or describes a performance regression.
model: opus
effort: xhigh
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

# Diagnose

A discipline for hard bugs. Skip phases only when explicitly justified.

## Contract

**Format (conditional output):** task file — see `contracts/task-contract.md` (schema_version: `"1"`)
**Role:** conditional producer (produces a task file only for non-inline fixes; inline single-file fixes are applied directly)

**Step-0 fires only when a tasks file is actually written:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <output-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not write the file.

When exploring the codebase, use the project's domain glossary to get a clear mental model of the relevant modules, and check ADRs in the area you're touching.

## Complexity gate

Before entering any phase, assess the bug:

- Is the root cause already visible in the code (typo, wrong constant, obvious off-by-one)?
- Is the fix 1 file, under 10 lines?
- Can you write the failing test and fix in one step?

If all three are true: jump directly to Phase 4 (write tasks). No feedback loop required. State the skip explicitly and explain why.

## Scope gate: bug vs. design change

Debug writes task files only for **bugs**: code that does not do what it was built to do, where the correct behavior is already settled. Re-check this at every phase boundary, not only at the start. Diagnosis often shows the code works as built and the real problem is a design choice.

The work has become a **design change** when any of these is true:

- The code behaves as built, and the fix changes intended behavior, UX, copy, flow, or layout.
- The fix requires picking between two or more options with real trade-offs (you presented options, or the user asked for them).
- The fix adds, removes, or swaps a feature, integration, or third-party component (e.g. turning off a payment method, replacing an input).
- The fix needs a decision a future reader would ask "why?" about, and that rationale lives only in this conversation.

When the gate trips:

1. Say so explicitly: `Scope gate: this is a design change, not a bug. Handing off to /to-seed.`
2. Do **not** write a task file. Do not run Phase 4.
3. Keep any diagnosis findings (root cause of the symptom, file:line seams, rejected options and why) in the conversation so `/to-seed` can capture them.
4. Tell the user the next steps: `/to-seed` to record the decision and rationale, then `/to-tasks` to produce the task file.

Mixed sessions are allowed. If one confirmed root cause is a real bug and another is a design change, write tasks only for the bug and hand the design change to `/to-seed`. A config-only fix with no code change (e.g. a dashboard setting) produces no task file either way.

## Observation log

Maintain a running scratchpad throughout all phases. Format:

```
Tried: <what you did>
Ruled out: <hypothesis or approach, and why>
Pending: <next probe>
```

Update after every significant action. This log feeds Phase 2 hypothesis ranking, the Phase 3 exit summary, and the PR description.

## Phase 1 — Build a feedback loop

**This is the skill.** Everything else is mechanical. If you have a fast, deterministic, agent-runnable pass/fail signal for the bug, you will find the cause — bisection, hypothesis-testing, and instrumentation all just consume that signal. If you don't have one, no amount of staring at code will save you.

Spend disproportionate effort here. **Be aggressive. Be creative. Refuse to give up.**

### Ways to construct one — try them in roughly this order

**Delegate the search.** Before writing the loop, you need to locate the code path, find existing tests, and map the module. These are read-only lookups. Spawn `caveman:cavecrew-investigator` (Haiku) for them. Keep the results. Write the loop yourself on the session model.

1. **Failing test** at whatever seam reaches the bug — unit, integration, e2e.
2. **Curl / HTTP script** against a running dev server.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser check via browser-checker agent** — spawn the `browser-checker` agent (see `agents/build/browser-checker.md`) with launch context resolved per `~/.dotfiles/claude-code-shared/resources/app-launch-detection.md`. Derive a minimal Check Spec from the route under investigation: a `goto` to the route, a `waitFor networkidle`, one `capture` step, and `expected_visual_change: null` (diagnostic, not a planned intentional change). Pass: `spec` (the Check Spec JSON), `base_url` (candidate server URL resolved per app-launch-detection.md), `repo` (Org/Repo string from the target repo directory, e.g. `Eric-Lingren/SpawnedSapien`), `run_dir` (create a directory under `docs/browser-checks/` for this debug session), and `base_sha` (current merge-base SHA). Omit `base_server_url` — debug is diagnostic and does not require baseline comparison. Feed the JSON result (browser-check-result v2, see `~/.dotfiles/claude-code-shared/resources/browser-check-result.md`) directly into the observation log. Evolve the Check Spec's steps as hypotheses sharpen; re-spawn with the updated spec for each hypothesis test. The agent is stateless — the debug skill owns the retry loop and server lifecycle. CDP MCP (Chrome DevTools) is reserved for live interactive inspection in Phase 3; do not mix it into the browser-checker agent.
5. **Replay a captured trace.** Save a real network request / payload / event log to disk; replay it through the code path in isolation.
6. **Throwaway harness.** Spin up a minimal subset of the system (one service, mocked deps) that exercises the bug code path with a single function call.
7. **Property / fuzz loop.** If the bug is "sometimes wrong output", run 1000 random inputs and look for the failure mode.
8. **Bisection harness.** If the bug appeared between two known states (commit, dataset, version), automate "boot at state X, check, repeat" so you can `git bisect run` it.
9. **Differential loop.** Run the same input through old-version vs new-version (or two configs) and diff outputs.
10. **HITL loop.** Last resort. If a human must click, drive _them_ with a structured script so the loop is still structured. Captured output feeds back to you.

**Test file requirement.** The feedback loop test must be written to a real test file — not a scratch script, not a REPL session. Record the path. This test becomes `acceptance_criteria[0]` in the Phase 4 task.

### Iterate on the loop itself

Treat the loop as a product. Once you have _a_ loop, ask:

- Can I make it faster? (Cache setup, skip unrelated init, narrow the test scope.)
- Can I make the signal sharper? (Assert on the specific symptom, not "didn't crash".)
- Can I make it more deterministic? (Pin time, seed RNG, isolate filesystem, freeze network.)

A 30-second flaky loop is barely better than no loop. A 2-second deterministic loop is a debugging superpower.

### Non-deterministic bugs

The goal is not a clean repro but a **higher reproduction rate**. Loop the trigger 100×, parallelise, add stress, narrow timing windows, inject sleeps. A 50%-flake bug is debuggable; 1% is not — keep raising the rate until it's debuggable.

### When you genuinely cannot build a loop

Stop and say so explicitly. List what you tried. Ask the user for: (a) access to whatever environment reproduces it, (b) a captured artifact (HAR file, log dump, core dump, screen recording with timestamps), or (c) permission to add temporary production instrumentation. Do **not** proceed to hypothesise without a loop.

### Phase 1 exit gate

Before moving to Phase 2, confirm all of the following:

- [ ] Loop produces the failure mode the **user** described — not a different failure that happens to be nearby. Wrong bug = wrong fix.
- [ ] Failure is reproducible across multiple runs (or, for non-deterministic bugs, at a high enough rate to debug against).
- [ ] Exact symptom captured (error message, wrong output, slow timing) so later phases can verify the fix actually addresses it.
- [ ] Failing test written to a real test file at `<path>` — not a scratch script.

Do not proceed until all four are confirmed.

## Phase 2 — Hypothesise

Generate **3–5 ranked hypotheses** before testing any of them. Single-hypothesis generation anchors on the first plausible idea.

Each hypothesis must be **falsifiable**: state the prediction it makes.

> Format: "If <X> is the cause, then <changing Y> will make the bug disappear / <changing Z> will make it worse."

If you cannot state the prediction, the hypothesis is a vibe — discard or sharpen it.

**Show the ranked list to the user before testing.** They often have domain knowledge that re-ranks instantly ("we just deployed a change to #3"), or know hypotheses they've already ruled out. Wait up to 60 seconds for a response. If no response, proceed with your ranking and note it in the observation log.

## Phase 3 — Instrument

**Capture a test baseline before touching any code.** Run the full test suite. Record:

```
Baseline: <N> passing, <M> failing, <K> skipped
Suite command: <command used>
```

Do not add any instrumentation until this is captured.

Each probe must map to a specific prediction from Phase 2. **Change one variable at a time.**

**Delegate the search.** Locating call sites, tracing data flow, finding config values, and mapping module boundaries are read-only lookups. Spawn `caveman:cavecrew-investigator` (Haiku) for each search before adding instrumentation yourself.

Tool preference:

1. **Debugger / REPL inspection** if the env supports it. One breakpoint beats ten logs.
2. **Chrome DevTools MCP** — use this for live interactive browser inspection in the main session when an automated repro already exists but live browser state must be observed: network waterfall, memory profile, live console, live DOM inspection. CDP MCP runs in the main session only. Do not use it inside the browser-checker subagent.
3. **Targeted logs** at the boundaries that distinguish hypotheses.
4. Never "log everything and grep".

**Tag every debug log** with a unique prefix, e.g. `[DEBUG-a4f2]`. Cleanup at the end becomes a single grep. Untagged logs survive; tagged logs die.

**Perf branch.** For performance regressions, logs are usually wrong. Instead: establish a baseline measurement (timing harness, `performance.now()`, profiler, query plan), then bisect. Measure first, fix second.

**Enumerate every writer to shared state.** If the suspected root cause involves shared state (form library state, a store, a context, a cache), do not stop at the one write path you analyzed. Delegate the search to `caveman:cavecrew-investigator` (Haiku). Have it find every writer to that state across all components that share it. Check each writer for side effects that undo the fix. Example: in Formik, with `validateOnChange` on (the default) and no `validate` or `validationSchema`, any `setFieldValue` call resets errors set via `setFieldError` to `{}`.

### Phase 3 exit gate

Before moving to Phase 4, complete both steps.

**Step 1: Suite regression check.** Re-run the full test suite. Compare to the baseline captured at Phase 3 entry.

```
Baseline: <N> passing, <M> failing
Current:  <N'> passing, <M'> failing
Delta: <any new failures?>
```

If there are new failures: your instrumentation caused a regression. Fix it before proceeding. Do not move to Phase 4 with a dirty suite.

**Step 2: Root cause summary.** Output this explicitly to the user:

```
Root cause confirmed: <one sentence per bug>
Hypothesis that won: <from Phase 2>
Proposed fix branch: fix/<slug>
```

Do not proceed to Phase 4 until both steps are complete.

## Phase 4 — Write fix tasks, then stop

**The debug skill does not apply any fix. It writes a tasks file and stops. All implementation happens in a separate `/build-code` session.**

**Before Step 1, re-run the Scope gate.** If any confirmed root cause is a design change, not a bug, it does not get a task here. Hand it to `/to-seed` per the Scope gate section. Proceed with Phase 4 only for the remaining bugs.

### Step 1: Branch

**gxcheck pre-flight:** Before asking the user, run `~/.dotfiles/.scripts/gxcheck` and surface its output as a brief status block (e.g. `Branch check: OK: branch looks clean`). This is advisory only — the skill continues regardless of the output.

Run `git branch --show-current`. Then ask the user:

```
Branching strategy:
1. Single branch for all tasks (you provide the name) — best for a focused fix
2. Per-task branches (auto-generated) — best for independent bugs reviewed separately

Which do you prefer?
```

For most debug sessions a single `fix/<slug>` branch is the right choice. If the user is on `main` or `master`, push strongly toward switching to a fix branch.

If single: ask "Branch name?" and suggest `fix/<slug>` derived from the confirmed root cause. If per-task: derive branch names per the format in branching-strategy.md.

**Wait for user response before continuing.**

See `~/.dotfiles/claude-code-shared/resources/branching-strategy.md` for branch naming rules, derivation format, and JSON recording format.

### Step 2: Write the tasks file

Read the canonical schema now:
```bash
cat ~/.dotfiles/claude-code-shared/contracts/task-schema.json
```
Use that schema exactly. Do not guess field names or structure.

**Multi-bug splitting rule.** If multiple root causes are confirmed:
- Independent bugs (different files, different call paths): write one task per bug.
- Coupled bugs (shared state, same callsite): write one task with compound acceptance criteria, and note the coupling explicitly in the description.

Each task must be self-contained. A future `/build-code` session has no memory of this diagnosis. Embed the full context in each task `description` using this pattern:

```
Root cause: <confirmed cause>. Failing scenario: <minimised repro>. Test seam: <file:line — or 'no correct seam: reason'>. Failing test: <path from Phase 1 exit gate>. Fix approach: <what to change and why>.
```

The first acceptance criterion must always be: `"Failing test exists at <path from Phase 1> that reproduces the bug before any fix is applied"`.

**Behavioral parity with a reference component.** If a fix task touches a component that has a sibling or reference with an established pattern (e.g. a sibling form cell), or the description says "same pattern as X", read the reference first. Add acceptance criteria that require behavioral parity, not just type or compile parity. Check these dimensions: controlled vs uncontrolled input, save trigger (onChange vs onBlur), debounce or latest-save guards, and conditional save guards (e.g. preview or readonly flags). Compile-only criteria are not enough here. Enumerate each behavioral guard in the reference, such as an early-return flag before a save or mutation. Write one acceptance criterion per guard, not one criterion total. Each guard also needs a test case with the guard condition active (e.g. the flag set to true) that asserts the guarded side effect does NOT fire. Tests that only render the default or false path do not count.

**Preserve identity stability when replacing module-level definitions or removing a context provider.** A module-level constant, component, or factory often exists to keep a stable reference. Examples include a TanStack Table cell factory, a React component definition, or a memoized callback. If a fix removes or replaces one, add this acceptance criterion: `Replacement functions and components are defined at module scope or behind a stable reference. Verify that the reference does not change between renders when upstream state (e.g., feature flags, fetched data) updates.` A context provider can give the same guarantee. It lets values reach consumers without prop-threading, so cell components can stay at module scope. If a fix removes such a provider (e.g. one wrapping table cells), the same criterion applies. Threading the values via closure is the failure mode here. An inline arrow inside a `useMemo` that depends on upstream state fails this check. It causes remounts, such as cells losing focus mid-edit.

**Check when a data source is populated before recommending it.** Before the Fix approach says where a component reads data, check when that source gets written. A source written by a deferred effect (useEffect, async load, a form-state mirror) is undefined on first render. IDs and other stable identity data used in API calls must come from a synchronous source, such as props or the row object. Use reactive form state only for values that must react to edits. Never recommend a placeholder fallback such as `{ id: 0 }` for a missing ID, because it silently hits a real endpoint. Optionally add the acceptance criterion `The mutation uses a real ID on first render.`

**"No testable seam" is a claim, not a default.** Before asserting it, you must attempt to write a test. The following bug types have testable seams even when they appear visual:

- Conditional renders based on auth state or flags → RTL `render()` + `screen.queryBy*` with mocked auth context
- Query `enabled` flags based on user state → mock the hook and assert `enabled` value
- Component receives wrong prop values → RTL render with test user state, assert rendered output

The "no testable seam" exemption is only valid for bugs whose failure mode is a **CSS visual property difference** (e.g. `blur(3px)` vs `background: gray`) that cannot be asserted in a DOM test. Conditional render logic, query gating, and prop threading are always testable.

If a seam genuinely does not exist, set the first acceptance criterion to: `"Visual regression verified manually — no automated test seam exists because <specific reason why DOM testing cannot reach this failure>"`. You must state why, not just that.

If a seam exists but Phase 1 could not build a feedback loop, set the first acceptance criterion to: `"Failing test written at <path> — seam at <file:line>"` using a test you write now.

**Get the next task ID:** Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/next-task-id.sh docs/tasks/`

**Generate the filename:** Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/task-filename.sh debug-<slug>`

Write to `docs/tasks/<filename>`.

See `~/.dotfiles/claude-code-shared/resources/branching-strategy.md` for JSON recording format.

HITL tasks from debug (rare — e.g. "enable the feature flag to expose the buggy code path") must be hands-only: a keyboard action the AI cannot perform. Never emit a decision-review HITL task. Never emit a follow-up for manual testing, verification, or confirming the fix looks right — that is part of the natural workflow, not a follow-up.

Set `"producer": "debug"` on the root object. Set `"source": {"type": "session", "ref": null}`. Follow all field rules from the schema above.

Set `follow_ups` to `[]` unless the diagnosis surfaced a genuine irreducible HITL action needed to ship the fix (rare — e.g. a production migration that must be manually triggered, a secret that must be rotated). **Do NOT emit a follow-up for debug cleanup, instrumentation removal, the post-mortem, or re-running tests.** That work is AFK and is run automatically by build-code's end-of-run cleanup step when it sees `producer: "debug"` (see Phase 5). Follow-ups are reserved for real human-on-a-keyboard work, not for cleanup the AI performs itself.

**browser_verify note:** Populate `browser_verify` on each fix task for any bug that manifested as a user-visible UI issue. The `browser_verify` field is a Check Spec JSON object (see `contracts/check-spec.md`). Use the spec you developed in the Phase 1 headless browser feedback loop, with `expected_visual_change: null` — the fix's visual outcome is diagnostic only and has not been declared intentional. Omit `browser_verify` for pure backend or non-UI bugs.

### Step 3: Stop and hand off

After the file is written, output:

```
Tasks written: docs/tasks/<filename>
Tasks: <T-XXXX list>

Next steps:
```

Then run:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py debug
```

Append that output (one `/skill — when` line per edge) under the Next steps header. Do not hardcode skill names.

**Phase 4 is complete. Do not open any source file. Do not write any fix code. The debug skill is done.**

## Phase 5 — Cleanup + post-mortem

Run this after `/build-code` completes and all tasks are `done`. build-code auto-invokes this cleanup at end-of-run whenever the tasks file has `producer: "debug"` — there is no cleanup follow-up entry to trigger it. If the run was executed some other way, invoke this phase manually.

**Run full test suite before cleanup.** Capture:

```
Pre-cleanup: <N> passing, <M> failing
```

Required before declaring done:

- [ ] All `[DEBUG-...]` instrumentation removed (`grep` the prefix)
- [ ] Throwaway prototypes deleted (or moved to a clearly-marked debug location)
- [ ] The winning hypothesis is stated in the PR description so the next debugger learns

**Run full test suite after cleanup.** Compare to pre-cleanup baseline:

```
Post-cleanup: <N'> passing, <M'> failing
Delta: <must be clean — no new failures>
```

If new failures appear: your cleanup caused a regression. Fix before proceeding.

<!-- attribution-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/attribution-capture.md`.
<!-- attribution-capture:end -->

**Both blocks are mandatory and independent.** Running attribution-capture does NOT discharge learning-capture. After attribution-tracer finishes, still spawn `capture-learning` as the final action of the terminal turn.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `debug`.
<!-- skill-done: debug -->
  - `/dispatch-tasks` — tasks file is written and ready to execute fixes
  - `/run-task-followups` — build-code is done and FU-001 cleanup is ready
  - `/to-e2e-tasks` — want e2e coverage after fixes land (optional)
<!-- learning-capture:end -->
