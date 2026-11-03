#!/usr/bin/env bash
# install-plugins.sh — Install Claude Code plugins for every configured account
# What: adds each marketplace and installs each plugin in ~/.cco and ~/.cch; safe to re-run (already-added and already-installed are no-ops); keeps going past a failure and exits 1 if any step failed
# When: install.sh runs it on a new machine; run it by hand after adding a plugin or marketplace here

set -uo pipefail

INSTANCES=("$HOME/.cco" "$HOME/.cch")
PLUGINS=("caveman@caveman")
# owner/repo form. The old github:owner/repo form is rejected by Claude Code.
MARKETPLACES=(
  "JuliusBrussee/caveman"
  "anthropics/claude-plugins-official"
)

CLAUDE="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
failed=0

if [[ ! -x "$CLAUDE" ]]; then
  echo "Claude Code not found at $CLAUDE. Install it first." >&2
  exit 1
fi

for config_dir in "${INSTANCES[@]}"; do
  if [[ ! -d "$config_dir" ]]; then
    echo "Skipping $config_dir (not found)"
    continue
  fi

  echo "Configuring $config_dir..."

  for repo in "${MARKETPLACES[@]}"; do
    if ! CLAUDE_CONFIG_DIR="$config_dir" "$CLAUDE" plugins marketplace add "$repo"; then
      echo "  Failed to add marketplace $repo" >&2
      failed=1
    fi
  done

  for plugin in "${PLUGINS[@]}"; do
    if ! CLAUDE_CONFIG_DIR="$config_dir" "$CLAUDE" plugins install "$plugin"; then
      echo "  Failed to install $plugin" >&2
      failed=1
    fi
  done
done

if [[ $failed -ne 0 ]]; then
  echo "Some plugin steps failed (see above)." >&2
  exit 1
fi
echo "Done."
