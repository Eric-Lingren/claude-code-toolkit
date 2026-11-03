#!/usr/bin/env bash
# capture-file.sh — deterministic driver for the capture skill.
#
#   capture-file.sh prepare <capture.json> --target <org/repo> [--source <repo|domain|->] [--plan <path>]
#       Foreground gate. Validates the capture, resolves labels, runs the wall check
#       (capture mode), flushes the outbox, looks for a duplicate, prints the preview
#       and writes a plan JSON to --plan (default: <capture.json>.plan.json).
#       Exit 0 ready for the preview; 1 invalid, blocked or not a capture target;
#       10 sealed required (rewrite anonymized, set sealed=true, rerun).
#   capture-file.sh file <capture.json> --target <org/repo> [--comment <N>]
#       Files the capture: a new issue, or a "seen again" comment on issue N.
#       Prints one line: "✓ captured #N [label] <url>" or "ERROR: <msg>" (and the
#       capture is enqueued in the outbox, exit 1).
#   capture-file.sh finish <capture.json> --target <org/repo> <adapter-response>
#       Same final line, from the response of the export-tasks-github agent
#       (a URL, or "ERROR: ...").
#   capture-file.sh flush
#       Retry the outbox on demand.
#   capture-file.sh refile <item.json>
#       Used by outbox flush. Files one queued item; target from its .target sidecar.
#
# Env: CAPTURE_RESULT_FILE, when set, receives the final line (used by the hotkey launcher).
# Env (tests): CAPTURE_ADAPTER (new-issue command: title body repo labels),
# CAPTURE_COMMENT_ADAPTER (comment command: number body repo), CAPTURE_OUTBOX_DIR,
# REPO_POLICY / WALL_POLICY / WALL_ROUTING.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED="$(cd "$HERE/../.." && pwd)"
ADAPTER="${CAPTURE_ADAPTER:-$SHARED/scripts/egress/github-issue.sh}"
COMMENT_ADAPTER="${CAPTURE_COMMENT_ADAPTER:-$SHARED/scripts/egress/github-issue-comment.sh}"
OUTBOX="$HERE/outbox.sh"
OUTBOX_DIR="${CAPTURE_OUTBOX_DIR:-$HOME/.dotfiles/local/capture-outbox}"
DEFAULT_TARGET="Eric-Lingren/dotfiles"

die() { echo "capture-file: $*" >&2; exit 1; }
usage() { sed -n '2,24p' "$0" >&2; exit 2; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
[ -n "$cmd" ] || usage

target="" source_arg="-" plan="" comment="" pos=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target) [ $# -ge 2 ] || die "--target needs a value"; target="$2"; shift 2 ;;
    --source) [ $# -ge 2 ] || die "--source needs a value"; source_arg="$2"; shift 2 ;;
    --plan) [ $# -ge 2 ] || die "--plan needs a value"; plan="$2"; shift 2 ;;
    --comment) [ $# -ge 2 ] || die "--comment needs a number"; comment="$2"; shift 2 ;;
    *) pos+=("$1"); shift ;;
  esac
done
capture="${pos[0]:-}"
result="${pos[1]:-}"
[ -n "$target" ] || target="$DEFAULT_TARGET"

field() {
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2],""); print(v if isinstance(v,str) else json.dumps(v))' "$1" "$2"
}

# $1=capture $2=target $3=adapter output. Prints the one final line; enqueues on failure.
report() {
  local cap="$1" tgt="$2" res="$3" label url n msg q name
  label="$(field "$cap" label)"
  url="$(printf '%s\n' "$res" | grep -Eo 'https://[^ ]+' | tail -1)"
  if [ -n "$url" ] && ! printf '%s' "$res" | grep -q '^ERROR'; then
    n="$(printf '%s' "$url" | sed -n 's#.*/issues/\([0-9][0-9]*\).*#\1#p')"
    [ -n "$n" ] || n="${comment:-?}"
    echo "✓ captured #$n [$label] $url"
    [ -z "${CAPTURE_RESULT_FILE:-}" ] || echo "✓ captured #$n [$label] $url" > "$CAPTURE_RESULT_FILE"
    return 0
  fi
  msg="$(printf '%s' "$res" | sed 's/^ERROR: *//' | tr '\n' ' ' | sed 's/ *$//')"
  [ -n "$msg" ] || msg="adapter returned no URL"
  q="$("$OUTBOX" enqueue "$cap" "$msg" 2>/dev/null)"
  name="${q#queued: }"
  if [ -n "$name" ] && [ -d "$OUTBOX_DIR" ]; then
    printf '%s' "$tgt" > "$OUTBOX_DIR/${name%.json}.target"
  fi
  echo "ERROR: $msg"
  [ -z "${CAPTURE_RESULT_FILE:-}" ] || echo "ERROR: $msg" > "$CAPTURE_RESULT_FILE"
  return 1
}

# $1=capture $2=target $3=comment-number. Prints adapter output; returns its status.
run_adapter() {
  local cap="$1" tgt="$2" cm="$3" body title label labels why
  body="$(python3 "$HERE/validate-capture.py" "$cap" 2>&1)" || { echo "ERROR: capture invalid"; return 1; }
  title="$(field "$cap" title)"; label="$(field "$cap" label)"
  if [ -n "$cm" ]; then
    why="$(field "$cap" why)"
    "$COMMENT_ADAPTER" "$cm" "seen again: ${why:-$title}, $(date +%F)" "$tgt" 2>&1
    return $?
  fi
  labels="$("$HERE/resolve-labels.sh" "$tgt" "$label" 2>&1)" || { echo "ERROR: $tgt is not a capture target"; return 1; }
  "$ADAPTER" "$title" "$body" "$tgt" "$labels" 2>&1
}

case "$cmd" in
  prepare)
    [ -f "$capture" ] || die "capture json required"
    [ -n "$plan" ] || plan="$capture.plan.json"

    body="$(python3 "$HERE/validate-capture.py" "$capture")" || { echo "ERROR: capture invalid" >&2; exit 1; }
    title="$(field "$capture" title)"; label="$(field "$capture" label)"; sealed="$(field "$capture" sealed)"
    labels="$("$HERE/resolve-labels.sh" "$target" "$label" 2>&1)" || { echo "ERROR: $target is not a capture target" >&2; exit 1; }

    wall_out="$("$SHARED/scripts/egress/wall-check.sh" --mode capture "$source_arg" "$target" 2>&1)"; wrc=$?
    case $wrc in
      0) ;;
      10)
        if [ "$sealed" != "true" ]; then
          echo "SEALED REQUIRED: anonymize (no client names, paths, snippets, ticket IDs, PR links, session footer), set sealed=true, empty where/session, rerun. $wall_out" >&2
          exit 10
        fi ;;
      *) echo "BLOCKED: $wall_out" >&2; exit 1 ;;
    esac
    marker=""
    [ "$sealed" = "true" ] && marker="⚠ sealed (from $source_arg)"

    notice=""
    if [ -n "$("$OUTBOX" list)" ]; then
      "$OUTBOX" flush "$0" refile >/dev/null 2>&1
      notice="outbox: retried queued captures ($("$OUTBOX" list | wc -l | tr -d ' ') still queued)"
    fi

    terms="$("$HERE/find-dupes.sh" --extract-terms "$title")"
    dupes="[]"
    [ -n "$terms" ] && dupes="$("$HERE/find-dupes.sh" "$target" "$terms" 2>/dev/null || echo '[]')"
    [ -n "$dupes" ] || dupes="[]"
    bytes="$(printf '%s' "$body" | wc -c | tr -d ' ')"

    DUPES="$dupes" python3 -c '
import json, os, sys
plan, target, label, labels, title, size, marker = sys.argv[1:8]
d = json.loads(os.environ["DUPES"])
json.dump({"target": target, "label": label, "labels": labels, "title": title,
           "body_bytes": int(size), "sealed_marker": marker,
           "dupe": d[0] if d else None}, open(plan, "w"))
' "$plan" "$target" "$label" "$labels" "$title" "$bytes" "$marker"

    [ -n "$notice" ] && echo "$notice"
    echo "capture -> $target [$label]"
    [ -n "$marker" ] && echo "$marker"
    echo "title: $title"
    echo "body: ${bytes} bytes"
    dupe_line="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))["dupe"]
print("≈#%d %s (%s)" % (d["number"], d["title"], d["url"]) if d else "")
' "$plan")"
    [ -n "$dupe_line" ] && echo "$dupe_line  (y comments there; Nnew forces a new issue)"
    echo "y / edit / cancel"
    ;;
  file)
    [ -f "$capture" ] || die "capture json required"
    out="$(run_adapter "$capture" "$target" "$comment")"; rc=$?
    if [ $rc -ne 0 ]; then case "$out" in ERROR*) ;; *) out="ERROR: $out" ;; esac; fi
    report "$capture" "$target" "$out"
    ;;
  finish)
    [ -f "$capture" ] || die "capture json required"
    report "$capture" "$target" "$result"
    ;;
  flush)
    "$OUTBOX" flush "$0" refile
    ;;
  refile)
    [ -f "$capture" ] || die "item path required"
    tf="${capture%.json}.target"
    tgt="$DEFAULT_TARGET"; [ -f "$tf" ] && tgt="$(cat "$tf")"
    out="$(run_adapter "$capture" "$tgt" "")"; rc=$?
    if [ $rc -eq 0 ]; then rm -f "$tf"; echo "$out"; exit 0; fi
    echo "$out" >&2
    exit 1
    ;;
  *) usage ;;
esac
