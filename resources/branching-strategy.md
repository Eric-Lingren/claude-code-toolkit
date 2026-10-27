---
name: branching-strategy
description: Canonical branching rules for all task-generating skills. Defines strategies, how to present the choice, branch name derivation, and JSON recording.
---

# Branching Strategy

All task-generating skills reference this document for branching decisions.

## Strategies

- **`single`**: one shared branch for all tasks. User provides the name. All tasks run on this branch.
- **`per-task`**: each task gets its own branch, auto-derived from task ID and title.

## Local-only prefixes

Some branches are **never pushed and never PRed**. They are scaffolding, not product.

- **`prototype/proto-<slug>`**: created by the `/prototype` skill for throwaway exploration code. Always local. Deleted after the session ends. Only `docs/prototypes/<slug>.md` survives and gets committed back to the originating branch. No other skill should push or PR a `prototype/*` branch.

## Presenting the choice

Ask the user:

```
Branching strategy:
1. Single branch for all tasks (you provide the name) — best for a focused feature or fix
2. Per-task branches (auto-generated) — best for independent tasks reviewed separately

Which do you prefer?
```

If single: ask "Branch name?"

## Branch name derivation (per-task)

Format: `{prefix}/t-{id-number}[-gh-{issue}]-{kebab-title}`

- Lowercase, max ~50 chars total
- `{prefix}`: from current branch (`feat/`, `fix/`, or `spike/`). If no recognized prefix (e.g. `main`), ask: "Is this a feat or fix?"
- `{id-number}`: numeric portion of the task ID (e.g. `0023` from `T-0023`)
- `gh-{issue}` (**optional**): present only when the task's `export_url` is a GitHub issue URL. The issue number is the last path segment of that URL (e.g. `export_url: "https://github.com/org/repo/issues/42"` → `gh-42`). Placed immediately after the task-ID segment.
- `{kebab-title}`: first 3-4 words of title, kebab-cased

Examples:
- Without GitHub issue: `T-0023 "Bootstrap auth schema"` → `feat/t-0023-bootstrap-auth-schema`
- With GitHub issue #42: `T-0023 "Bootstrap auth schema"` + `export_url: ".../issues/42"` → `feat/t-0023-gh-42-bootstrap-auth-schema`

## JSON recording

Single strategy:

```json
"branching": {
  "strategy": "single",
  "branch": "feat/my-feature"
}
```

Per-task strategy (no top-level `branch`; each task's `branch` field holds its own):

```json
"branching": {
  "strategy": "per-task"
}
```
