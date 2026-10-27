# An Agent gets its own subdirectory iff it has companion files

## Status

superseded by [ADR-0006](0006-scripts-live-in-scripts-not-agents.md)

## Context

Agents in the flat shared pool (`agents/`, see
[ADR-0001](0001-agents-and-scripts-are-a-flat-shared-pool.md)) appear on disk in two
shapes: a bare `agents/<name>.md`, or a wrapped `agents/<name>/agent.md` subdirectory. A
consistency scan (`scanner.py --consistency`, SHAPE-1) flags any *group* that mixes the
two shapes as an inconsistency, and will always flag `agents/egress/` (formerly `task-exporters`), where
`export-tasks.md` is bare while `export-tasks-github/` and `export-tasks-notion/` are wrapped.

The mixing is not sloppiness. The two wrapped agents each carry companion scripts
(`export-tasks-github/github-issue.sh`; `export-tasks-notion/notion-check-token.sh`,
`notion-page-api.sh`; since relocated to `scripts/egress/`) that need a home next to their owner. `export-tasks` has no
companion files, so it stays a bare `.md`. Across the whole pool this holds without
exception: the only two agents with a personal subdirectory are exactly the two with
companion scripts; every other agent (the top-level pool and all `personas/`) is bare and
owns no companion files.

## Decision

An Agent is wrapped in its own `agents/<...>/<name>/agent.md` subdirectory **iff** it owns
companion files (adapter scripts, fixtures) that belong beside it. An Agent with no
companion files stays a bare `agents/<...>/<name>.md`. We do **not** normalize to
uniform wrapping for its own sake: giving `export-tasks` an `export-tasks/agent.md`
subdirectory with nothing beside it trades a real "this file has neighbors" signal for
cosmetic uniformity, and manufactures empty directories.

## Consequences

- `scanner.py --consistency` (SHAPE-1) will keep emitting a mixed-sibling-wrapping finding
  for `agents/egress/` and any future group where a companion-carrying agent sits
  beside a bare one. These are **working as intended** and are not reorg candidates.
  Future improve-directory-structure runs should filter SHAPE-1 findings whose bare
  members own no companion files, the same way ADR-0001 filters single-consumer
  CCP/CRP pool moves.
- A cleaner long-term fix is to teach the SHAPE-1 rule this exception directly (skip a
  group as consistent when its bare members have no companion files and its wrapped
  members do). That is a `scanner.py` code change, tracked separately from this ADR.
- The wrapping shape stays a reliable at-a-glance signal: a `<name>/` subdirectory means
  "this agent has neighbors worth opening," a bare `<name>.md` means "this agent is
  self-contained."
