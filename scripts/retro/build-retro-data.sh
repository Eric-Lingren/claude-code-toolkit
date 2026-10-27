#!/usr/bin/env bash
# build-retro-data.sh — build compact sprint-retro signals from GitHub + Linear.
#
# Reuses the standup pipeline (fetch-github-standup.sh, fetch-linear-standup.sh,
# build-standup-data.sh) and derives retro talking-point signals from it.
#
# Must run from inside the target git repo (gh resolves the repo from cwd).
#
# Usage:
#   build-retro-data.sh [--work-dir <dir>]
#
# Options:
#   --work-dir   Directory for intermediate files (github.json, linear.json,
#                data.json). Defaults to a fresh mktemp dir.
#
# Env (test hooks):
#   RETRO_STANDUP_DATA   path to a prebuilt build-standup-data.sh output; skips
#                        all fetching.
#   RETRO_NOW            ISO 8601 "now" for deterministic open_days.
#
# Output (stdout): one JSON object:
#   {
#     "shipped": {count, median_days_to_merge, fast_merges, items:[{key,title,tag,pr,days_to_merge}]},
#     "signals": {distinct_reviewers, small_prs, ci_green_ratio},
#     "open": {count, oldest_open_days},
#     "approved_unmerged": [{key,pr,open_days,ciRollup}],
#     "approved_ci_red":   [{key,pr,open_days}],
#     "changes_requested": [{key,pr,open_days}],
#     "unresolved_threads":[{key,pr,count}],
#     "chains": [{group, open_prs:[pr...], pending_tickets}]
#   }
#
# Exit 0 on success; exit 1 on error.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STANDUP_DIR="$SCRIPT_DIR/../standup"

WORK_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --work-dir) WORK_DIR="${2:?--work-dir needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$WORK_DIR" ]; then
  WORK_DIR=$(mktemp -d)
fi
mkdir -p "$WORK_DIR"

if [ -n "${RETRO_STANDUP_DATA:-}" ]; then
  cp "$RETRO_STANDUP_DATA" "$WORK_DIR/data.json"
else
  # Write straight to files. Never round-trip JSON through `echo` (zsh echo
  # interprets backslash escapes and corrupts PR bodies).
  bash "$STANDUP_DIR/fetch-github-standup.sh" > "$WORK_DIR/github.json"
  KEY_IDS=$(python3 -I -c "import json,sys; print(','.join(json.load(open(sys.argv[1]))['key_ids']))" "$WORK_DIR/github.json")
  bash "$STANDUP_DIR/fetch-linear-standup.sh" "$KEY_IDS" > "$WORK_DIR/linear.json"
  # Empty standups dir: retro covers the whole cycle, not "since last standup".
  mkdir -p "$WORK_DIR/no-standups"
  bash "$STANDUP_DIR/build-standup-data.sh" "$WORK_DIR/linear.json" "$WORK_DIR/github.json" \
    --standups-dir "$WORK_DIR/no-standups" > "$WORK_DIR/data.json"
fi

python3 -I - "$WORK_DIR/data.json" <<'PYEOF'
import json, os, re, statistics, sys
from datetime import datetime, timezone

with open(sys.argv[1]) as f:
    d = json.load(f)

now_raw = os.environ.get("RETRO_NOW")
now = datetime.fromisoformat(now_raw.replace("Z", "+00:00")) if now_raw else datetime.now(timezone.utc)

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

def days(a, b):
    return round((b - a).total_seconds() / 86400, 1)

TAG = re.compile(r"\[([^\]]+)\]")

def tag(title):
    m = TAG.search(title or "")
    return m.group(1) if m else None

def key(entry):
    return (entry.get("ticket") or {}).get("key")

# --- shipped this cycle ---
shipped_items, merge_days = [], []
for entry in d.get("done_new", []) + d.get("done_earlier", []):
    t = entry.get("ticket") or {}
    merged = [p for p in entry.get("prs", []) if p.get("mergedAt")]
    pr = merged[0] if merged else None
    dtm = days(ts(pr["createdAt"]), ts(pr["mergedAt"])) if pr and pr.get("createdAt") else None
    if dtm is not None:
        merge_days.append(dtm)
    shipped_items.append({
        "key": t.get("key"),
        "title": t.get("title"),
        "tag": tag(t.get("title")),
        "pr": pr["number"] if pr else None,
        "days_to_merge": dtm,
    })

# --- open PRs ---
open_rows = []
for group in ("in_review", "in_progress", "todo"):
    for entry in d.get(group, []):
        for p in entry.get("prs", []):
            if p.get("state") == "OPEN":
                open_rows.append((entry, p))

def open_days(p):
    c = ts(p.get("createdAt"))
    return days(c, now) if c else None

approved = [(e, p) for e, p in open_rows if p.get("reviewDecision") == "APPROVED"]
row = lambda e, p: {"key": key(e), "pr": p["number"], "open_days": open_days(p)}

# --- chains: open work grouped by parent ticket ---
chains = {}
for group in ("in_review", "in_progress", "todo"):
    for entry in d.get(group, []):
        t = entry.get("ticket") or {}
        parent = t.get("parentKey")
        if not parent:
            continue
        c = chains.setdefault(parent, {"group": parent, "tag": tag(t.get("title")), "open_prs": [], "pending_tickets": 0})
        c["pending_tickets"] += 1
        c["open_prs"] += [p["number"] for p in entry.get("prs", []) if p.get("state") == "OPEN"]

sig = d.get("theme_signals") or {}
out = {
    "shipped": {
        "count": len(shipped_items),
        "median_days_to_merge": round(statistics.median(merge_days), 1) if merge_days else None,
        "fast_merges": sum(1 for x in merge_days if x <= 1),
        "items": shipped_items,
    },
    "signals": {
        "distinct_reviewers": sig.get("distinct_reviewers"),
        "small_prs": sig.get("small_prs"),
        "ci_green_ratio": sig.get("ci_green_ratio"),
    },
    "open": {
        "count": len(open_rows),
        "oldest_open_days": max((open_days(p) or 0 for _, p in open_rows), default=None),
    },
    "approved_unmerged": [{**row(e, p), "ciRollup": p.get("ciRollup")} for e, p in approved],
    "approved_ci_red": [row(e, p) for e, p in approved if p.get("ciRollup") == "failure"],
    "changes_requested": [row(e, p) for e, p in open_rows if p.get("reviewDecision") == "CHANGES_REQUESTED"],
    "unresolved_threads": [{"key": key(e), "pr": p["number"], "count": p["unresolvedThreadCount"]}
                           for e, p in open_rows if p.get("unresolvedThreadCount")],
    "chains": sorted((c for c in chains.values() if c["pending_tickets"] > 1),
                     key=lambda c: -c["pending_tickets"]),
}
json.dump(out, sys.stdout, indent=1)
print()
PYEOF
