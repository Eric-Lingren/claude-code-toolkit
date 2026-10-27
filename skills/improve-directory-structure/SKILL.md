---
name: improve-directory-structure
description: Build a dependency graph over a directory tree and surface reorganization opportunities (misplaced shared nodes, low-cohesion clusters, near-duplicate files) informed by domain vocabulary and ADR decisions. Grills each candidate against a curated litmus set, then hands off a verified reorg plan via to-seed. Never moves files or rewrites references directly. Use when the user wants to reorganize a directory, audit repo layout, find directory-structure smells, or asks "does this belong here".
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

# Improve Directory Structure

Surface **reorg opportunities**: nodes and clusters whose on-disk placement disagrees
with their actual dependency graph. The aim is a directory tree where "where a thing
lives" and "who actually uses it" agree — not a rewrite of the code inside any file.

This skill never touches disk beyond `CONTEXT.md`/ADR updates during the grilling loop
(see [grill-with-docs's inline-update discipline](../grill-with-docs/SKILL.md)). It stops
at a **verified plan**, handed off through the standard `to-seed -> to-tasks -> build-code`
pipeline. File moves and reference rewrites are always separate, discrete, AI-doable
tasks with `scanner --check` as their acceptance gate — never something this skill or its
conversation performs directly.

## Scope

This skill only analyzes directories with **structured, explicit cross-references** —
places where "what depends on what" can be mechanically derived from on-disk artifacts,
mirroring how `improve-codebase-architecture` scopes itself to module depth rather than
prose quality. Two shapes qualify, each backed by a registered `scanner.py` extractor:

- **A claude-tooling repo** (this repo, or one shaped like it: `skills/<name>/SKILL.md`
  directories, `agents/registry.json`, `resources/model-tiers.json`,
  `skill-pipeline.json`) — analyzed with `--extractor claude-tooling`. Edges come from
  registry consumer lists, `subagent_type=` spawns, absolute script-path references,
  tier-registry keys, and pipeline stage links.
- **A JS/TS or Python code directory** with resolvable relative imports — analyzed with
  `--extractor generic-code`. Edges come from `import`/`require`/`from ... import`
  statements that resolve to another on-disk file in the same tree.

**Decline the request** when the target is a sparse or unstructured prose tree — a
directory of markdown notes, design docs, or loose assets with no machine-resolvable
cross-references (no imports, no registry entries, no explicit path citations). Neither
extractor can build a meaningful edge set there; running the scanner against it would
produce a graph of near-zero edges and isolated nodes, which is diagnostic confirmation,
not a hollow finding to report. If unsure whether a target qualifies, run the scanner
first and check the edge count before committing to the analysis — a graph with edges
roughly proportional to node count is in scope; a graph that's almost all isolated nodes
is not, and you should say so plainly and stop rather than manufacture findings.

## Litmus set

Every finding is checked against exactly seven principles — SRP, CCP/LOCALITY, CRP, DRY,
ADP, SDP, MODULARITY — computed by `dependency-graph-findings.py`. See
[resources/LITMUS.md](resources/LITMUS.md) for what each one means, how to read a
finding's evidence, and two categories of graph noise (`pipeline_next` workflow loops,
and a deliberately-shared cluster's own low internal-edge ratio) that are not
directory-structure smells even though the raw scripts can emit them.

A candidate with no cited principle and no cited node/edge evidence is not a finding —
don't present it.

**SHAPE — a companion finding class.** The seven litmus principles are all derived from
the *edge graph*, so they are structurally blind to on-disk organizational smells that
live in naming/layout rather than references (inconsistent sibling wrapping, a fixture
loose among executables). Those are surfaced separately by `scanner.py --consistency`,
which emits `SHAPE` findings in the same envelope. SHAPE is not part of the litmus set and
is only available for the `claude-tooling` extractor. See
[resources/LITMUS.md](resources/LITMUS.md)'s "SHAPE findings" section for what it detects
and what it deliberately does not.

## Process

### 1. Scope check

Before doing anything else, apply the **Scope** section above to the user's target
directory. State explicitly which shape it matches (claude-tooling repo or generic code
tree) and which extractor you'll use. If it doesn't match either shape, decline plainly
and stop — do not run the scanner "just to see."

### 2. Load context and build dependency graph (parallel)

Fire both in the same turn — they are independent:

**context-loader** (`subagent_type: context-loader`, repo root) — captures vocabulary and ADR decisions.

**Scanner** (Bash) — run with the extractor chosen in step 1:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/scanner.py --extractor <claude-tooling|generic-code> [root] --out /tmp/graph-<slug>.json
```

Wait for both to complete, then capture the reference-integrity baseline:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/scanner.py --check [--extractor <name>] [root]
```

Use `vocabulary` terms from context-loader throughout your analysis and candidate write-ups — name clusters and proposed destinations the way the project's own `CONTEXT.md` names its nouns (e.g. this repo's own vocabulary: Skill, Agent, Contract, Hook, Resource, Registry, Pipeline stage, Tier), not generic architecture jargon. From `adrs[]`, deep-read the full text of any ADR relevant to the area you're analyzing via its `path` — a decision recorded there is not up for re-litigation. Do not glob `docs/adr/` directly.

### 3. Evaluate the dependency graph

If `--check` already fails on the untouched tree, note the pre-existing dangling
references/cycles as known debt in your candidate write-up (don't silently attribute them
to a reorg move that hasn't happened), but don't let pre-existing debt block presenting
new candidates.

### 4. Compute findings

Pipe the graph into the findings engine:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/dependency-graph-findings.py /tmp/graph-<slug>.json --out /tmp/findings-<slug>.json
```

Read [resources/LITMUS.md](resources/LITMUS.md)'s "Known non-findings" section before
trusting the raw output — filter `pipeline_next`-only ADP cycles and uniformly-fan-out
shared-bucket MODULARITY findings, the same way `scanner.py --check`'s own
`detect_cycles` filters `pipeline_next` edges.

For a `claude-tooling` repo, also collect the companion SHAPE findings — organizational
inconsistencies the edge graph can't see (see the "SHAPE — a companion finding class"
note above):

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/scanner.py --consistency [root] --out /tmp/shape-<slug>.json
```

This is advisory (always exits 0). Its `findings[]` share the same shape as the litmus
findings, so they merge into the same candidate list in step 5. Skip this for the
`generic-code` extractor, which defines no consistency rules.

### 5. Present candidates

Present a numbered list of reorg candidates. For each one:

- **Finding** — the finding `id` and `principle` (e.g. "F0003, CCP")
- **Evidence** — the cited node(s)/edge(s) verbatim from the finding, so the user can
  verify the claim against the graph themselves
- **Current placement -> proposed placement** — the concrete source path and destination
  directory, derived mechanically from the evidence per
  [resources/LITMUS.md](resources/LITMUS.md)'s "Reading a finding" section
- **Why** — the litmus principle's placement question, answered in one sentence using
  the project's own vocabulary

For a `SHAPE` finding the shape is the same, but "current -> proposed" reads as a
normalization rather than an owner-move: e.g. "pick one wrapping style for the group" or
"move the fixture into `scripts/tests/`". Its `detail` already states the fix; cite the
finding `id` + `SHAPE` and the node(s)/fact(s) verbatim like any other candidate.

Do not propose file contents or reference rewrites yet. Ask the user: "Which of these
would you like to pursue?"

### 6. Grilling loop

Once the user picks one or more candidates, drop into a grilling conversation per
candidate — same discipline as `improve-codebase-architecture`'s grilling loop:

- Walk the blast radius: every edge in the finding's evidence is a reference that will
  need to resolve to the new location. Are there other references to the same node not
  captured by the extractor's edge signals (e.g. prose mentions, unresolvable dynamic
  paths)? Surface these as risk, not as blockers — the downstream task can catch them via
  its `scanner --check` gate, but the user should know about them going in.
  A registry file might turn up in `scanner --check`'s dangling-reference categories itself
  if the extractor has a `check_integrity` function — cross-reference.
- Confirm the destination cluster's name matches the project's vocabulary. If it doesn't
  exist as a term in `CONTEXT.md` yet, add it there — same discipline as
  `grill-with-docs` (see
  [grill-with-docs/resources/CONTEXT-FORMAT.md](../grill-with-docs/resources/CONTEXT-FORMAT.md)).
  Create the file lazily if it doesn't exist.
- **User rejects a candidate with a load-bearing reason** (e.g. "that shared bucket is
  deliberately shared, splitting it would break X")? Offer an ADR, framed as: "Want me to
  record this as an ADR so future directory-structure reviews don't re-suggest it?" Only
  offer when the reason would matter to a future reviewer — skip ephemeral or
  self-evident reasons. See
  [grill-with-docs/resources/ADR-FORMAT.md](../grill-with-docs/resources/ADR-FORMAT.md).
- Resolve exactly what the destination path is, and what (if anything) at the destination
  already exists that the moved node would need to merge with or rename around.

Repeat until every chosen candidate has a concrete, argued-through destination and known
blast radius.

### 7. Hand off a verified plan — never execute moves

This skill's output is a plan, not a diff. When the grilling loop settles:

1. State plainly: "This is where the skill stops — no files have been moved, no
   references have been rewritten."
2. Recommend `/to-seed` to capture the resolved candidates as a seed. Each candidate's
   `decisions` entry must carry: the finding `id` + `principle` it was derived from, the
   source path, the destination path, and the blast-radius risk surfaced in the grilling
   loop.
3. Explicitly note, as part of what gets captured (so it survives into
   `implementation_decisions` or equivalent): **every reorg task that `to-tasks` later
   generates from this seed must cite
   `python3 ~/.dotfiles/claude-code-shared/scripts/dependency-graph/scanner.py --check` (with the same
   extractor/root used in step 3) as its acceptance gate.** This is the mechanical proof
   that a move didn't dangle a reference or introduce a cycle — it is not optional per
   task, and it is the reason this skill never performs moves itself: a move without that
   gate re-run afterward is unverified by construction.
4. Print the standard next-step suggestion — do NOT use `AskUserQuestion` or any blocking
   UI element:

   > "Next: `/to-seed` to capture these candidates as a JSON IR, then `/to-tasks` to break
   > the plan into discrete move-and-verify tasks."

**CRITICAL RULES:**
- Do NOT move any file, rewrite any reference, or touch anything beyond `CONTEXT.md`/ADR
  updates during the grilling loop, at any point in this skill — not even a single
  candidate the user seems eager about. That is `build-code`'s job, gated by
  `scanner --check`, on a task this skill only describes.
- Answering "yes" to a specific scoped grilling question does not authorize broader
  implementation. Specific questions have specific scope.
- Do not block. Print the suggestion and stop.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `improve-directory-structure`.
<!-- skill-done: improve-directory-structure -->
<!-- learning-capture:end -->
