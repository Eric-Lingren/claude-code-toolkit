#!/usr/bin/env python3
# cluster_flags.py: flag validated clusters that must not go to a headless run
# What: flag_clusters(clusters, rows) returns copies of clusters; flagged ones get needs_grill=true and
#   flag_reasons (list of "contradiction" / "lane-target"). Unflagged clusters are returned unchanged.
#   contradiction: the cluster carries the model's contradiction=true marker, OR two learnings of the
#     target have opposing fix text (add/remove, enable/disable, always/never, require/forbid, ...) on a shared
#     backticked section; both clusters holding those learnings are flagged.
#   lane-target: any learning's improves, or the cluster target, names a learnings-lane or review-lane
#     component. Forced regardless of confidence.
# When: after validate_clusters passes, before any headless run. Flagged clusters are listed in the kick digest.
# CLI: cluster_flags.py CLUSTERS.json ROWS.json -> prints flagged JSON. Digest: digest_lines(clusters).
import json
import re
import sys

LANE_RE = re.compile(r"(learnings|review)[-_]lane|learnings-clusterer|review-watcher", re.I)
OPPOSED = [("add", "remove"), ("enable", "disable"), ("always", "never"), ("require", "forbid"),
           ("allow", "forbid"), ("allow", "block"), ("keep", "drop"), ("include", "exclude")]
SECTION_RE = re.compile(r"`([^`]+)`")


def is_lane(name):
    return isinstance(name, str) and bool(LANE_RE.search(name))


def _words(text):
    return set(re.findall(r"[a-z]+", text.lower()))


def _sections(text):
    return set(SECTION_RE.findall(text or ""))


def opposing(fix_a, fix_b):
    """True when fix texts fix_a and fix_b share a backticked section and use opposite verbs of one pair."""
    if not (_sections(fix_a) & _sections(fix_b)):
        return False
    words_a, words_b = _words(fix_a), _words(fix_b)
    return any((verb in words_a and opposite in words_b) or (opposite in words_a and verb in words_b)
               for verb, opposite in OPPOSED)


def _contradicting_ids(rows):
    bad = set()
    fixed = [row for row in rows if isinstance(row.get("fix"), str)]
    for i, row_a in enumerate(fixed):
        for row_b in fixed[i + 1:]:
            if opposing(row_a["fix"], row_b["fix"]):
                bad.update((row_a.get("id"), row_b.get("id")))
    return bad


def flag_clusters(clusters, rows):
    by_id = {row.get("id"): row for row in rows}
    bad = _contradicting_ids(rows)
    out = []
    for cluster in clusters:
        reasons = []
        learning_ids = cluster.get("learning_ids", [])
        if cluster.get("contradiction") is True or bad & set(learning_ids):
            reasons.append("contradiction")
        if is_lane(cluster.get("target")) or any(
                is_lane(by_id.get(learning_id, {}).get("improves")) for learning_id in learning_ids):
            reasons.append("lane-target")
        out.append(dict(cluster, needs_grill=True, flag_reasons=reasons) if reasons else dict(cluster))
    return out


def headless_ready(clusters):
    return [cluster for cluster in clusters if not cluster.get("needs_grill")]


def digest_lines(clusters):
    return [f"needs-grill: {cluster['id']} ({cluster.get('target')}) "
            f"{', '.join(cluster['flag_reasons'])}: {cluster.get('root_cause')}"
            for cluster in clusters if cluster.get("needs_grill")]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    clusters = json.load(open(sys.argv[1]))
    clusters = clusters["clusters"] if isinstance(clusters, dict) else clusters
    print(json.dumps(flag_clusters(clusters, json.load(open(sys.argv[2])))))
