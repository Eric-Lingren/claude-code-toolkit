# Litmus Set

The seven architecture principles this skill checks a directory tree against. This is
the exact set computed by `dependency-graph-findings.py` — see that script's module
docstring for the canonical definitions; this file restates them with the placement
question each one answers, so a finding can be translated into a concrete "move X to Y"
proposal.

Every finding this skill surfaces cites one of these principles plus the node(s)/edge(s)
that prove it. A candidate with no principle + no cited evidence is not a finding — don't
propose it.

| Principle | Violation shape | Placement question it answers |
|---|---|---|
| **SRP** | A node's fan-out spans several unrelated clusters | "Is this one file secretly doing several owners' jobs? Split it along cluster lines." |
| **CCP / LOCALITY** | A shared-bucket node's incoming edges all trace back to one owning cluster | "Why does this live in the shared bucket when only one owner ever calls it? Move it next to that owner." |
| **CRP** | A shared-bucket node has exactly one consumer | "This isn't shared at all — it has one caller. Co-locate or inline it." |
| **DRY** | Two nodes have near-identical content or edge signatures | "These are the same thing twice. Consolidate." |
| **ADP** | A cycle exists in the edge graph | "These nodes depend on each other in a loop. Break the cycle — invert or extract one edge." |
| **SDP** | A high-churn node has many stable dependents | "Stable nodes depend on unstable code. Stabilize the churny node or invert the dependency." |
| **MODULARITY** | A cluster's internal-edge ratio is low relative to its cross-cluster edges | "This directory bundles two concerns that rarely talk to each other internally. Split it." |

## Reading a finding

Each finding from `dependency-graph-findings.py` is shaped:

```json
{
  "id": "F0003",
  "principle": "CCP",
  "title": "agent:build-runner is shared but every consumer belongs to cluster 'build-code'",
  "nodes": ["agent:build-runner", "skill:build-code", "skill:build-code"],
  "evidence": [{"edge": {"from": "skill:build-code", "to": "agent:build-runner", "kind": "registry_consumer"}}, ...],
  "detail": "..."
}
```

Translate mechanically: `nodes[0]`'s current `path` (from the graph, not the finding) is
the move's source; the owning cluster named in `detail` is the move's destination
directory. Never propose a destination the evidence doesn't support — if the `detail`
says "every consumer belongs to cluster 'build-code'", the destination is next to
`build-code`, not somewhere else that seemed tidy.

## Known non-findings

Two categories of graph noise are not directory-structure smells; do not surface them as
candidates even if the raw scripts emit them:

- **`pipeline_next` cycles (ADP).** `skill-pipeline.json`'s `next[]` links are a
  workflow/suggestion DAG, not a dependency graph — some loop back by design (e.g.
  `dispatch-tasks -> to-e2e-tasks -> dispatch-tasks`). `scanner.py --check` already
  filters these before running cycle detection (see its `detect_cycles` docstring). If
  you run `dependency-graph-findings.py` directly on a graph rather than through
  `--check`, filter `pipeline_next` edges out before trusting an ADP finding, or you will
  flag an intentional workflow loop as a structural defect.
- **A deliberately shared cluster's own MODULARITY score.** A cluster that exists
  specifically to be a shared bucket (e.g. an `agents/` or `scripts/` directory serving
  many unrelated owners) will always show a low internal-edge ratio — that is the
  bucket's job, not a defect. Only surface a MODULARITY finding when the cross-cluster
  edges cluster around a *subset* of the bucket that could split off with its own
  owner, not when the whole bucket is uniformly fan-out by design.

## SHAPE findings

The seven litmus principles above are all computed from the edge graph. They cannot see
organizational smells that live in on-disk *naming and layout* rather than in references.
`scanner.py --consistency` (claude-tooling extractor only) surfaces those separately as a
companion `SHAPE` finding class, emitted in the same findings envelope so they slot into
the same candidate list. It is advisory: it always exits 0 and never gates a task the way
`--check` does.

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/scanner.py --consistency [root]
```

Current SHAPE rules:

- **SHAPE-1 — mixed sibling wrapping.** A logical agent group mixes wrapping styles: some
  members are a bare `<name>.md`, others are a `<name>/agent.md` subdirectory. Evidence
  names each member and its shape. The fix is to pick one shape for the whole group. A
  `SHAPE` finding cites `nodes` (the agent ids) and `fact` evidence, so it clears the
  "no principle + no evidence" bar the same way a litmus finding does.
- **SHAPE-2 — stray fixture.** A `*.example.*` fixture/example file sits directly among
  executables under `scripts/` instead of in `scripts/tests/` or a `fixtures/` directory.

**What SHAPE deliberately does NOT flag.** A flat, heterogeneous *shared bucket* is a
design choice, not a defect — the same reasoning as the shared-bucket MODULARITY
non-finding above, and recorded for this repo in
`docs/adr/0001-agents-and-scripts-are-a-flat-shared-pool.md`. `resources/` mixing `.md`
docs and `.json` config is intentional, so "mixed file types in a bucket" is not a rule.
Only genuine *within-group* inconsistency is. When you add a SHAPE rule, hold it to the
same near-zero-false-positive bar: it must not fire on a deliberately flat pool.
