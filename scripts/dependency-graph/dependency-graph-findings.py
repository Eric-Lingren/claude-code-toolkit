#!/usr/bin/env python3
"""dependency-graph-findings.py — generic findings engine over a dependency graph.

This is the repo-agnostic *analysis core* of the improve-directory-structure
scanner. It does not walk a repo and does not know how to derive nodes/edges
for any particular repo type (that is the job of per-repo-type extractors,
e.g. a claude-tooling extractor or a generic import-graph extractor). It only
consumes an already-built {nodes, edges, metrics} graph and computes findings
against a curated architecture litmus set:

  SRP         fan-out spans unrelated clusters -> node should split
  CCP/LOCALITY  edges point to one owner cluster but node lives in a shared
                bucket -> node should move next to its owner
  CRP         a shared-bucket node has exactly one consumer -> co-locate or
                inline it instead of treating it as shared
  DRY         two nodes have near-identical content/edge signatures ->
                consolidate
  ADP         a cycle exists in the edge graph -> break it
  SDP         a churny node has high fan-in (many stable dependents rely on
                unstable code) -> stabilize or invert the dependency
  MODULARITY  a cluster's internal-edge ratio is low relative to its
                cross-cluster edges -> the directory is really two concerns

Every finding cites the principle plus the exact node(s)/edge(s) that prove
the violation. Deterministic and tier-less: same input always yields the same
findings JSON, byte for byte (findings are sorted before IDs are assigned).

## Input contract

A JSON document (file arg or stdin) shaped:

{
  "nodes": [
    {
      "id": "<unique string>",
      "path": "<repo-relative path>",
      "type": "skill|agent|script|contract|resource|other",
      "cluster": "<owning group label, e.g. a skill/dir name>",
      "is_shared": <bool, optional, default false>,
          // true = this node lives in a common/shared bucket (resources/,
          // contracts/, a top-level shared/ dir, ...) rather than being
          // co-located with a single owner.
      "content_signature": [<string>, ...]   // optional, for DRY detection.
          // A set of tokens/shingles describing the node's content or
          // structural shape. Extractors decide how to derive this
          // (e.g. normalized import list, doc shingles, AST fingerprints).
    },
    ...
  ],
  "edges": [
    { "from": "<node id>", "to": "<node id>", "kind": "<string, optional>" },
    ...
  ],
  "metrics": {
    "<node id>": {
      "fan_in": <int>,
      "fan_out": <int>,
      "dir_depth": <int>,
      "orphan": <bool>,
      "churn": <int, optional, default 0>
          // relative change-frequency signal (e.g. commit count). Only
          // required for SDP; nodes without it are skipped for that check.
    },
    ...
  }
}

`edges` are directed: {"from": A, "to": B} means "A depends on / references
B". fan_out(A) counts outgoing edges, fan_in(B) counts incoming edges.
Extractors are expected to supply fan_in/fan_out/dir_depth/orphan directly in
`metrics` (this script trusts them, falling back to deriving fan_in/fan_out
from `edges` when a node's metrics entry is missing those keys).

## Output contract

{
  "schema_version": "1",
  "generated_at": "<ISO-8601 UTC>",
  "summary": {"nodes": <int>, "edges": <int>, "findings": <int>},
  "findings": [
    {
      "id": "F0001",
      "principle": "SRP|CCP|CRP|DRY|ADP|SDP|MODULARITY",
      "title": "<one line>",
      "nodes": [<node id>, ...],
      "evidence": [ {"edge": {"from":..,"to":..,"kind":..}} | {"fact": "..."} ],
      "detail": "<human-readable explanation citing the evidence>"
    },
    ...
  ]
}

## Usage

  python3 dependency-graph-findings.py <graph.json>
  cat graph.json | python3 dependency-graph-findings.py
  python3 dependency-graph-findings.py <graph.json> --out findings.json

Threshold flags (all optional, defaults documented in --help):
  --srp-min-fanout, --srp-min-clusters
  --dry-min-similarity
  --sdp-min-churn, --sdp-min-fanin
  --modularity-min-edges, --modularity-max-ratio
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# Loading / validation
# --------------------------------------------------------------------------

def load_graph(path):
    if path:
        with open(path) as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: could not parse input JSON: {e}", file=sys.stderr)
        sys.exit(1)

    for key in ("nodes", "edges"):
        if key not in data or not isinstance(data[key], list):
            print(f"ERROR: input must have a top-level '{key}' array", file=sys.stderr)
            sys.exit(1)
    if "metrics" not in data or not isinstance(data["metrics"], dict):
        data["metrics"] = {}

    node_ids = set()
    for n in data["nodes"]:
        nid = n.get("id")
        if not nid:
            print(f"ERROR: node missing 'id': {n}", file=sys.stderr)
            sys.exit(1)
        if nid in node_ids:
            print(f"ERROR: duplicate node id: {nid}", file=sys.stderr)
            sys.exit(1)
        node_ids.add(nid)

    for e in data["edges"]:
        for side in ("from", "to"):
            if e.get(side) not in node_ids:
                print(f"ERROR: edge references unknown node '{e.get(side)}': {e}", file=sys.stderr)
                sys.exit(1)

    return data


# --------------------------------------------------------------------------
# Graph helpers
# --------------------------------------------------------------------------

def build_indexes(data):
    nodes_by_id = {n["id"]: n for n in data["nodes"]}
    out_edges = defaultdict(list)
    in_edges = defaultdict(list)
    for e in data["edges"]:
        out_edges[e["from"]].append(e)
        in_edges[e["to"]].append(e)
    return nodes_by_id, out_edges, in_edges


def node_metric(metrics, node_id, key, default):
    return metrics.get(node_id, {}).get(key, default)


def edge_ref(e):
    ref = {"from": e["from"], "to": e["to"]}
    if e.get("kind"):
        ref["kind"] = e["kind"]
    return ref


# --------------------------------------------------------------------------
# ADP — cycle detection
# --------------------------------------------------------------------------

def find_cycles(nodes_by_id, out_edges):
    visited = set()
    on_stack = set()
    stack = []
    raw_cycles = []

    def dfs(u):
        visited.add(u)
        stack.append(u)
        on_stack.add(u)
        for e in out_edges.get(u, []):
            v = e["to"]
            if v not in visited:
                dfs(v)
            elif v in on_stack:
                idx = stack.index(v)
                raw_cycles.append(list(stack[idx:]))
        stack.pop()
        on_stack.discard(u)

    for nid in nodes_by_id:
        if nid not in visited:
            dfs(nid)

    seen = set()
    cycles = []
    for cyc in raw_cycles:
        min_idx = cyc.index(min(cyc))
        rotated = tuple(cyc[min_idx:] + cyc[:min_idx])
        if rotated in seen:
            continue
        seen.add(rotated)
        cycles.append(list(rotated))
    return cycles


def detect_adp(nodes_by_id, out_edges):
    findings = []
    for cycle in find_cycles(nodes_by_id, out_edges):
        ring = cycle + [cycle[0]]
        evidence = []
        for a, b in zip(ring, ring[1:]):
            match = next((e for e in out_edges.get(a, []) if e["to"] == b), None)
            evidence.append({"edge": edge_ref(match) if match else {"from": a, "to": b}})
        findings.append({
            "principle": "ADP",
            "title": f"Cycle in edge graph: {' -> '.join(ring)}",
            "nodes": list(cycle),
            "evidence": evidence,
            "detail": (
                f"Nodes {', '.join(cycle)} form a dependency cycle "
                f"({' -> '.join(ring)}). Break the cycle by inverting or "
                f"extracting one of the cited edges."
            ),
        })
    return findings


# --------------------------------------------------------------------------
# SRP — fan-out spans unrelated clusters
# --------------------------------------------------------------------------

def detect_srp(nodes_by_id, out_edges, metrics, min_fanout, min_clusters):
    findings = []
    for nid, node in nodes_by_id.items():
        edges = out_edges.get(nid, [])
        fanout = node_metric(metrics, nid, "fan_out", len(edges))
        if fanout < min_fanout:
            continue
        by_cluster = defaultdict(list)
        for e in edges:
            tgt = nodes_by_id.get(e["to"])
            tgt_cluster = tgt.get("cluster") if tgt else None
            by_cluster[tgt_cluster].append(e)
        if len(by_cluster) < min_clusters:
            continue
        evidence = [{"edge": edge_ref(e)} for e in edges]
        cluster_list = ", ".join(sorted(str(c) for c in by_cluster))
        findings.append({
            "principle": "SRP",
            "title": f"{nid} fans out across {len(by_cluster)} unrelated clusters",
            "nodes": [nid],
            "evidence": evidence,
            "detail": (
                f"{nid} has fan-out {fanout} spanning clusters [{cluster_list}]. "
                f"A single node depending on this many unrelated clusters is "
                f"doing more than one job; split it along cluster lines."
            ),
        })
    return findings


# --------------------------------------------------------------------------
# CCP/locality and CRP — shared-bucket placement
# --------------------------------------------------------------------------

def detect_ccp_and_crp(nodes_by_id, in_edges, metrics):
    ccp_findings = []
    crp_findings = []
    for nid, node in nodes_by_id.items():
        if not node.get("is_shared"):
            continue
        edges = in_edges.get(nid, [])
        fan_in = node_metric(metrics, nid, "fan_in", len(edges))
        if fan_in == 0:
            continue
        source_clusters = set()
        for e in edges:
            src = nodes_by_id.get(e["from"])
            source_clusters.add(src.get("cluster") if src else None)

        if fan_in == 1:
            owner = nodes_by_id.get(edges[0]["from"])
            owner_cluster = owner.get("cluster") if owner else None
            crp_findings.append({
                "principle": "CRP",
                "title": f"{nid} is a shared-bucket node with a single consumer",
                "nodes": [nid, edges[0]["from"]],
                "evidence": [{"edge": edge_ref(edges[0])}],
                "detail": (
                    f"{nid} is marked shared but has exactly one consumer "
                    f"({edges[0]['from']}, cluster '{owner_cluster}'). Co-locate "
                    f"or inline {nid} next to its sole consumer instead of "
                    f"bucketing it as shared."
                ),
            })
        elif len(source_clusters) == 1:
            (owner_cluster,) = source_clusters
            evidence = [{"edge": edge_ref(e)} for e in edges]
            ccp_findings.append({
                "principle": "CCP",
                "title": f"{nid} is shared but every consumer belongs to cluster '{owner_cluster}'",
                "nodes": [nid] + [e["from"] for e in edges],
                "evidence": evidence,
                "detail": (
                    f"{nid} is marked shared, but all {fan_in} incoming edges "
                    f"originate from cluster '{owner_cluster}'. Move {nid} next "
                    f"to that owner instead of leaving it in the shared bucket."
                ),
            })
    return ccp_findings, crp_findings


# --------------------------------------------------------------------------
# DRY — content/edge similarity
# --------------------------------------------------------------------------

def jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def detect_dry(nodes_by_id, out_edges, min_similarity):
    findings = []
    ids = sorted(nodes_by_id.keys())
    for i, a in enumerate(ids):
        node_a = nodes_by_id[a]
        sig_a = node_a.get("content_signature")
        for b in ids[i + 1:]:
            node_b = nodes_by_id[b]
            sig_b = node_b.get("content_signature")

            if sig_a and sig_b:
                sim = jaccard(sig_a, sig_b)
                basis = "content_signature"
            else:
                targets_a = {e["to"] for e in out_edges.get(a, [])}
                targets_b = {e["to"] for e in out_edges.get(b, [])}
                sim = jaccard(targets_a, targets_b)
                basis = "edge targets"

            if sim >= min_similarity:
                findings.append({
                    "principle": "DRY",
                    "title": f"{a} and {b} are {sim:.0%} similar ({basis})",
                    "nodes": [a, b],
                    "evidence": [{"fact": f"{basis} similarity = {sim:.3f} (threshold {min_similarity})"}],
                    "detail": (
                        f"{a} and {b} share {sim:.0%} of their {basis}, at or "
                        f"above the {min_similarity:.0%} DRY threshold. "
                        f"Consolidate them into a single node."
                    ),
                })
    return findings


# --------------------------------------------------------------------------
# SDP — churny node with high fan-in
# --------------------------------------------------------------------------

def detect_sdp(nodes_by_id, in_edges, metrics, min_churn, min_fanin):
    findings = []
    for nid in nodes_by_id:
        m = metrics.get(nid, {})
        if "churn" not in m:
            continue
        churn = m["churn"]
        fan_in = node_metric(metrics, nid, "fan_in", len(in_edges.get(nid, [])))
        if churn < min_churn or fan_in < min_fanin:
            continue
        dependents = sorted({e["from"] for e in in_edges.get(nid, [])})
        findings.append({
            "principle": "SDP",
            "title": f"{nid} is churny (churn={churn}) with {fan_in} dependents",
            "nodes": [nid] + dependents,
            "evidence": [{"edge": edge_ref(e)} for e in in_edges.get(nid, [])],
            "detail": (
                f"{nid} has churn {churn} (>= {min_churn}) yet {fan_in} nodes "
                f"depend on it (>= {min_fanin}): {', '.join(dependents)}. "
                f"Stable nodes should not depend on unstable ones -- stabilize "
                f"{nid} or invert the dependency."
            ),
        })
    return findings


# --------------------------------------------------------------------------
# MODULARITY — cluster cohesion/coupling score
# --------------------------------------------------------------------------

def detect_modularity(nodes_by_id, edges, min_edges, max_ratio):
    findings = []
    cluster_of = {nid: n.get("cluster") for nid, n in nodes_by_id.items()}
    touching = defaultdict(list)
    internal = defaultdict(list)
    for e in edges:
        c_from = cluster_of.get(e["from"])
        c_to = cluster_of.get(e["to"])
        for c in {c_from, c_to}:
            if c is not None:
                touching[c].append(e)
        if c_from is not None and c_from == c_to:
            internal[c_from].append(e)

    for cluster, touch_edges in sorted(touching.items(), key=lambda kv: str(kv[0])):
        total = len(touch_edges)
        if total < min_edges:
            continue
        ratio = len(internal.get(cluster, [])) / total
        if ratio < max_ratio:
            cross_edges = [e for e in touch_edges if e not in internal.get(cluster, [])]
            findings.append({
                "principle": "MODULARITY",
                "title": f"Cluster '{cluster}' has low internal-edge ratio ({ratio:.0%})",
                "nodes": sorted({nid for nid, c in cluster_of.items() if c == cluster}),
                "evidence": [{"edge": edge_ref(e)} for e in cross_edges],
                "detail": (
                    f"Cluster '{cluster}' has an internal-edge ratio of "
                    f"{ratio:.0%} across {total} touching edges (below the "
                    f"{max_ratio:.0%} threshold). This directory likely bundles "
                    f"two separate concerns; consider splitting along the "
                    f"cross-cluster edges cited above."
                ),
            })
    return findings


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def sort_key(f):
    return (f["principle"], tuple(f["nodes"]), f["title"])


def assemble_findings(data, thresholds):
    nodes_by_id, out_edges, in_edges = build_indexes(data)
    metrics = data["metrics"]

    all_findings = []
    all_findings += detect_adp(nodes_by_id, out_edges)
    all_findings += detect_srp(
        nodes_by_id, out_edges, metrics,
        thresholds["srp_min_fanout"], thresholds["srp_min_clusters"],
    )
    ccp, crp = detect_ccp_and_crp(nodes_by_id, in_edges, metrics)
    all_findings += ccp
    all_findings += crp
    all_findings += detect_dry(nodes_by_id, out_edges, thresholds["dry_min_similarity"])
    all_findings += detect_sdp(
        nodes_by_id, in_edges, metrics,
        thresholds["sdp_min_churn"], thresholds["sdp_min_fanin"],
    )
    all_findings += detect_modularity(
        nodes_by_id, data["edges"],
        thresholds["modularity_min_edges"], thresholds["modularity_max_ratio"],
    )

    all_findings.sort(key=sort_key)
    for i, f in enumerate(all_findings, start=1):
        f["id"] = f"F{i:04d}"
    # Put id first for readability.
    ordered = []
    for f in all_findings:
        ordered.append({
            "id": f["id"],
            "principle": f["principle"],
            "title": f["title"],
            "nodes": f["nodes"],
            "evidence": f["evidence"],
            "detail": f["detail"],
        })

    return {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "nodes": len(data["nodes"]),
            "edges": len(data["edges"]),
            "findings": len(ordered),
        },
        "findings": ordered,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Generic findings engine over a {nodes, edges, metrics} dependency graph.",
    )
    p.add_argument("input", nargs="?", help="Path to graph JSON. Defaults to stdin.")
    p.add_argument("--out", help="Write findings JSON here instead of stdout.")

    p.add_argument("--srp-min-fanout", type=int, default=4,
                    help="Minimum fan-out for SRP consideration (default: 4).")
    p.add_argument("--srp-min-clusters", type=int, default=3,
                    help="Minimum distinct target clusters for SRP (default: 3).")
    p.add_argument("--dry-min-similarity", type=float, default=0.8,
                    help="Jaccard similarity threshold for DRY (default: 0.8).")
    p.add_argument("--sdp-min-churn", type=int, default=5,
                    help="Minimum churn for SDP consideration (default: 5).")
    p.add_argument("--sdp-min-fanin", type=int, default=5,
                    help="Minimum fan-in for SDP consideration (default: 5).")
    p.add_argument("--modularity-min-edges", type=int, default=6,
                    help="Minimum touching edges before scoring a cluster (default: 6).")
    p.add_argument("--modularity-max-ratio", type=float, default=0.5,
                    help="Internal-edge ratio below which a cluster is flagged (default: 0.5).")

    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.input and args.input != "-":
        data = load_graph(args.input)
    else:
        data = load_graph(None)

    thresholds = {
        "srp_min_fanout": args.srp_min_fanout,
        "srp_min_clusters": args.srp_min_clusters,
        "dry_min_similarity": args.dry_min_similarity,
        "sdp_min_churn": args.sdp_min_churn,
        "sdp_min_fanin": args.sdp_min_fanin,
        "modularity_min_edges": args.modularity_min_edges,
        "modularity_max_ratio": args.modularity_max_ratio,
    }

    result = assemble_findings(data, thresholds)
    out_text = json.dumps(result, indent=2) + "\n"

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_text)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
