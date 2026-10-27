# Agents and scripts are a deliberately flat shared pool

## Status

accepted

## Context

`agents/` and `scripts/` are registry-indexed shared buckets. Any Skill may spawn any
Agent (via `agents/registry.json`) and reference any script by absolute path
(`~/.dotfiles/claude-code-shared/scripts/<name>`). A dependency-graph analysis
(improve-directory-structure, litmus set CCP/CRP) will always flag nodes in these buckets
that currently have a single consumer, proposing they move "next to their owner" — e.g.
the `to-seed` personas, the `dispatch-tasks` task-exporters, `register-skill`'s scripts.

## Decision

Agents and scripts stay in their shared pool even when they have exactly one consumer
today. We optimize for a **single registry-indexed pool any skill can draw from**, not for
per-skill locality. Single-consumer-today does not justify relocation: consumers change,
and the uniform pool keeps agent/script discovery and the tiering registry
(`resources/model-tiers.json`) in one place.

## Consequences

- `scanner.py --extractor claude-tooling` and `dependency-graph-findings.py` will keep
  emitting CCP and CRP findings for single-consumer bucket nodes. These are **working as
  intended** and are not reorg candidates. Future improve-directory-structure runs should
  filter CCP/CRP-into-a-skill-directory the same way `pipeline_next` ADP cycles and
  shared-bucket MODULARITY findings are already filtered.
- Locality is still achievable **inside** the bucket: grouping related nodes under an
  owner-named subdirectory (`agents/seed-review/`, `agents/egress/`,
  `scripts/architecture-skill-audit/`, `scripts/agent-eval/`) is the established
  pattern and does not violate this decision. This ADR rejects moving nodes *out* of the
  pool into `skills/<name>/`, not grouping them *within* it.
