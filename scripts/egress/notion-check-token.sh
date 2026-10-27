#!/usr/bin/env bash
set -euo pipefail

SECRETS_FILE="$HOME/.dotfiles/local/secrets.env"
if [ -f "$SECRETS_FILE" ]; then
  # shellcheck source=/dev/null
  source "$SECRETS_FILE"
fi

if [ -z "${NOTION_PERSONAL_TOKEN:-}" ]; then
  echo "ERROR: NOTION_PERSONAL_TOKEN is not set." >&2
  echo "" >&2
  echo "Steps:" >&2
  echo "  1. Go to notion.so/profile/integrations" >&2
  echo "  2. Create a new Internal integration, select your personal workspace" >&2
  echo "  3. Copy the secret (starts with ntn_)" >&2
  echo "  4. Copy ~/.dotfiles/local/secrets.env.template to ~/.dotfiles/local/secrets.env" >&2
  echo "  5. Uncomment and fill in: export NOTION_PERSONAL_TOKEN=ntn_..." >&2
  echo "  6. Open your personal Notion DB, click ... > Connections > add the integration" >&2
  exit 1
fi

exit 0
