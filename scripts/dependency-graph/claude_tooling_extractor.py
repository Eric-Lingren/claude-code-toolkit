#!/usr/bin/env python3
"""claude_tooling_extractor.py -- repo-type edge extractor for claude-code-shared.

This is a *scanner extractor* (see scanner.py), not the analysis core. It walks
the claude-code-shared tooling repo and emits a {nodes, edges, metrics} JSON
document shaped exactly per the input contract documented at the top of
dependency-graph-findings.py. It does not compute any findings itself -- that
is dependency-graph-findings.py's job.

## What becomes a node

- **skill** nodes: one per `skills/<name>/SKILL.md` directory found on disk.
  cluster = the skill's own directory name (a skill is its own owner).
- **agent** nodes: one per entry in `agents/registry.json`. cluster = "agents",
  is_shared = true (agents live in a top-level shared bucket, not co-located
  with any single consumer skill).
- **script** nodes: one per file under `scripts/` (recursively, excluding
  `__pycache__` and dot-directories). cluster = "scripts", is_shared = true.
- **resource** nodes: the small set of structured config files that other
  nodes reference by key or by path (`agents/registry.json`,
  `resources/model-tiers.json`, `skill-pipeline.json`, `resources/repo-policy.json`,
  `resources/task-routing.json`), when present. cluster = "resources",
  is_shared = true.

## What becomes an edge

Exactly the five edge-producing signals named in the task:

1. **registry.json entries** -- for every agent's `consumers` list, an edge
   `skill:<consumer> -> agent:<name>` (kind `registry_consumer`), when the
   consumer skill actually exists on disk.
2. **`subagent_type=` spawns** -- grepping every skill/agent markdown file for
   `subagent_type: <name>` / `subagent_type="<name>"` produces an edge from
   the owning skill/agent to the spawned agent (kind `spawns`).
3. **Absolute script-path references** -- grepping the same files for
   `claude-code-shared/scripts/<path>` references produces an edge from the
   owning skill/agent to the referenced script node (kind `script_ref`).
4. **`model-tiers.json` keys** -- every skill/agent name present in
   `resources/model-tiers.json`'s `skills`/`agents` maps produces an edge to
   the model-tiers resource node (kind `tiered_by`).
5. **`skill-pipeline.json` stage links** -- every `skills[<slug>].next[].skill`
   entry produces an edge `skill:<slug> -> skill:<target>` (kind `pipeline_next`).

Edges referencing a node that doesn't resolve to an actual on-disk artifact
(e.g. a stale registry consumer name) are silently skipped -- integrity
checking is scanner --check's job (a later task), not this extractor's.

## Usage

  python3 claude_tooling_extractor.py [base_dir]
  python3 claude_tooling_extractor.py --out graph.json

Default base_dir: parent of this script (claude-code-shared/).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

SUBAGENT_TYPE_RE = re.compile(r'subagent_type\s*[:=]\s*"?([A-Za-z][A-Za-z0-9_-]*)"?')
SCRIPT_PATH_RE = re.compile(r'claude-code-shared/scripts/([A-Za-z0-9_.\-/]+)')
TRAILING_PUNCT = '`)("\',:;.'

# --check's literal-absolute-path-reference scan. Matches either of two
# equivalent ways a doc/script can point back into this repo:
#   claude-code-shared/<rest>        -- via ~/.dotfiles/claude-code-shared/<rest>
#                                        or the fully-resolved /Users/<u>/.dotfiles/...
#                                        form (the substring still appears either way)
#   .cch|.cco/(skills|agents|settings.json)<rest>
#                                     -- via the ~/.cch or ~/.cco symlink farm
# Per CONTEXT.md invariant 4, `.cch/` and `.cco/` are symlink farms pointing
# ONLY at this repo's skills/, agents/, and settings.json (`.cch/skills` ->
# `skills/`, `.cch/agents` -> `agents/`, `.cch/settings.json` ->
# `settings.json`) -- NOT a general alias for the whole repo. `~/.cch` and
# `~/.cco` are Claude Code's own per-profile home directories and hold plenty
# of unrelated content (session transcripts under `.cch/projects/...`, etc.)
# that has nothing to do with claude-code-shared; only these three specific
# subpaths are folded back to the same node this repo already tracks.
ALT_ROOT_RE = re.compile(
    r'claude-code-shared/([A-Za-z0-9_.\-/]+)'
    r'|\.cc[ho]/(skills/[A-Za-z0-9_.\-/]+|agents/[A-Za-z0-9_.\-/]+|settings\.json)'
)

RESOURCE_FILES = [
    "agents/registry.json",
    "resources/model-tiers.json",
    "skill-pipeline.json",
    "resources/repo-policy.json",
    "resources/task-routing.json",
]


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_skills(base_dir):
    """Return {skill_name: skill_dir_relpath} for every skills/<name>/SKILL.md."""
    skills_dir = os.path.join(base_dir, "skills")
    out = {}
    if not os.path.isdir(skills_dir):
        return out
    for name in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, name)
        if os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            out[name] = os.path.relpath(skill_dir, base_dir)
    return out


def load_registry(base_dir):
    """Return the list of agent entries from agents/registry.json (or [])."""
    path = os.path.join(base_dir, "agents", "registry.json")
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("agents", [])


def discover_scripts(base_dir):
    """Return {relpath: relpath} for every file under scripts/."""
    scripts_dir = os.path.join(base_dir, "scripts")
    out = {}
    if not os.path.isdir(scripts_dir):
        return out
    for root, dirs, files in os.walk(scripts_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, base_dir)
            out[rel] = rel
    return out


def discover_markdown_files(base_dir):
    """Return a list of repo-relative paths to every skill/agent markdown file."""
    out = []
    for top in ("skills", "agents"):
        top_dir = os.path.join(base_dir, top)
        if not os.path.isdir(top_dir):
            continue
        for root, dirs, files in os.walk(top_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for fname in files:
                if fname.endswith(".md"):
                    out.append(os.path.relpath(os.path.join(root, fname), base_dir))
    return sorted(out)


# Top-level dirs/files scanned for literal absolute-path references. Scoped
# to authored, structural content -- deliberately excludes learnings/ and
# usage-reports/, which hold runtime-generated data (session transcript
# paths, historical schema tags, log lines) that isn't a cross-reference
# anyone maintains by hand and would otherwise flood --check with noise.
CHECK_SCAN_DIRS = ("skills", "agents", "scripts", "resources", "contracts", "hooks")
CHECK_SCAN_ROOT_FILES = ("CONTEXT.md", "settings.json", "skill-pipeline.json")


def discover_check_scan_files(base_dir):
    """Return repo-relative paths to every authored/structural file eligible
    for --check's literal absolute-path reference scan (see CHECK_SCAN_DIRS).
    Broader than discover_markdown_files (which is scoped to skill/agent-owned
    markdown specifically) but intentionally narrower than 'everything under
    base_dir', to keep the scan to content someone actually maintains by hand."""
    out = []
    for top in CHECK_SCAN_DIRS:
        top_dir = os.path.join(base_dir, top)
        if not os.path.isdir(top_dir):
            continue
        for root, dirs, files in os.walk(top_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for fname in files:
                out.append(os.path.relpath(os.path.join(root, fname), base_dir))
    for fname in CHECK_SCAN_ROOT_FILES:
        if os.path.isfile(os.path.join(base_dir, fname)):
            out.append(fname)
    return sorted(out)


# --------------------------------------------------------------------------
# Node-id resolution
# --------------------------------------------------------------------------

def owning_node_id(relpath, skills, agent_file_to_id):
    """Map a repo-relative markdown file path back to the skill/agent node id
    that owns it, or None if it isn't owned by any tracked node."""
    parts = relpath.split(os.sep)
    if parts[0] == "skills" and len(parts) > 1 and parts[1] in skills:
        return f"skill:{parts[1]}"
    if relpath in agent_file_to_id:
        return agent_file_to_id[relpath]
    return None


def resolve_script_path(candidate, script_ids):
    """Try to resolve a grep-captured script path candidate against the known
    script node ids, stripping trailing punctuation the regex over-captured."""
    rel = f"scripts/{candidate}"
    for trim in range(0, 4):
        probe = rel if trim == 0 else rel[:-trim]
        if probe.rstrip(TRAILING_PUNCT) != probe:
            probe = probe.rstrip(TRAILING_PUNCT)
        if probe in script_ids:
            return probe
    stripped = rel.rstrip(TRAILING_PUNCT)
    if stripped in script_ids:
        return stripped
    return None


# --------------------------------------------------------------------------
# Churn (best-effort, optional)
# --------------------------------------------------------------------------

def compute_churn(base_dir):
    """Best-effort commit-count-per-file churn signal via one `git log` call.
    Returns {relpath: commit_count}, or {} if git is unavailable/fails."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=", "--name-only"],
            cwd=base_dir, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return {}
        counts = Counter(line.strip() for line in proc.stdout.splitlines() if line.strip())
        return dict(counts)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------

def build_graph(base_dir):
    base_dir = os.path.abspath(base_dir)
    skills = discover_skills(base_dir)              # name -> relpath
    registry_agents = load_registry(base_dir)
    scripts = discover_scripts(base_dir)             # relpath -> relpath
    md_files = discover_markdown_files(base_dir)

    nodes = {}
    edges = []

    def add_node(node_id, path, ntype, cluster, is_shared=False):
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id, "path": path, "type": ntype,
                "cluster": cluster, "is_shared": is_shared,
            }

    # -- skill nodes --
    for name, relpath in skills.items():
        add_node(f"skill:{name}", os.path.join(relpath, "SKILL.md"), "skill", name, is_shared=False)

    # -- agent nodes --
    agent_names = set()
    agent_file_to_id = {}
    for entry in registry_agents:
        name = entry.get("name")
        file_ = entry.get("file")
        if not name or not file_:
            continue
        node_id = f"agent:{name}"
        agent_names.add(name)
        agent_file_to_id[file_] = node_id
        add_node(node_id, file_, "agent", "agents", is_shared=True)

    # -- script nodes --
    script_ids = set()
    for relpath in scripts.values():
        node_id = f"script:{relpath}"
        script_ids.add(relpath)
        add_node(node_id, relpath, "script", "scripts", is_shared=True)

    # -- resource nodes --
    for relpath in RESOURCE_FILES:
        if os.path.isfile(os.path.join(base_dir, relpath)):
            add_node(f"resource:{relpath}", relpath, "resource", "resources", is_shared=True)

    def add_edge(src, dst, kind):
        if src in nodes and dst in nodes and src != dst:
            edges.append({"from": src, "to": dst, "kind": kind})

    # -- 1. registry.json entries -> registry_consumer edges --
    for entry in registry_agents:
        name = entry.get("name")
        if not name:
            continue
        agent_id = f"agent:{name}"
        for consumer in entry.get("consumers", []):
            if consumer in skills:
                add_edge(f"skill:{consumer}", agent_id, "registry_consumer")

    # -- 2 & 3. subagent_type= spawns and absolute script-path references --
    for relpath in md_files:
        owner = owning_node_id(relpath, skills, agent_file_to_id)
        if owner is None:
            continue
        full_path = os.path.join(base_dir, relpath)
        try:
            with open(full_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue

        for m in SUBAGENT_TYPE_RE.finditer(text):
            target_name = m.group(1)
            if target_name in agent_names:
                add_edge(owner, f"agent:{target_name}", "spawns")

        for m in SCRIPT_PATH_RE.finditer(text):
            script_id = resolve_script_path(m.group(1), script_ids)
            if script_id is not None:
                add_edge(owner, f"script:{script_id}", "script_ref")

    # -- 4. model-tiers.json keys -> tiered_by edges --
    tiers_path = os.path.join(base_dir, "resources", "model-tiers.json")
    if os.path.isfile(tiers_path):
        with open(tiers_path) as f:
            tiers = json.load(f)
        tiers_resource_id = "resource:resources/model-tiers.json"
        if tiers_resource_id in nodes:
            for skill_name in tiers.get("skills", {}):
                if skill_name in skills:
                    add_edge(f"skill:{skill_name}", tiers_resource_id, "tiered_by")
            for agent_name in tiers.get("agents", {}):
                if agent_name in agent_names:
                    add_edge(f"agent:{agent_name}", tiers_resource_id, "tiered_by")

    # -- 5. skill-pipeline.json stage links -> pipeline_next edges --
    pipeline_path = os.path.join(base_dir, "skill-pipeline.json")
    if os.path.isfile(pipeline_path):
        with open(pipeline_path) as f:
            pipeline = json.load(f)
        for slug, spec in pipeline.get("skills", {}).items():
            if slug not in skills:
                continue
            for nxt in spec.get("next", []):
                target = nxt.get("skill")
                if target in skills:
                    add_edge(f"skill:{slug}", f"skill:{target}", "pipeline_next")

    # -- metrics --
    fan_out = defaultdict(int)
    fan_in = defaultdict(int)
    for e in edges:
        fan_out[e["from"]] += 1
        fan_in[e["to"]] += 1

    churn = compute_churn(base_dir)

    metrics = {}
    for node_id, node in nodes.items():
        m = {
            "fan_in": fan_in.get(node_id, 0),
            "fan_out": fan_out.get(node_id, 0),
            "dir_depth": node["path"].count("/") + 1,
            "orphan": fan_in.get(node_id, 0) == 0 and fan_out.get(node_id, 0) == 0,
        }
        c = churn.get(node["path"])
        if c is not None:
            m["churn"] = c
        metrics[node_id] = m

    ordered_nodes = [nodes[k] for k in sorted(nodes.keys())]
    return {"nodes": ordered_nodes, "edges": edges, "metrics": metrics}


# --------------------------------------------------------------------------
# --check -- reference-integrity pass
# --------------------------------------------------------------------------

def check_integrity(base_dir):
    """Reuse the exact same signals as build_graph's edge-resolution pass,
    but instead of silently skipping a reference that doesn't resolve to an
    on-disk artifact (build_graph's documented behavior), record it as a
    dangling-reference issue. This is scanner --check's job, called out
    explicitly in build_graph's own docstring as deferred to a later task.

    Returns a de-duplicated list of issue dicts:
      {"kind": ..., "source": ..., "target": ..., "detail": ...}

    Covers:
      1. registry.json 'consumers' entries naming a skill that doesn't exist
         on disk (mirrors build_graph signal 1).
      2. registry.json 'file' fields pointing at a file that doesn't exist on
         disk (build_graph adds these nodes unconditionally today; --check is
         the first place this gets validated).
      3. `claude-code-shared/scripts/<path>` references inside skill/agent
         markdown that don't resolve to a known script (mirrors build_graph
         signal 3 / resolve_script_path).
      4. `resources/model-tiers.json` skill/agent keys that don't exist on
         disk (mirrors build_graph signal 4).
      5. `skill-pipeline.json` stage slugs and next-targets that don't exist
         on disk (mirrors build_graph signal 5).
      6. Any literal absolute-path string reference, in authored/structural
         content only (see CHECK_SCAN_DIRS), of the form
         `claude-code-shared/<path>` or `.cch|.cco/(skills|agents|settings.json)<path>`,
         that doesn't resolve to an actual on-disk path. The `.cch`/`.cco`
         forms are followed as the symlink-farm aliases they are (CONTEXT.md
         invariant 4) rather than as an ordinary (and therefore
         always-broken) literal path into a directory this repo doesn't
         contain.
    """
    base_dir = os.path.abspath(base_dir)
    skills = discover_skills(base_dir)
    registry_agents = load_registry(base_dir)
    scripts = discover_scripts(base_dir)
    script_ids = set(scripts.values())
    md_files = discover_markdown_files(base_dir)
    agent_names = {e["name"] for e in registry_agents if e.get("name")}
    agent_file_to_id = {
        e["file"]: f"agent:{e['name']}"
        for e in registry_agents if e.get("name") and e.get("file")
    }

    issues = []

    # -- 1 & 2. registry.json entries --
    for entry in registry_agents:
        name = entry.get("name")
        file_ = entry.get("file")
        if name and file_ and not os.path.isfile(os.path.join(base_dir, file_)):
            issues.append({
                "kind": "dangling_registry_agent_file",
                "source": "agents/registry.json",
                "target": file_,
                "detail": f"agent '{name}' declares file '{file_}' which does not exist on disk",
            })
        for consumer in entry.get("consumers", []):
            # A consumer name is valid whether it names a skill (the common
            # case) or another registered agent (agent-to-agent spawns, e.g.
            # export-tasks-github's consumer 'export-tasks' names the export-tasks
            # coordinator agent, not a skill). Only flag it dangling if it
            # resolves to neither.
            if consumer not in skills and consumer not in agent_names:
                issues.append({
                    "kind": "dangling_registry_consumer",
                    "source": "agents/registry.json",
                    "target": consumer,
                    "detail": (
                        f"agent '{name}' lists consumer '{consumer}' which does not "
                        f"exist as a skill under skills/ or as a registered agent"
                    ),
                })

    # -- 3. script-path references in skill/agent markdown --
    for relpath in md_files:
        owner = owning_node_id(relpath, skills, agent_file_to_id)
        if owner is None:
            continue
        full_path = os.path.join(base_dir, relpath)
        try:
            with open(full_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        for m in SCRIPT_PATH_RE.finditer(text):
            script_id = resolve_script_path(m.group(1), script_ids)
            if script_id is None:
                issues.append({
                    "kind": "dangling_script_ref",
                    "source": relpath,
                    "target": f"scripts/{m.group(1)}",
                    "detail": (
                        f"{relpath} references 'claude-code-shared/scripts/{m.group(1)}' "
                        f"which does not resolve to a script on disk"
                    ),
                })

    # -- 4. model-tiers.json keys --
    tiers_path = os.path.join(base_dir, "resources", "model-tiers.json")
    if os.path.isfile(tiers_path):
        with open(tiers_path) as f:
            tiers = json.load(f)
        for skill_name in tiers.get("skills", {}):
            if skill_name not in skills:
                issues.append({
                    "kind": "dangling_tier_key",
                    "source": "resources/model-tiers.json",
                    "target": skill_name,
                    "detail": (
                        f"model-tiers.json lists skill '{skill_name}' which does not "
                        f"exist under skills/"
                    ),
                })
        for agent_name in tiers.get("agents", {}):
            if agent_name not in agent_names:
                issues.append({
                    "kind": "dangling_tier_key",
                    "source": "resources/model-tiers.json",
                    "target": agent_name,
                    "detail": (
                        f"model-tiers.json lists agent '{agent_name}' which is not "
                        f"registered in agents/registry.json"
                    ),
                })

    # -- 5. skill-pipeline.json stage links --
    pipeline_path = os.path.join(base_dir, "skill-pipeline.json")
    if os.path.isfile(pipeline_path):
        with open(pipeline_path) as f:
            pipeline = json.load(f)
        for slug, spec in pipeline.get("skills", {}).items():
            if slug not in skills:
                issues.append({
                    "kind": "dangling_pipeline_source",
                    "source": "skill-pipeline.json",
                    "target": slug,
                    "detail": f"skill-pipeline.json defines stage '{slug}' which does not exist under skills/",
                })
                continue
            for nxt in spec.get("next", []):
                target = nxt.get("skill")
                if target and target not in skills:
                    issues.append({
                        "kind": "dangling_pipeline_target",
                        "source": "skill-pipeline.json",
                        "target": target,
                        "detail": (
                            f"skill-pipeline.json stage '{slug}' points at next-skill "
                            f"'{target}' which does not exist under skills/"
                        ),
                    })

    # -- 6. literal absolute-path references in authored/structural content --
    for relpath in discover_check_scan_files(base_dir):
        full_path = os.path.join(base_dir, relpath)
        try:
            with open(full_path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        for m in ALT_ROOT_RE.finditer(text):
            candidate = (m.group(1) or m.group(2) or "").rstrip(TRAILING_PUNCT)
            if not candidate or candidate.endswith(".jsonl"):
                # .jsonl targets are virtually always runtime-appended log
                # data (audit logs, learning captures), not an authored
                # cross-reference to something expected to pre-exist.
                continue
            if not os.path.exists(os.path.join(base_dir, candidate)):
                issues.append({
                    "kind": "dangling_absolute_path_ref",
                    "source": relpath,
                    "target": candidate,
                    "detail": (
                        f"{relpath} references '{m.group(0)}' (resolves to "
                        f"'{candidate}' relative to claude-code-shared/) which does "
                        f"not exist on disk"
                    ),
                })

    # De-duplicate: the same broken reference commonly repeats within/across
    # files (e.g. a stale script path quoted in several skills).
    seen = set()
    deduped = []
    for issue in issues:
        key = (issue["kind"], issue["source"], issue["target"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


# --------------------------------------------------------------------------
# --consistency -- filesystem-shape / organizational-consistency pass
# --------------------------------------------------------------------------

# Basename that marks an agent as "wrapped" in its own subdirectory
# (<group>/<name>/agent.md) rather than a bare <group>/<name>.md file.
WRAPPED_AGENT_BASENAME = "agent.md"

# Substring marking a fixture/example data file (e.g. foo.example.json).
FIXTURE_MARKER = ".example."

# Directory names where fixtures/examples are expected to live and are
# therefore NOT flagged when found among executables.
FIXTURE_HOME_DIRS = ("tests", "fixtures", "examples")


def _consistency_agent_wrapping(base_dir):
    """SHAPE-1: within one logical agent group, siblings must share a wrapping
    style. A group mixing bare `<name>.md` files with `<name>/agent.md`
    subdirectory-wrapped agents is flagged.

    'Group' is the directory that logically contains an agent: for a bare
    agent it is the file's parent dir; for a wrapped agent (basename
    `agent.md`) it is the parent of its wrapping subdir. This means a mixed
    group like agents/egress/ (bare export-tasks.md alongside
    export-tasks-github/agent.md) is flagged, while a uniformly-bare group
    (agents/seed-review/) and the top-level agents/ bucket (bare agents plus
    group *subdirectories*, which contribute their own groups, not agents at
    the agents/ level) are not."""
    registry_agents = load_registry(base_dir)
    groups = defaultdict(list)  # group_dir -> [(name, relpath, shape)]
    for entry in registry_agents:
        name = entry.get("name")
        file_ = entry.get("file")
        if not name or not file_:
            continue
        parts = file_.split("/")
        if os.path.basename(file_) == WRAPPED_AGENT_BASENAME and len(parts) >= 3:
            group_dir = "/".join(parts[:-2])
            shape = "wrapped"
        else:
            group_dir = "/".join(parts[:-1])
            shape = "bare"
        groups[group_dir].append((name, file_, shape))

    findings = []
    for group_dir in sorted(groups):
        members = sorted(groups[group_dir])
        if len({shape for _, _, shape in members}) < 2:
            continue  # uniform group -- fine
        member_desc = ", ".join(f"{name} ({shape}: {rel})" for name, rel, shape in members)
        findings.append({
            "principle": "SHAPE",
            "title": f"Agent group '{group_dir}' mixes wrapping styles (bare file vs agent.md subdir)",
            "nodes": sorted(f"agent:{name}" for name, _, _ in members),
            "evidence": [
                {"fact": f"agent '{name}' is {shape} at '{rel}'"}
                for name, rel, shape in members
            ],
            "detail": (
                f"Agents under '{group_dir}' use inconsistent shapes: {member_desc}. "
                f"Pick one shape for the whole group -- either every agent is a bare "
                f"'<name>.md' or every agent is a '<name>/agent.md' subdirectory -- so "
                f"siblings in the same group look the same on disk."
            ),
        })
    return findings


def _consistency_stray_fixtures(base_dir):
    """SHAPE-2: a fixture/example data file (`*.example.*`) sitting directly
    among executables under scripts/ instead of in scripts/tests/ or a
    dedicated fixtures/ directory."""
    findings = []
    for rel in sorted(discover_scripts(base_dir)):
        if FIXTURE_MARKER not in os.path.basename(rel):
            continue
        if any(part in FIXTURE_HOME_DIRS for part in rel.split("/")[:-1]):
            continue  # already beside tests/fixtures -- fine
        findings.append({
            "principle": "SHAPE",
            "title": f"Fixture '{rel}' sits among executables in scripts/",
            "nodes": [f"script:{rel}"],
            "evidence": [{
                "fact": (
                    f"'{rel}' matches the '*{FIXTURE_MARKER}*' fixture pattern and sits "
                    f"outside any {'/'.join(FIXTURE_HOME_DIRS)} directory"
                )
            }],
            "detail": (
                f"'{rel}' is a fixture/example data file sitting directly among "
                f"executable scripts. Move it into scripts/tests/ (or a dedicated "
                f"fixtures/ directory) so scripts/ holds executables and its fixtures "
                f"live beside the tests that use them."
            ),
        })
    return findings


def consistency_findings(base_dir):
    """Filesystem-shape ("SHAPE") findings: organizational inconsistencies the
    edge-graph litmus set (dependency-graph-findings.py) is structurally blind
    to, because they live in on-disk naming/layout, not in references.

    Where check_integrity mirrors build_graph's edge signals, this reasons
    about *shape*: how sibling artifacts are wrapped, and whether non-code
    fixtures are loose among executables. Each returned dict is a partial
    finding (no id) shaped like dependency-graph-findings.py output --
    {"principle": "SHAPE", "title", "nodes", "evidence", "detail"}. scanner.py's
    --consistency mode sorts these, assigns F-ids, and wraps them in the
    standard findings envelope.

    Deliberately NOT flagged (same philosophy as
    docs/adr/0001-agents-and-scripts-are-a-flat-shared-pool.md): a flat,
    heterogeneous shared bucket is a design choice, not a defect. resources/
    mixing .md docs and .json config is intentional, so 'mixed file types in a
    bucket' is not a rule here -- only genuine within-group inconsistency is.

    Rules:
      SHAPE-1  A logical agent group mixes wrapping styles (bare <name>.md vs
               <name>/agent.md subdir). See _consistency_agent_wrapping.
      SHAPE-2  A `*.example.*` fixture lives among executables under scripts/.
               See _consistency_stray_fixtures.
    """
    base_dir = os.path.abspath(base_dir)
    return _consistency_agent_wrapping(base_dir) + _consistency_stray_fixtures(base_dir)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "base_dir", nargs="?",
        default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        help="path to claude-code-shared/ root (default: parent of this script)",
    )
    parser.add_argument("--out", help="write graph JSON here instead of stdout")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    graph = build_graph(args.base_dir)
    out_text = json.dumps(graph, indent=2) + "\n"

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_text)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
