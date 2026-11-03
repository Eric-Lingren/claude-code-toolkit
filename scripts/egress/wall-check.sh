#!/usr/bin/env bash
# wall-check.sh — single source of truth for the work-to-personal wall rule.
# Read-only. Used by agents/egress/export-tasks.md (step 4, blocks on a hard
# violation) and skills/dispatch-tasks/SKILL.md (preview WARNING lines only).
#
# Usage:
#   wall-check.sh [--mode default|capture] <source> <dest>
#
#   <source>, <dest>  A repo ("Org/Repo", a key in repo-policy.json) or a domain
#                     name (a "domain" value in repo-policy.json / a key in
#                     task-routing.json destinations). A repo resolves to its
#                     repo-policy.json "domain". Source "-" means the cwd repo,
#                     resolved via scripts/pick/resolve-repo.sh. Nothing is
#                     hardcoded; each domain's wall comes from
#                     task-routing.json destinations[domain].wall.
#
# Rules (each corporate-wall domain is one sealed client or employer; compare
# by domain, not just wall type, so two clients never cross):
#   corporate-to-anywhere   source wall is corporate and dest domain differs.
#                           HARD. Includes another corporate domain.
#   anywhere-to-corporate   source is not corporate and dest wall is corporate.
#                           HARD.
#   project-to-personal     source wall is project and dest wall is personal.
#                           SOFT: WARNING line on stdout, exit 0.
#   same domain, personal->project, anything else: pass, no output.
#
# Exit codes (the contract):
#   0   pass. A soft violation still exits 0 and prints "WARNING: ..." on stdout.
#   1   blocked. Hard violation; reason on stderr. In --mode capture this is
#       used only for cases capture cannot survive (anywhere-to-corporate).
#   2   usage or config error (bad args, unknown repo/domain, missing file).
#       Fails closed: callers treat any non-zero as "do not write".
#   10  sealed-required. Only with --mode capture: a corporate source routed
#       outside its own domain. Capture may continue only if the record is
#       sealed (kept inside the corporate domain). Reason on stderr.
#
# Modes:
#   default   hard violations exit 1 (export-tasks behavior).
#   capture   corporate-to-anywhere exits 10 instead of 1; all else as default.
#
# Test hooks: WALL_POLICY overrides repo-policy.json, WALL_ROUTING overrides
# task-routing.json, PICK_REPO / PICK_POLICY pass through to resolve-repo.sh.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$(cd "$HERE/../.." && pwd)"
POLICY="${WALL_POLICY:-$SHARED/resources/repo-policy.json}"
ROUTING="${WALL_ROUTING:-$SHARED/resources/task-routing.json}"

die() { echo "wall-check: $*" >&2; exit 2; }

mode=default
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)
      [ $# -ge 2 ] || die "--mode needs a value (default|capture)"
      mode="$2"; shift 2 ;;
    --mode=*) mode="${1#--mode=}"; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
case "$mode" in default|capture) ;; *) die "unknown mode '$mode' (expected default|capture)" ;; esac
[ "${#args[@]}" -eq 2 ] || die "usage: wall-check.sh [--mode default|capture] <source> <dest>"
src="${args[0]}"; dst="${args[1]}"

if [ "$src" = "-" ]; then
  out=$(bash "$SHARED/scripts/pick/resolve-repo.sh" 2>/dev/null) || die "resolve-repo.sh failed"
  case "$out" in
    "{"*) src=$(printf '%s' "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["repo"])') ;;
    "no issue_tracker configured for "*) src="${out#no issue_tracker configured for }" ;;
    *) die "cannot resolve cwd repo" ;;
  esac
fi

[ -f "$POLICY" ] || die "repo-policy not found: $POLICY"
[ -f "$ROUTING" ] || die "task-routing not found: $ROUTING"

SRC="$src" DST="$dst" MODE="$mode" POLICY="$POLICY" ROUTING="$ROUTING" python3 - <<'PY'
import json, os, sys

policy = json.load(open(os.environ["POLICY"]))
dests = json.load(open(os.environ["ROUTING"])).get("destinations", {})
mode = os.environ["MODE"]

def err(msg, code):
    print(f"wall-check: {msg}", file=sys.stderr)
    sys.exit(code)

def domain_of(name):
    entry = policy.get(name)
    if isinstance(entry, dict):
        d = entry.get("domain")
        if not d:
            err(f"repo '{name}' has no domain in repo-policy.json", 2)
        return d
    return name

def wall_of(domain):
    d = dests.get(domain)
    if not isinstance(d, dict) or "wall" not in d:
        err(f"unknown domain '{domain}' (no destinations entry with a wall in task-routing.json)", 2)
    return d["wall"]

src_d, dst_d = domain_of(os.environ["SRC"]), domain_of(os.environ["DST"])
src_w, dst_w = wall_of(src_d), wall_of(dst_d)

if src_d == dst_d:
    sys.exit(0)

if src_w == "corporate":
    msg = (f"HARD WALL VIOLATION (corporate-to-anywhere): domain '{src_d}' "
           f"(wall=corporate) routed outside its own entry to '{dst_d}' (wall={dst_w}).")
    if mode == "capture":
        err(msg + " Sealed required.", 10)
    err(msg, 1)

if dst_w == "corporate":
    err(f"HARD WALL VIOLATION (anywhere-to-corporate): domain '{src_d}' "
        f"(wall={src_w}) routed into corporate domain '{dst_d}'.", 1)

if src_w == "project" and dst_w == "personal":
    print(f"WARNING: domain '{src_d}' (wall=project) routed to personal domain '{dst_d}'. "
          "Soft boundary: a separate workspace is expected.")
sys.exit(0)
PY
