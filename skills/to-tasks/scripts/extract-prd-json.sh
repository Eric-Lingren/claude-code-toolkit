#!/usr/bin/env bash
# Extract and validate PRD JSON from .html, .md, or .json files.
# Usage: extract-prd-json.sh <path-to-prd>
#
# .html — extracts <script id="prd-data"> block, validates JSON parses
# .md   — cats as-is, validates JSON parses
# .json — passthrough, validates against prd-data-schema.json (strict)

set -euo pipefail

SCHEMA_PATH="$(dirname "$(realpath "$0")")/../../to-spec/resources/prd-data-schema.json"

file="${1:?Usage: extract-prd-json.sh <prd-file>}"

if [[ ! -f "$file" ]]; then
  echo "Error: file not found: $file" >&2
  exit 1
fi

validate_json_parseable() {
  local input_file="$1"
  if ! python3 -c "import sys,json; json.load(open(sys.argv[1]))" "$input_file" 2>/dev/null; then
    echo "Error: not valid JSON: $input_file" >&2
    exit 1
  fi
}

validate_json_schema() {
  local input_file="$1"
  python3 - "$input_file" "$SCHEMA_PATH" << 'PYEOF'
import sys, json

data_path = sys.argv[1]
schema_path = sys.argv[2]

with open(data_path) as f:
    data = json.load(f)
with open(schema_path) as f:
    schema = json.load(f)

required = schema.get("required", [])
missing = [k for k in required if k not in data]
if missing:
    print(f"Error: seed JSON missing required fields: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

case "$file" in
  *.html)
    extracted=$(sed -n '/<script type="application\/json" id="prd-data">/,/<\/script>/{
      /<script/d
      /<\/script>/d
      p
    }' "$file")
    if [[ -z "$extracted" ]]; then
      echo "Error: no prd-data JSON block found in HTML file: $file" >&2
      exit 1
    fi
    tmp=$(mktemp /tmp/prd-extract.XXXXXX.json)
    trap 'rm -f "$tmp"' EXIT
    echo "$extracted" > "$tmp"
    validate_json_parseable "$tmp"
    cat "$tmp"
    ;;
  *.md)
    tmp=$(mktemp /tmp/prd-extract.XXXXXX.json)
    trap 'rm -f "$tmp"' EXIT
    cat "$file" > "$tmp"
    validate_json_parseable "$tmp"
    cat "$tmp"
    ;;
  *.json)
    validate_json_parseable "$file"
    validate_json_schema "$file"
    cat "$file"
    ;;
  *)
    echo "Error: unsupported file type: $file (expected .html, .md, or .json)" >&2
    exit 1
    ;;
esac
