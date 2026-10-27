#!/usr/bin/env bash
# base-server.sh — manage a base-SHA dev server for visual verification.
#
# Creates a read-only git worktree at /tmp/base-worktree-<sha>, symlinks
# worktree_links from the main checkout (per repo-policy.json), starts the
# project's dev server on the requested port, and polls until healthy.
#
# Usage:
#   base-server.sh up   --repo <path> --base-sha <sha> --port <port>
#   base-server.sh down --repo <path> --base-sha <sha> [--port <port>]
#
# up:
#   Prints "http://localhost:<port>" when the server is accepting requests.
#   Idempotent: if the worktree and server are already up on that port,
#   prints the URL and exits 0 without restarting.
#
# down:
#   Kills the server process (via the PID file) and removes the worktree.
#
# Exit codes:
#   0  success
#   1  error (message printed to stderr)
#   2  usage error

set -uo pipefail

# ── Constants ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_POLICY="$SCRIPT_DIR/../../resources/repo-policy.json"
WORKTREE_BASE="/tmp"
HEALTH_POLL_INTERVAL=2   # seconds between polls
HEALTH_TIMEOUT=60        # max seconds to wait for the server

# ── Usage ──────────────────────────────────────────────────────────────────

usage() {
  sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
  exit 2
}

die() { echo "base-server: error: $*" >&2; exit 1; }

# ── Arg parsing ────────────────────────────────────────────────────────────

CMD="${1:-}"
[[ -z "$CMD" ]] && usage
shift

REPO=""
BASE_SHA=""
PORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)     REPO="$2";     shift 2 ;;
    --base-sha) BASE_SHA="$2"; shift 2 ;;
    --port)     PORT="$2";     shift 2 ;;
    -h|--help)  usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -z "$REPO"     ]] && die "--repo is required"
[[ -z "$BASE_SHA" ]] && die "--base-sha is required"

REPO="$(cd "$REPO" && pwd -P)"   # Resolve to absolute path
SHORT_SHA="${BASE_SHA:0:12}"
WORKTREE_DIR="$WORKTREE_BASE/base-worktree-$SHORT_SHA"

# ── Helpers ────────────────────────────────────────────────────────────────

# Read worktree_links for this repo from repo-policy.json.
# Returns a newline-separated list of relative paths, or empty.
get_worktree_links() {
  if [[ ! -f "$REPO_POLICY" ]]; then
    return
  fi
  local remote_url
  remote_url=$(git -C "$REPO" remote get-url origin 2>/dev/null || echo "")
  if [[ -z "$remote_url" ]]; then
    return
  fi
  # Derive "Owner/Repo" key from HTTPS or SSH remote URL
  local repo_key
  repo_key=$(echo "$remote_url" \
    | sed -E 's|.*[:/]([^/]+/[^/]+?)(\.git)?$|\1|')

  python3 - "$REPO_POLICY" "$repo_key" <<'PY'
import json, sys
policy_file, key = sys.argv[1], sys.argv[2]
try:
    policy = json.loads(open(policy_file).read())
except Exception as e:
    sys.exit(0)
entry = policy.get(key, {})
links = entry.get("worktree_links", [])
for l in links:
    print(l)
PY
}

# Detect the dev server start command for the worktree directory.
# Sets DETECTED_CMD and DETECTED_PORT_ARG in the caller's scope.
detect_start_command() {
  local dir="$1"
  local port="$2"
  local pkg="$dir/package.json"

  DETECTED_CMD=""
  DETECTED_PORT_ARG=""

  if [[ ! -f "$pkg" ]]; then
    DETECTED_CMD="npm start"
    return
  fi

  # Detect framework from config files (most reliable)
  if [[ -f "$dir/vite.config.ts" || -f "$dir/vite.config.js" || -f "$dir/vite.config.mjs" ]]; then
    DETECTED_CMD="npm run dev"
    DETECTED_PORT_ARG="-- --port $port"
    return
  fi

  if [[ -f "$dir/next.config.js" || -f "$dir/next.config.ts" || -f "$dir/next.config.mjs" ]]; then
    DETECTED_CMD="npm run dev"
    DETECTED_PORT_ARG="-- -p $port"
    return
  fi

  # Detect framework from package.json dependencies
  local has_vite has_next
  has_vite=$(python3 -c "
import json, sys
try:
    d = json.load(open('$pkg'))
    deps = {**d.get('dependencies',{}), **d.get('devDependencies',{})}
    print('yes' if 'vite' in deps else 'no')
except:
    print('no')
" 2>/dev/null)

  has_next=$(python3 -c "
import json, sys
try:
    d = json.load(open('$pkg'))
    deps = {**d.get('dependencies',{}), **d.get('devDependencies',{})}
    print('yes' if 'next' in deps else 'no')
except:
    print('no')
" 2>/dev/null)

  if [[ "$has_vite" == "yes" ]]; then
    DETECTED_CMD="npm run dev"
    DETECTED_PORT_ARG="-- --port $port"
  elif [[ "$has_next" == "yes" ]]; then
    DETECTED_CMD="npm run dev"
    DETECTED_PORT_ARG="-- -p $port"
  else
    # Generic fallback: just set PORT env var
    DETECTED_CMD="npm run dev"
    DETECTED_PORT_ARG=""
  fi
}

# Poll base_url until HTTP 200 or timeout.
wait_for_server() {
  local url="$1"
  local elapsed=0
  while [[ $elapsed -lt $HEALTH_TIMEOUT ]]; do
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [[ "$code" == "200" || "$code" == "301" || "$code" == "302" ]]; then
      return 0
    fi
    sleep "$HEALTH_POLL_INTERVAL"
    elapsed=$((elapsed + HEALTH_POLL_INTERVAL))
  done
  return 1
}

# ── Command: up ────────────────────────────────────────────────────────────

cmd_up() {
  [[ -z "$PORT" ]] && die "--port is required for 'up'"

  local pid_file="$WORKTREE_DIR.pid"
  local url="http://localhost:$PORT"

  # If server is already healthy, report and exit
  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid=$(cat "$pid_file" 2>/dev/null || echo "")
    if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
      local code
      code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
      if [[ "$code" == "200" || "$code" == "301" || "$code" == "302" ]]; then
        echo "$url"
        return 0
      fi
    fi
  fi

  # Create worktree if it doesn't exist
  if [[ ! -d "$WORKTREE_DIR" ]]; then
    echo "base-server: creating worktree at $WORKTREE_DIR for $SHORT_SHA" >&2
    git -C "$REPO" worktree add "$WORKTREE_DIR" "$BASE_SHA" --detach 2>&1 >&2 \
      || die "git worktree add failed"
  else
    echo "base-server: reusing existing worktree at $WORKTREE_DIR" >&2
  fi

  # Symlink worktree_links
  while IFS= read -r link_path; do
    [[ -z "$link_path" ]] && continue
    local src="$REPO/$link_path"
    local dst="$WORKTREE_DIR/$link_path"
    if [[ -e "$src" || -L "$src" ]]; then
      local dst_dir
      dst_dir=$(dirname "$dst")
      mkdir -p "$dst_dir"
      if [[ ! -e "$dst" && ! -L "$dst" ]]; then
        ln -sf "$src" "$dst"
        echo "base-server: symlinked $link_path" >&2
      fi
    else
      echo "base-server: warning: worktree_link source not found: $src" >&2
    fi
  done < <(get_worktree_links)

  # Install deps in the worktree
  local pkg_lock=""
  for lock in pnpm-lock.yaml yarn.lock package-lock.json bun.lock bun.lockb; do
    if [[ -f "$WORKTREE_DIR/$lock" ]]; then
      pkg_lock="$lock"
      break
    fi
  done

  if [[ -n "$pkg_lock" && ! -d "$WORKTREE_DIR/node_modules" ]]; then
    echo "base-server: installing deps in worktree..." >&2
    (
      cd "$WORKTREE_DIR"
      case "$pkg_lock" in
        pnpm-lock.yaml) pnpm install --frozen-lockfile --prefer-offline 2>&1 >&2 ;;
        yarn.lock)      yarn install --frozen-lockfile 2>&1 >&2 ;;
        bun.lock|bun.lockb) bun install --frozen-lockfile 2>&1 >&2 ;;
        *) npm ci 2>&1 >&2 ;;
      esac
    ) || die "dep install failed in worktree"
  fi

  # Detect start command
  detect_start_command "$WORKTREE_DIR" "$PORT"

  echo "base-server: starting: PORT=$PORT $DETECTED_CMD $DETECTED_PORT_ARG" >&2

  # Start the server in the background
  local log_file="$WORKTREE_DIR.log"
  (
    cd "$WORKTREE_DIR"
    export PORT="$PORT"
    # shellcheck disable=SC2086
    exec $DETECTED_CMD $DETECTED_PORT_ARG >"$log_file" 2>&1
  ) &
  local server_pid=$!
  echo "$server_pid" > "$pid_file"

  echo "base-server: server PID $server_pid, polling $url ..." >&2

  # Poll until healthy
  if ! wait_for_server "$url"; then
    echo "base-server: server did not become healthy within ${HEALTH_TIMEOUT}s" >&2
    echo "base-server: last log lines:" >&2
    tail -20 "$log_file" >&2 || true
    kill "$server_pid" 2>/dev/null || true
    rm -f "$pid_file"
    exit 1
  fi

  echo "base-server: server is up at $url" >&2
  echo "$url"
}

# ── Command: down ──────────────────────────────────────────────────────────

cmd_down() {
  local pid_file="$WORKTREE_DIR.pid"

  # Kill the server process
  if [[ -f "$pid_file" ]]; then
    local pid
    pid=$(cat "$pid_file" 2>/dev/null || echo "")
    if [[ -n "$pid" ]]; then
      echo "base-server: killing PID $pid" >&2
      kill "$pid" 2>/dev/null || true
      # Give it a moment to exit
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  else
    echo "base-server: no PID file found at $pid_file (server may not be running)" >&2
  fi

  # Remove the worktree
  if [[ -d "$WORKTREE_DIR" ]]; then
    echo "base-server: removing worktree at $WORKTREE_DIR" >&2
    git -C "$REPO" worktree remove "$WORKTREE_DIR" --force 2>&1 >&2 \
      || {
        echo "base-server: git worktree remove failed, trying manual rm" >&2
        rm -rf "$WORKTREE_DIR"
        git -C "$REPO" worktree prune 2>/dev/null || true
      }
  else
    echo "base-server: no worktree found at $WORKTREE_DIR" >&2
  fi

  # Clean up log file
  rm -f "$WORKTREE_DIR.log"

  echo "base-server: down" >&2
}

# ── Dispatch ───────────────────────────────────────────────────────────────

case "$CMD" in
  up)   cmd_up ;;
  down) cmd_down ;;
  *)    die "unknown command: $CMD (expected: up | down)" ;;
esac
