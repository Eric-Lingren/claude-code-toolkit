#!/bin/bash
# ensure-worktree-deps.sh — give a checkout real, local JS dependencies.
#
# Usage: ensure-worktree-deps.sh [--check] [--quiet] [path]
#
#   path      checkout to fix (default: git toplevel of $PWD)
#   --check   report problems only; change nothing. Exit 1 if unhealthy.
#   --quiet   print only when something is repaired or installed
#
# Why: node_modules is gitignored, so a new worktree has none. Agents used to
# "fix" that by symlinking node_modules from the main checkout. That breaks
# twice over:
#   1. Turbopack (Next 16) refuses symlinks that leave the project root:
#      "Symlink [project]/clients/web/node_modules is invalid, it points out
#      of the filesystem root".
#   2. pnpm install run through the link rewrites the MAIN checkout's package
#      links to point into the worktree. Removing the worktree then breaks main.
#
# What it does:
#   - Finds every node_modules for the checkout (root + package dirs, depth 2).
#   - Removes node_modules that are themselves symlinks (the link only).
#   - Removes package links inside node_modules that resolve outside the
#     checkout, or that dangle (the link only; targets are never touched).
#   - Runs a frozen install when anything was repaired, node_modules is
#     missing, or the lockfile drifted from the installed state.
#
# Never symlinks, never deletes anything that is not a symlink.
#
# Exit codes: 0 healthy or repaired, 1 unhealthy (--check) or install failed,
#             2 usage error.
set -uo pipefail

CHECK=false
QUIET=false
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=true ;;
    --quiet) QUIET=true ;;
    -h|--help) sed -n 2,30p "$0"; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  TARGET=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "ensure-worktree-deps: not in a git repo and no path given" >&2
    exit 2
  }
fi
[[ -d "$TARGET" ]] || { echo "ensure-worktree-deps: no such dir: $TARGET" >&2; exit 2; }
ROOT=$(cd "$TARGET" && pwd -P)

say() { $QUIET || echo "ensure-worktree-deps: $*" >&2; }
loud() { echo "ensure-worktree-deps: $*" >&2; }

problems=0
repaired=0

# Print "<reason>\t<link>" for each package link in node_modules $1 that
# dangles or resolves outside $ROOT. Skips the .pnpm store itself.
scan_links() {
  python3 - "$1" "$ROOT" <<'PY'
import os, sys
nm, root = sys.argv[1], sys.argv[2]
def links():
    for name in os.listdir(nm):
        if name == ".pnpm":
            continue
        p = os.path.join(nm, name)
        if os.path.islink(p):
            yield p
        elif name.startswith("@") and os.path.isdir(p):
            for sub in os.listdir(p):
                q = os.path.join(p, sub)
                if os.path.islink(q):
                    yield q
for p in links():
    if not os.path.exists(p):
        print(f"dangling\t{p}")
        continue
    t = os.path.realpath(p)
    if t != root and not t.startswith(root + os.sep):
        print(f"points outside checkout -> {t}\t{p}")
PY
}

# Package dirs: root plus up to two levels down, skipping deps, git, and
# nested agent worktrees under .claude/.
package_dirs() {
  find "$ROOT" -maxdepth 3 \
    \( -name node_modules -o -name .git -o -name .claude -o -name .next \) -prune -o \
    -name package.json -type f -print 2>/dev/null | xargs -n1 dirname | sort -u
}

fix_link() {
  local link="$1" why="$2"
  problems=$((problems + 1))
  if $CHECK; then
    loud "BAD ($why): ${link#$ROOT/}"
  else
    rm -- "$link" && repaired=$((repaired + 1)) && loud "removed symlink ($why): ${link#$ROOT/}"
  fi
}

while IFS= read -r pkg; do
  nm="$pkg/node_modules"

  if [[ -L "$nm" ]]; then
    fix_link "$nm" "node_modules is a symlink -> $(readlink "$nm")"
    continue
  fi
  [[ -d "$nm" ]] || continue

  # Direct children and scoped (@scope/name) children, scanned in one pass.
  while IFS=$'\t' read -r why link; do
    [[ -n "$link" ]] && fix_link "$link" "$why"
  done < <(scan_links "$nm")
done < <(package_dirs)

# Install roots: dirs holding a lockfile (root first, then nested).
install_needed() {
  local dir="$1" nm="$1/node_modules"
  [[ -d "$nm" && ! -L "$nm" ]] || return 0
  if [[ -f "$dir/pnpm-lock.yaml" ]]; then
    cmp -s "$dir/pnpm-lock.yaml" "$nm/.pnpm/lock.yaml" || return 0
    # Every workspace importer should have its node_modules.
    local imp
    while IFS= read -r imp; do
      [[ "$imp" == "." ]] && continue
      [[ -f "$dir/$imp/package.json" ]] || continue
      [[ -d "$dir/$imp/node_modules" && ! -L "$dir/$imp/node_modules" ]] || return 0
    done < <(awk '/^importers:/{f=1;next} /^[^ ]/{f=0} f && /^  [^ ].*:$/{sub(/^  /,"");sub(/:$/,"");gsub(/\x27/,"");print}' "$dir/pnpm-lock.yaml")
    return 1
  fi
  local lock
  for lock in yarn.lock package-lock.json bun.lock bun.lockb; do
    [[ -f "$dir/$lock" ]] && [[ "$dir/$lock" -nt "$nm" ]] && return 0
  done
  return 1
}

run_install() {
  local dir="$1"
  local rel="${dir#$ROOT}"; rel="${rel#/}"; rel="${rel:-.}"
  loud "installing deps in $rel"
  local rc
  if [[ -f "$dir/pnpm-lock.yaml" ]]; then
    (cd "$dir" && pnpm install --frozen-lockfile --prefer-offline >&2); rc=$?
  elif [[ -f "$dir/yarn.lock" ]]; then
    (cd "$dir" && yarn install --frozen-lockfile >&2); rc=$?
  elif [[ -f "$dir/bun.lock" || -f "$dir/bun.lockb" ]]; then
    (cd "$dir" && bun install --frozen-lockfile >&2); rc=$?
  else
    (cd "$dir" && npm ci >&2); rc=$?
  fi
  [[ $rc -eq 0 ]] || loud "install FAILED in $rel (exit $rc)"
  return $rc
}

lock_dirs=$(find "$ROOT" -maxdepth 3 \
  \( -name node_modules -o -name .git -o -name .claude \) -prune -o \
  \( -name pnpm-lock.yaml -o -name yarn.lock -o -name package-lock.json -o -name bun.lock -o -name bun.lockb \) \
  -type f -print 2>/dev/null | xargs -n1 dirname 2>/dev/null | sort -u)

# A nested lockfile inside a pnpm workspace is covered by the root install.
if [[ -f "$ROOT/pnpm-lock.yaml" ]]; then
  lock_dirs="$ROOT"
fi

status=0
for dir in $lock_dirs; do
  if install_needed "$dir" || [[ $repaired -gt 0 ]]; then
    problems=$((problems + 1))
    if $CHECK; then
      loud "install needed in ${dir#$ROOT/}"
    else
      run_install "$dir" || status=1
    fi
  fi
done

if $CHECK; then
  [[ $problems -eq 0 ]] && { say "ok: $ROOT"; exit 0; }
  exit 1
fi

[[ $problems -eq 0 ]] && say "ok: $ROOT"
exit $status
