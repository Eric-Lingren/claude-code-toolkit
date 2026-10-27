#!/usr/bin/env bash
# Installs Claude Code plugins for all configured instances.
# Run this on a new machine after setting up dotfiles.

set -euo pipefail

INSTANCES=("$HOME/.cco" "$HOME/.cch")
PLUGINS=("caveman@caveman")
MARKETPLACES=(
  "JuliusBrussee/caveman"
  "anthropics/claude-plugins-official"
)

for config_dir in "${INSTANCES[@]}"; do
  if [[ ! -d "$config_dir" ]]; then
    echo "Skipping $config_dir (not found)"
    continue
  fi

  echo "Configuring $config_dir..."

  for repo in "${MARKETPLACES[@]}"; do
    CLAUDE_CONFIG_DIR="$config_dir" command claude plugins marketplace add "github:$repo" 2>/dev/null || true
  done

  for plugin in "${PLUGINS[@]}"; do
    CLAUDE_CONFIG_DIR="$config_dir" command claude plugins install "$plugin"
    echo "  Installed $plugin"
  done
done

echo "Done."
