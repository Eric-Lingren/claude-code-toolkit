#!/usr/bin/env python3
"""scanner.py -- CLI dispatcher for the dependency-graph scanner.

The scanner is split into two layers (see dependency-graph-findings.py for the
full rationale):

  extractor  (repo-type specific)  -> emits {nodes, edges, metrics} JSON
  core       (dependency-graph-findings.py, repo-agnostic) -> emits findings

This script is the thin CLI glue that picks an extractor by name and runs it
against a target directory. It does not compute findings itself -- pipe its
output into dependency-graph-findings.py for that:

  python3 scanner.py --extractor claude-tooling \\
    | python3 dependency-graph-findings.py

## Extractors

  claude-tooling  -- walks a claude-code-shared-shaped repo (see
                     claude_tooling_extractor.py for exactly which artifacts
                     become nodes/edges).
  generic-code    -- walks an arbitrary JS/TS or Python code directory (see
                     generic_code_extractor.py). One node per source file,
                     edges resolved from import/require statements that
                     point at another on-disk file in the same tree.

(Additional extractors register themselves in the EXTRACTORS map below as
they're built.)

## --check mode

  python3 scanner.py --check [root]
  python3 scanner.py --check --extractor claude-tooling [root]

Reference-integrity mode. Builds the graph exactly as above, then reuses the
same edge-resolution pass (an extractor's optional check_integrity(base_dir)
function, if it defines one) to find references that DON'T resolve to an
on-disk artifact -- the inverse of the normal build_graph behavior, which
silently drops anything that fails to resolve. Also runs the ADP (cycle)
finding from dependency-graph-findings.py against the built graph. Prints a
report and exits non-zero if any dangling reference or cycle is found, zero
otherwise. Defaults to the claude-tooling extractor when --extractor is
omitted, since --check's integrity signals (registry.json, model-tiers.json,
skill-pipeline.json, and literal absolute-path references) are claude-tooling
concepts; generic-code has no check_integrity and --check against it will
only run cycle detection.

## --consistency mode

  python3 scanner.py --consistency [root]
  python3 scanner.py --consistency --extractor claude-tooling [root]

Filesystem-shape mode. Emits SHAPE findings -- organizational inconsistencies
that live in on-disk naming/layout rather than in the edge graph, so the
graph-based analysis core (dependency-graph-findings.py) is structurally blind
to them. Delegates to an extractor's optional consistency_findings(base_dir)
function, then sorts + assigns F-ids and wraps the result in the SAME findings
envelope dependency-graph-findings.py produces, so SHAPE findings compose with
the litmus findings. Defaults to the claude-tooling extractor when --extractor
is omitted (its consistency rules -- agent wrapping style, stray fixtures --
are claude-tooling conventions); an extractor with no consistency_findings()
hook yields an empty finding set. Unlike --check this is advisory: it always
exits 0 and just reports. Honors --out.

## Usage

  python3 scanner.py --extractor claude-tooling [root]
  python3 scanner.py --extractor claude-tooling --out graph.json
  python3 scanner.py --extractor generic-code [root]
  python3 scanner.py --check [root]
  python3 scanner.py --consistency [root]
  python3 scanner.py --list-extractors
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_tooling_extractor  # noqa: E402
import generic_code_extractor  # noqa: E402

EXTRACTORS = {
    "claude-tooling": claude_tooling_extractor.build_graph,
    "generic-code": generic_code_extractor.build_graph,
}

# Full extractor modules (not just their build_graph function), so --check
# can look for an optional check_integrity(base_dir) on each.
EXTRACTOR_MODULES = {
    "claude-tooling": claude_tooling_extractor,
    "generic-code": generic_code_extractor,
}


def default_root_for(extractor_name):
    if extractor_name == "claude-tooling":
        # claude-tooling only makes sense against the claude-code-shared repo
        # this script itself lives in.
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.getcwd()


def detect_cycles(graph):
    """Reuse dependency-graph-findings.py's ADP (cycle) detection against an
    already-built graph, rather than re-implementing cycle detection here.
    That script's filename isn't a valid Python module name (hyphens), so it
    is invoked as a subprocess exactly the way its own docstring documents
    piping into it -- this is the same code path, not a parallel one.

    `pipeline_next` edges (skill-pipeline.json's next-skill suggestions) are
    excluded before detection. That graph is a workflow/suggestion DAG, not a
    dependency graph -- it loops back by design (e.g. dispatch-tasks ->
    to-e2e-tasks -> dispatch-tasks so generated e2e tasks get dispatched;
    prototype -> to-seed -> prototype so a captured seed can send you back to
    prototype more). ADP (Acyclic Dependencies Principle) targets structural
    dependency cycles; flagging intentional workflow loops here would make
    --check permanently and uninformatively red."""
    findings_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dependency-graph-findings.py")
    filtered_graph = dict(graph)
    filtered_graph["edges"] = [e for e in graph["edges"] if e.get("kind") != "pipeline_next"]
    try:
        proc = subprocess.run(
            [sys.executable, findings_script],
            input=json.dumps(filtered_graph), capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        print(f"WARNING: cycle detection sub-invocation failed to run: {e}", file=sys.stderr)
        return []
    if proc.returncode != 0:
        print(f"WARNING: cycle detection sub-invocation exited non-zero: {proc.stderr.strip()}", file=sys.stderr)
        return []
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"WARNING: could not parse cycle detection output: {e}", file=sys.stderr)
        return []
    return [f for f in result.get("findings", []) if f.get("principle") == "ADP"]


def run_check(extractor_name, root):
    """--check entrypoint: build the graph, run integrity + cycle checks,
    print a report, and return a process exit code (0 = clean, 1 = found
    dangling references and/or cycles)."""
    build_graph = EXTRACTORS[extractor_name]
    module = EXTRACTOR_MODULES[extractor_name]
    graph = build_graph(root)

    issues = []
    if hasattr(module, "check_integrity"):
        issues = module.check_integrity(root)

    cycles = detect_cycles(graph)

    print(f"scanner --check: extractor={extractor_name} root={root}")
    print(f"  nodes={len(graph['nodes'])} edges={len(graph['edges'])}")
    print(f"  dangling references: {len(issues)}")
    for issue in issues:
        print(f"    [{issue['kind']}] {issue['source']} -> {issue['target']}: {issue['detail']}")
    print(f"  cycles: {len(cycles)}")
    for finding in cycles:
        ring = finding["nodes"] + [finding["nodes"][0]]
        print(f"    {finding['id']}: {' -> '.join(ring)}")

    if issues or cycles:
        print(f"FAIL: {len(issues)} dangling reference(s), {len(cycles)} cycle(s) found.")
        return 1
    print("OK: no dangling edges, no cycles.")
    return 0


def run_consistency(extractor_name, root):
    """--consistency entrypoint: collect SHAPE findings from the extractor's
    optional consistency_findings(base_dir) hook, sort them deterministically,
    assign F-ids, and return the standard findings envelope (the same shape
    dependency-graph-findings.py emits, so SHAPE findings compose with the
    litmus findings the analysis core produces).

    SHAPE is a companion finding class to the edge-graph litmus set, not part
    of it: it is derived from on-disk layout/naming, which the graph-based core
    cannot see. An extractor without a consistency_findings() hook yields an
    empty finding set with an explanatory note."""
    module = EXTRACTOR_MODULES[extractor_name]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not hasattr(module, "consistency_findings"):
        return {
            "schema_version": "1",
            "generated_at": generated_at,
            "summary": {"findings": 0},
            "findings": [],
            "note": f"extractor '{extractor_name}' defines no consistency_findings()",
        }
    raw = list(module.consistency_findings(root))
    raw.sort(key=lambda f: (f["principle"], tuple(f["nodes"]), f["title"]))
    findings = []
    for i, f in enumerate(raw, start=1):
        findings.append({
            "id": f"F{i:04d}",
            "principle": f["principle"],
            "title": f["title"],
            "nodes": f["nodes"],
            "evidence": f["evidence"],
            "detail": f["detail"],
        })
    return {
        "schema_version": "1",
        "generated_at": generated_at,
        "summary": {"findings": len(findings)},
        "findings": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--extractor", choices=sorted(EXTRACTORS.keys()),
        help="which repo-type extractor to run",
    )
    parser.add_argument(
        "root", nargs="?", default=None,
        help="repo root to scan (default: extractor-specific; for "
             "claude-tooling, the claude-code-shared repo this script lives in)",
    )
    parser.add_argument("--out", help="write graph JSON here instead of stdout")
    parser.add_argument(
        "--list-extractors", action="store_true",
        help="print the registered extractor names and exit",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="reference-integrity mode: report dangling edges/cycles and "
             "exit non-zero if any are found (see module docstring)",
    )
    parser.add_argument(
        "--consistency", action="store_true",
        help="filesystem-shape mode: emit SHAPE findings (organizational "
             "inconsistencies the edge graph can't see) as a findings-JSON "
             "envelope (see module docstring)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_extractors:
        for name in sorted(EXTRACTORS.keys()):
            print(name)
        return

    if not args.extractor:
        if args.check or args.consistency:
            args.extractor = "claude-tooling"
        else:
            parser.error("--extractor is required (see --list-extractors)")

    root = args.root or default_root_for(args.extractor)
    if not os.path.isdir(root):
        print(f"ERROR: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        sys.exit(run_check(args.extractor, root))

    if args.consistency:
        result = run_consistency(args.extractor, root)
        out_text = json.dumps(result, indent=2) + "\n"
        if args.out:
            with open(args.out, "w") as f:
                f.write(out_text)
        else:
            sys.stdout.write(out_text)
        return

    build_graph = EXTRACTORS[args.extractor]
    graph = build_graph(root)
    out_text = json.dumps(graph, indent=2) + "\n"

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_text)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
