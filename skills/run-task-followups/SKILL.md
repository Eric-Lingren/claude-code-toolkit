---
name: run-task-followups
description: Wizard-style interactive walkthrough of manual follow-up actions from a tasks JSON file. Guides user through each follow-up one at a time with concrete steps (exact URL, click, value), progress framing, and confirm gates on irreversible actions. Enriches steps with codebase context, offers help when stuck, and captures friction to improve shared runbooks. For steps that capture secrets or write .env / gh values, generates a bundled bash snippet so secrets never pass through chat. Use when user wants guided help completing manual follow-ups after build-code.
model: sonnet
effort: medium
invokedBy: human
---

# Run Follow-ups

Interactive walkthrough of manual follow-ups from a `docs/tasks/` JSON file. Reads the `follow_ups` array and guides the user through each item one at a time.

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

### 1. Ask for task file

List ALL `*.json` files in `docs/tasks/`. Present as numbered options. Ask the user to pick one.

Read the chosen JSON file. If `follow_ups` is empty or missing, tell the user there are no follow-ups and exit.

### 2. Present the follow-up queue

Print a numbered summary of all follow-ups:

```
Follow-ups for: docs/tasks/20260512-1423-user-auth-flow.json

 #   Title                              Source     Trigger
 ──   ──────────────────────────────     ────────   ───────
 1    Add STRIPE_KEY to Cloudflare       discovered T-0024
 2    Run database migration             planned    T-0023
 3    Visual test: login flow            planned    T-0024

3 follow-ups total. Starting from #1 of 3. Say "skip" to jump to the next item.
```

Count the queue and hold that total. Every item you present announces its position as `N of M` so the user always knows how far they are and how much is left.

### 3. Walk through each follow-up

#### 0. Generate the wizard snippet for secret/env steps

Before the live walk, scan every follow-up and its steps. Flag any step that **captures a secret** (API key, token, password, `sk_`/`pk_` value) or **writes** `.env` / `gh secret` / `gh variable`. These are **wizard steps**. Never ask for a secret value in chat. A pasted secret lands in the session transcript.

If there are no wizard steps, skip this sub-step entirely and start the walk.

If there is at least one wizard step, generate ONE bundled script for all of them:

1. Derive the slug from the task file basename (drop the `.json`). Target path: `docs/wizard/<taskfile-slug>.sh`.
2. Copy `~/.dotfiles/claude-code-shared/resources/wizard-snippet-template.sh` to that path verbatim. **Never hand-edit the library above the `STAGES` marker.**
3. Below the marker, author one `stage` per wizard step, in dependency order, using the enriched (wizard-grade) instructions. Use the library helpers: `stage`, `say`/`step`, `open_url`, `ask`/`ask_secret`, `write_env`, `set_secret`/`set_var`, `pause`/`confirm`. Set `TOTAL_STAGES` to the count.
4. **HARD RULE:** the file is committed to `docs/wizard/`. It must contain NO literal secret value. Every secret is captured at runtime via `ask_secret`. Never write a real key into a `write_env`/`set_secret` argument.
5. Validate: `bash -n docs/wizard/<taskfile-slug>.sh`. Run `shellcheck` if available. `chmod +x` it. Do not run it yourself; it opens browsers and blocks on human input.
6. Tell the user the script exists and how to run it: `bash docs/wizard/<taskfile-slug>.sh`. During the live walk, when you reach a wizard step, do not ask for the value. Point the user at the script and mark the item done once they confirm they ran it.

The script is committed as a durable artifact. `clean-scaffolding` leaves `docs/wizard/` untouched.

#### Then, for each follow-up in order:

#### a. Enrich steps before presenting

Before showing the follow-up to the user, enrich its steps:

1. Read `~/.dotfiles/claude-code-shared/resources/hitl-steps-runbooks.md`.
2. Find a matching template by title keyword or category.
   - **If matched:** run the template's `Enrichment:` instructions. Grep the codebase, read config files, check diffs. Replace vague steps with specific values found.
   - **If no match:** run generic enrichment. Grep the codebase for keywords from the follow-up title. Surface relevant file paths, env var names, config keys, table/column names. Replace any step that says "update X" or "add Y" with the exact command, SQL, or dashboard path.
3. Every step must pass the quality bar from hitl-steps-runbooks.md, at **wizard grade**: each step reads as `open this exact URL → do this exact click → copy this exact value into this exact variable`. No step may say "update X" or "add Y" without the where and how. If it still does, rewrite it with the best info available from the codebase. When a step has a URL, present the URL before asking for its value.

#### b. Present the enriched steps

Print the title and numbered steps clearly:

```
Follow-up 1 of 3: Add STRIPE_KEY to Cloudflare
Source: discovered (from T-0024)

Steps:
  1. Go to Cloudflare dashboard > Workers & Pages > your-app > Settings > Variables
  2. Click 'Add variable'
  3. Name: STRIPE_KEY, Value: from Stripe dashboard > API keys > Secret key (sk_live_...)
  4. Click 'Encrypt' then 'Save'

Done? (yes / skip / help)
```

Present only the current follow-up. Do not dump later items' steps inline. One task on screen keeps the user oriented.

**Irreversible steps get a confirm gate.** If the follow-up runs a migration, a cutover, a delete, or any action that is hard to undo, do not accept a bare "done". First restate what is about to happen and ask an explicit y/N:

```
This step runs the production database migration. It is not easily reversible.
Proceed? (y / N)
```

Only continue on an affirmative. On "N", treat as skip.

#### c. Handle user response

- **"yes" or "done"**: Mark complete, move to next.
- **"skip"**: Move to next without marking complete.
- **"help"** or any question: Assist the user. Provide additional context, clarify steps, troubleshoot issues. Stay on this follow-up until resolved.

#### d. Detect friction

If the user asks for help, reports confusion, or corrects a step, this is a friction signal. After resolving the issue, ask:

```
The steps for this type of follow-up were unclear/incorrect.
Want me to update the shared runbooks so this is better next time? (yes / no)
```

If yes, proceed to step 4 (template update) for this item before continuing.

#### e. Offer template creation for unmatched follow-ups

If no template matched this follow-up (step 3a used generic enrichment), offer after the user completes it:

```
No template exists for this type of follow-up.
Want me to create one so future follow-ups like this are more specific? (yes / no)
```

If yes, proceed to step 4 to create a new runbook with an `Enrichment:` section.

### 4. Update runbooks on friction

When the user confirms a runbook update:

1. Ask: "What was wrong, and what are the correct steps?"
2. Collect the corrected steps from the user.
3. Read `~/.dotfiles/claude-code-shared/resources/hitl-steps-runbooks.md`.
4. Check if a runbook for this type already exists (match by title keyword or category).
   - If exists: update the steps and enrichment in place.
   - If new: append a new runbook entry with Steps, Notes (if needed), and Enrichment sections.
5. Write the updated `hitl-steps-runbooks.md`.

Runbook format (see `~/.dotfiles/claude-code-shared/resources/hitl-steps-runbooks.md` for examples):

```markdown
## Short title for the action

Steps:
1. Exact command or dashboard path
2. Next step with concrete values

Enrichment:
- What to grep/check in codebase to fill placeholders
```

Use `{placeholder}` syntax for project-specific values. Include an `Enrichment:` section with lookup instructions.

### 5. End-of-walkthrough summary

After all follow-ups are processed, print a summary:

```
Follow-up walkthrough complete.

 #   Title                              Result
 ──   ──────────────────────────────     ──────
 1    Add STRIPE_KEY to Cloudflare       done
 2    Run database migration             done
 3    Visual test: login flow            skipped

Runbooks updated: 1 (Cloudflare env var steps corrected)
Wizard snippet: docs/wizard/20260512-1423-user-auth-flow.sh (ran)
```

If a wizard snippet was generated, list its path and whether the user ran it. If none was generated, omit the line.

### 6. Offer to push and open a PR

After the summary, ask the user: **"Push and open a PR?"**

If yes:

#### a. Generate a PR description

Gather context:
- Current branch: `git rev-parse --abbrev-ref HEAD`
- Commits on branch: `git log main...HEAD --oneline`
- Diff (truncated to first 300 lines): `git diff main...HEAD | head -n 300`
- Linear ticket: extract the first `[A-Za-z]+-[0-9]+` pattern from the branch name (uppercase). Optional. If found, check CLAUDE.md or `.claude/` config for a Linear workspace URL. If present, include `Linear Ticket: [TICKET](<workspace-url>/issue/TICKET)`. If no workspace URL, include ticket ID as plain text. If no ticket pattern, omit.

Write the PR description:

```
### <short descriptive title>
<Linear ticket link, if found>

<2-3 sentences: what was broken or missing, and what this PR does to fix it>

**Changes:**
<bullet list of key code changes, skip test files unless they are the point>
```

Rules:
- Under 250 words
- No Testing section, no other sections
- No em dashes. Use periods or commas only.
- Concise, no run-on sentences

#### b. Push and create the PR

1. Run `~/.dotfiles/.scripts/gxpush --pr` to stage, commit, push, and open the PR. gxpush shows a preview manifest and prompts for confirmation before any git operation runs.
2. Return the PR URL to the user (gxpush prints it after `gh pr create` completes).

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `run-task-followups`.
<!-- skill-done: run-task-followups -->
<!-- learning-capture:end -->
