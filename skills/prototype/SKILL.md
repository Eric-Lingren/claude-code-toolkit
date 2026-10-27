---
name: prototype
description: Explore ideas, hypotheses, and design options before committing to a PRD. Runs throwaway code (terminal or UI) on a local-only git branch, then collapses findings into docs/prototypes/<filename>.md. Use when the user wants to prototype, sanity-check a data model or state machine, mock up a UI, explore design options, or says "prototype this", "let me play with it", "try a few designs".
model: sonnet
effort: high
invokedBy: human
---

# Prototype

A prototype is **throwaway code that answers a question**.

## Contract

**Format (conditional output):** seed file — see `contracts/seed-contract.md` (schema_version: `"2"`)
**Role:** conditional producer (produces a seed file only when user chooses option 1 in the end-of-run save prompt)

**Step-0 fires only when a seed file is chosen:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/seed-schema.json \
  <output-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not write the file. It sits between `/grill-me` and `/to-spec` in the pipeline. The only durable output is a findings doc. The code is scaffolding and gets deleted.

## Setup

1. **Derive a slug** from the feature or question being explored. Lowercase, kebab-case, max ~40 chars (e.g. `cart-state-model`).
2. **Determine the output filename** by running `~/.dotfiles/claude-code-shared/scripts/scaffolding/doc-filename.sh <slug> md`. This produces `YYYYMMDD-HHMM-<slug>.md`.
3. **Create a local git branch**: `prototype/proto-<slug>`. Never push this git branch. Never PR it. It is local scaffolding only. See `branching-strategy.md` for the local-only prefix contract.
4. **Pick a mode** (see below).

## Pick a mode

The two modes produce very different artifacts — getting this wrong wastes the whole prototype. Identify which question is being answered:

- **Logic mode** — "Does this logic / state model feel right?" → [LOGIC.md](LOGIC.md). Build a tiny interactive terminal app that pushes the state machine through cases that are hard to reason about on paper.
- **UI mode** — "What should this look like?" → [UI.md](UI.md). Generate several radically different UI variations on a single route, switchable via a URL search param and a floating bottom bar.

If the question is genuinely ambiguous and the user is not reachable, default to whichever mode better matches the surrounding code (backend module → logic mode; page or component → UI mode) and state the assumption at the top of the session.

## Rules that apply to both modes

1. **Throwaway from day one.** The git branch is deleted at the end. No production code is implemented here. Name things so a casual reader can see it is a prototype, not production.
2. **One command to run.** Whatever the project's existing task runner supports — `pnpm <name>`, `python <path>`, `yarn <name>`, etc. The user must be able to start it without thinking.
3. **No persistence by default.** State lives in memory. If the question explicitly involves a database, hit a scratch DB or a local file with a clear "PROTOTYPE — wipe me" name.
4. **Skip the polish.** No tests, no error handling beyond what makes the prototype runnable, no abstractions. The point is to learn something fast and then delete it.
5. **Surface the state.** After every action (logic mode) or on every variant switch (UI mode), print or render the full relevant state so the user can see what changed.
6. **No code leaves the git branch.** Code is never committed outside `prototype/proto-<slug>`. The branch is deleted when the session ends.

## When done

Once the prototype has answered its question and a final direction is agreed upon:

1. **Write `docs/prototypes/<filename>`** on the prototype git branch. Use `<filename>` from Setup step 2 and the template from `~/.dotfiles/claude-code-shared/resources/prototype-template.md`.

2. **Ask the user to review the artifact.** Show the path and prompt:

   ```
   Findings written to docs/prototypes/<filename>.
   Does this look correct? Anything to add or remove before we finalize?
   ```

   Incorporate any changes the user requests. Repeat until confirmed.

3. **Commit all prototype code** (excluding the artifact) to the prototype branch. Untracked files follow `git checkout` and will bleed onto the target branch if not committed first:
   ```bash
   git add -A -- ':!docs/prototypes/<filename>'
   git commit -m "chore: prototype scaffolding (branch will be deleted)"
   ```

4. **Commit the artifact** separately on the prototype branch:
   ```bash
   git add docs/prototypes/<filename>
   git commit -m "docs: add prototype findings for <slug>"
   ```

5. **Create a clean PR branch** off the original branch and cherry-pick only the artifact commit:
   ```bash
   git checkout <original-branch>
   git checkout -b prototype/<slug>
   git cherry-pick <artifact-commit-sha>
   ~/.dotfiles/.scripts/gxpush --push-only
   ```

6. **Open a PR** targeting the original branch. Auto-generate the PR body from the artifact:
   - Title: `docs: prototype findings for <slug>`
   - Body: pull `## Question`, `## Verdict`, and `## PRD Handoff Notes` sections from the artifact verbatim.

   ```bash
   gh pr create --base <original-branch> --title "docs: prototype findings for <slug>" --body "..."
   ```

7. **Confirm with the user before deleting the prototype branch.** Show:

   ```
   PR open: <pr-url>
   Artifact: docs/prototypes/<filename>
   PR branch: prototype/<slug>

   Ready to delete the throwaway branch prototype/proto-<slug>?
   ```

   Wait for confirmation before proceeding.

8. **Delete the prototype git branch.**

   ```bash
   git branch -D prototype/proto-<slug>
   ```

9. **Prompt: save output as seed, handoff, or neither.**

   ```
   How should I save this prototype's findings?
   1. Seed file (docs/seeds/) — feeds directly into /to-spec and /to-tasks
   2. Handoff file (docs/prototypes/) — human-readable only, no downstream skills
   3. Neither — findings are in the PR description only
   ```

   - **Option 1 (seed):** Write a seed file to `docs/seeds/` following `contracts/seed-contract.md`. Run Step-0 validation before writing. On validation failure: STOP and report stderr.
   - **Option 2 (handoff):** Write to `docs/prototypes/` using `prototype-template.md`. No schema validation.
   - **Option 3 (neither):** Skip artifact writing entirely.

10. **Hand off to the PRD phase.** Print:

    ```
    Prototype complete.

    Artifact: <absolute-path-to-artifact> (or "none" if option 3)
    PR:       <pr-url>

    Run /to-spec or /to-tasks to continue.
    ```

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `prototype`.
<!-- skill-done: prototype -->
  - `/to-seed` — prototype confirmed the approach and ready to capture decisions
<!-- learning-capture:end -->
