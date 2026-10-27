---
name: to-e2e-tasks
description: Analyze branch changes, discover critical user-facing workflows, grill the test plan, and generate a Playwright e2e tasks JSON file for build-code. Output is a tasks JSON plan, not implemented tests. Use when user wants to plan e2e coverage after implementing a feature.
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

# To E2E Tasks

Analyze the current branch's changes, discover which critical user-facing workflows are affected, stress-test the test plan with the user, and output a Playwright e2e tasks JSON file to `docs/tasks/` that `/build-code` can execute. This skill is a planner — it produces a task list, not implemented test code.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version: `"1"`)
**Role:** producer

**Step-0 — validate output after writing:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <output-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not write the file.

## Process

### 1. Detect Playwright setup

Scan the project for existing Playwright configuration:

```
find . -maxdepth 3 -name "playwright.config.*" -o -name "playwright.config.*"
```

**If config exists:** read it to learn:
- Test directory (e.g. `tests/e2e/`, `e2e/`)
- Base URL
- Project names (chromium, firefox, etc.)
- Existing fixtures, helpers, or page objects (scan the test directory)

Record these for use in task descriptions so generated tests follow existing patterns.

**If no config exists:** the first task in the output JSON will be "Scaffold Playwright setup" with acceptance criteria for config, directory structure, and a sample test. All other tasks will be blocked by this one.

### 2. Read context

Fire all of the following in the same turn as the step 1 Playwright config scan (parallel — all are independent):

**a. Project vocabulary and ADRs.** Spawn the `context-loader` agent (`subagent_type: context-loader`, repo root as working directory).

**b. The diff and commit log** (two Bash calls in the same turn):
```
git diff $(git merge-base HEAD origin/main)..HEAD
git log $(git merge-base HEAD origin/main)..HEAD --oneline
```

Wait for context-loader, diff, and commit log results before step 2c.

**c. The source artifact.** Look for an existing task JSON in `docs/tasks/` for the current branch. If found, read its `source` field:
- When `source.type` is `"seed"` or `"prd"` and `source.ref` is non-null: load the artifact at `source.ref` for context (seed or PRD respectively).
- When `source.type` is `"session"` (ref null): fall through to the prompt-or-skip path below.
- When multiple task files exist: ask the user which one corresponds to the current work.

If no task JSON exists, or the source cannot be resolved, ask the user to provide the seed/PRD path directly or skip context.

Use `vocabulary` terms from context-loader in workflow names and test scenario descriptions. If the payload's `missing` list is non-empty, proceed without domain vocabulary.

### 3. Two-pass workflow discovery

#### Pass 1: Scan for user-facing signals

Analyze the diff for changes that affect user-facing behavior:

- Route definitions or page components (new/modified)
- API endpoint handlers
- Form handlers, validation logic
- Authentication or authorization changes
- Payment, billing, or subscription logic
- Data mutations visible to users (CRUD operations)
- Navigation changes, redirects
- Error states shown to users

Ignore purely internal changes: utility refactors, type-only changes, build config, dev tooling.

#### Pass 2: Group into workflows

Cluster the signals from pass 1 into coherent user-facing workflows. Each workflow is a sequence of user actions that achieves a goal.

Examples:
- "User signup flow" (visit signup page, fill form, submit, verify email, land on dashboard)
- "Invoice creation flow" (navigate to invoices, click create, fill details, save, see confirmation)
- "Settings update flow" (open settings, change value, save, see confirmation)

### 4. Present discovered workflows

Show the user what you found. For each workflow:

- **Workflow name**: descriptive, using `vocabulary` terms from the context-loader payload
- **Signals found**: which diff changes map to this workflow
- **Proposed test scenarios**: happy path + key edge cases

```
Discovered workflows from branch changes:

1. Invoice creation flow
   Signals: new route /invoices/new, InvoiceForm component, createInvoice API handler
   Proposed tests:
   - Happy path: create invoice with valid data
   - Validation: submit with missing required fields
   - Edge: create invoice with maximum line items

2. Invoice PDF export
   Signals: new exportPdf handler, PdfPreview component
   Proposed tests:
   - Happy path: export existing invoice as PDF
   - Edge: export invoice with special characters in title
```

Ask:
- Are these the right workflows to cover?
- Any workflows missing?
- Are the test scenarios sufficient? Too many? Too few?
- Any edge cases I should add or remove?

Iterate until the user approves.

### 5. Grill the test plan

Once workflows are approved, stress-test the test plan. Apply the same rigor as `/grill-with-docs` but scoped to the test plan:

**Challenge against the glossary.** If the context-loader payload returned `vocabulary` terms, verify test names and descriptions use those canonical domain terms. "Your test says 'checkout' but the project vocabulary calls it 'order submission'. Which is right?"

**Sharpen fuzzy scenarios.** "You have 'test error handling' but which error? Network timeout? Validation failure? Auth expiry? Be specific."

**Probe edge cases.** Invent scenarios that test boundaries. "What happens if the user double-clicks submit? What if the session expires mid-flow? What about concurrent edits?"

**Cross-reference PRD.** If PRD mentions acceptance criteria not covered by any test scenario, surface the gap. "PRD says 'user sees error toast on failed payment' but no test covers the failed payment path."

**Cross-reference diff.** If the diff contains error handling, branching logic, or state transitions not covered by a test, flag it. "The diff shows a retry loop in the API handler. No test covers the retry path."

**Update CONTEXT.md inline** if new terms crystallize during the grill. Use the format in `~/.dotfiles/claude-code-shared/skills/grill-with-docs/resources/CONTEXT-FORMAT.md`.

Ask questions one at a time. Provide your recommended answer for each. Iterate until the test plan is solid.

### 6. Determine task structure

After the grill, build the task list:

**First task (if page objects/fixtures needed):** "Create page objects and fixtures for {workflow names}." This task sets up reusable helpers: page object classes for pages touched across workflows, shared fixtures for auth state, test data setup, etc. Reference existing patterns found in step 1.

**Remaining tasks:** One task per test scenario. Each task:
- Is type `AFK`
- Is `blocked_by` the fixtures task only (or nothing if no fixtures task)
- Has no dependencies on other test tasks
- Has acceptance criteria describing expected test behavior
- Embeds page objects, fixtures, and workflow context directly in `description` so `/tdd` receives full context (e.g. "Use InvoiceFormPage and InvoiceDetailPage page objects. Setup fixtures: authenticated-user, test-organization. Workflow: invoice creation happy path.")

### 7. Determine the next task ID

Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/next-task-id.sh docs/tasks/` to get the next available ID.

### 8. Confirm output directory

Resolve the absolute path of `docs/tasks/` relative to the current working directory. Ask the user before writing:

```
E2E tasks file will be saved to: /absolute/path/to/docs/tasks/
Is that correct?
```

Use whatever path the user confirms. Create it if it doesn't exist.

### 9. Confirm branching strategy

**MANDATORY.** Follow `~/.dotfiles/claude-code-shared/resources/branching-strategy.md` for how to present the choice and record the result in the `branching` field.

Also confirm: should the JSON file itself be saved on the current branch, or on the task branch? Default to current branch.

### 10. Write the JSON file

Derive the slug from the PRD filename if available (strip timestamp prefix and extension). If no PRD, derive from the branch name.

Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/task-filename.sh e2e-<slug>` to generate the filename.

If a file for this slug already exists (any prefix), ask whether to overwrite or merge (same logic as `/to-tasks`).

Read the canonical schema now:
```bash
cat ~/.dotfiles/claude-code-shared/contracts/task-schema.json
```
Use that schema exactly. Do not guess field names or structure.

Set `"producer": "to-e2e-tasks"`. Add one e2e-specific top-level field alongside the standard fields:
- `"source_branch"`: the branch name these tests were generated from.

HITL tasks (rare — e.g. "provision Playwright auth credentials") must be hands-only: a keyboard action the AI cannot perform. Never emit a decision-review HITL task.

Tell the user the output path and ID range once written.

Output the handoff block:

```
Next steps:
```

Then run:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py to-e2e-tasks
```

Append that output under the Next steps header. Do not hardcode skill names.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `to-e2e-tasks`.
<!-- skill-done: to-e2e-tasks -->
  - `/dispatch-tasks` — e2e tasks file is written and ready to implement
<!-- learning-capture:end -->
