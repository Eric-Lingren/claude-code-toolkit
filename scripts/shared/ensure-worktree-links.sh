#!/bin/bash
# ensure-worktree-links.sh — symlink gitignored local config into a worktree.
#
# Usage: ensure-worktree-links.sh [path]
#        ensure-worktree-links.sh --install-hook [repo]
#
#   path            worktree to fix (default: git toplevel of $PWD)
#   --install-hook  write a local .git/hooks/post-checkout in repo (default:
#                   main worktree of $PWD) that runs this script whenever
#                   `git worktree add` creates a worktree. Local only, never
#                   committed. Refuses to overwrite a hook it did not write.
#
# Why: gitignored files (app/.env, local_settings.py) don't exist in a new
# worktree. One checkout switching branches keeps a single shared copy, so a
# symlink to the main worktree reproduces that. A copy would drift the moment
# a credential is rotated.
#
# What it does:
#   - Reads "worktree_links" for the repo from repo-policy.json (via gx-lib).
#   - For each path: symlinks <main>/<path> into the worktree when missing.
#   - Leaves existing links and real files alone (warns on real files).
#   - No-op in the main worktree or for repos with no worktree_links.
#
# Callers: wt (~/.dotfiles/.scripts/worktree), the post-checkout hook this
# script installs, and the worktree-deps.sh EnterWorktree hook.
#
# Exit codes: 0 done (including no-op), 2 usage error or hook conflict.
set -uo pipefail

GX_LIB="$HOME/.dotfiles/.scripts/gx-lib.sh"
HOOK_MARKER="# managed-by: ensure-worktree-links.sh"

INSTALL_HOOK=false
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --install-hook) INSTALL_HOOK=true ;;
    -h|--help) sed -n 2,27p "$0"; exit 0 ;;
    -*) echo "ensure-worktree-links: unknown flag '$arg'" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

# Hook context exports GIT_DIR and friends; resolve from the path instead.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

TARGET="${TARGET:-$PWD}"
TOPLEVEL=$(git -C "$TARGET" rev-parse --show-toplevel 2>/dev/null) || {
  echo "ensure-worktree-links: '$TARGET' is not inside a git checkout" >&2
  exit 2
}

COMMON=$(git -C "$TOPLEVEL" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
MAIN_ROOT="${COMMON%/.git}"

# ─── Hook install ─────────────────────────────────────────────────────────────

if [[ "$INSTALL_HOOK" == true ]]; then
  hook="$COMMON/hooks/post-checkout"
  if [[ -e "$hook" ]] && ! grep -qF "$HOOK_MARKER" "$hook"; then
    echo "ensure-worktree-links: $hook exists and is not ours; leaving it alone" >&2
    exit 2
  fi
  mkdir -p "$COMMON/hooks"
  cat > "$hook" <<EOF
#!/bin/bash
$HOOK_MARKER
# Links gitignored local config into new worktrees. A null previous ref (\$1)
# means a fresh checkout, which is what \`git worktree add\` produces.
[[ "\$1" =~ ^0+\$ ]] || exit 0
script="\$HOME/.dotfiles/claude-code-shared/scripts/shared/ensure-worktree-links.sh"
[[ -x "\$script" ]] && "\$script" "\$PWD"
exit 0
EOF
  chmod +x "$hook"
  echo "installed post-checkout hook: $hook" >&2
  exit 0
fi

# ─── Link ─────────────────────────────────────────────────────────────────────

[[ "$TOPLEVEL" != "$MAIN_ROOT" ]] || exit 0

if [[ ! -f "$GX_LIB" ]]; then
  echo "ensure-worktree-links: $GX_LIB not found; skipping" >&2
  exit 0
fi
# shellcheck source=/dev/null
source "$GX_LIB"
cd "$TOPLEVEL" || exit 0
gx_load_policy 2>/dev/null || exit 0

while IFS= read -r rel; do
  [[ -n "$rel" ]] || continue
  src="$MAIN_ROOT/$rel"
  dest="$TOPLEVEL/$rel"

  if [[ ! -e "$src" ]]; then
    echo "WARN: local config '$rel' not found in $MAIN_ROOT; skipping" >&2
    continue
  fi
  [[ -L "$dest" ]] && continue
  if [[ -e "$dest" ]]; then
    echo "WARN: '$rel' already exists in worktree as a real file; leaving it alone" >&2
    continue
  fi

  mkdir -p "$(dirname "$dest")"
  if ln -s "$src" "$dest" 2>/dev/null; then
    echo "linked local config: $rel -> $src" >&2
  else
    echo "WARN: could not link '$rel' into worktree" >&2
  fi
done < <(gx_worktree_links)

exit 0
