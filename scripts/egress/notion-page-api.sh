#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: notion-page-api.sh <db_id> <title> <description>" >&2
  exit 1
fi

DB_ID="$1"
TITLE="$2"
DESCRIPTION="$3"

if [ -z "$DB_ID" ] || [ -z "$TITLE" ] || [ -z "$DESCRIPTION" ]; then
  echo "error: db_id, title, and description must all be non-empty" >&2
  exit 1
fi

SECRETS_FILE="$HOME/.dotfiles/local/secrets.env"
if [ -f "$SECRETS_FILE" ]; then
  # shellcheck source=/dev/null
  source "$SECRETS_FILE"
fi

if [ -z "${NOTION_PERSONAL_TOKEN:-}" ]; then
  echo "error: NOTION_PERSONAL_TOKEN is not set. Run notion-check-token.sh for setup instructions." >&2
  exit 1
fi

RESPONSE=$(curl -s -X POST https://api.notion.com/v1/pages \
  -H "Authorization: Bearer $NOTION_PERSONAL_TOKEN" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d "$(printf '{"parent":{"database_id":"%s"},"properties":{"Name":{"title":[{"text":{"content":"%s"}}]}},"children":[{"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":"%s"}}]}}]}' "$DB_ID" "$TITLE" "$DESCRIPTION")")

PAGE_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null)

if [ -z "$PAGE_URL" ]; then
  echo "error: Notion API returned no url. Response: $RESPONSE" >&2
  exit 1
fi

echo "$PAGE_URL"
