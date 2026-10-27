---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
model: sonnet
effort: medium
invokedBy: human
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to `docs/handoffs/` in the current workspace. Create the directory if it does not exist.

Name the file by running:

```bash
~/.dotfiles/claude-code-shared/scripts/scaffolding/doc-filename.sh <slug> md
```

Where `<slug>` is a short kebab-case description of the work (e.g. `auth-refactor`). The output is `YYYYMMDD-HHMM-<slug>.md`.

Include a machine-readable provenance line in the document header immediately after the title:

```
**Source ref:** `<seed-or-task-basename>`
```

Where `<seed-or-task-basename>` is the basename only (no directory prefix) of the seed or task file that triggered this handoff. Example: `20260606-0007-dispatch-tasks-pipeline.json`. This field enables clean-scaffolding to walk chains mechanically: a grep for `Source ref:` extracts the provenance pointer without LLM involvement.

Include a "suggested skills" section in the document, which suggests skills that the agent should invoke.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `handoff`.
<!-- skill-done: handoff -->
<!-- learning-capture:end -->
