#!/usr/bin/env bash
# Usage: clean-scaffolding.sh preview|delete|delete-files|archive-files
#
# preview:       enumerate disposable doc scaffolding, print full grouped file
#                list and a total. Exit 0 if files found, exit 2 if nothing.
# delete:        re-enumerate the same set, remove each explicit path.
# delete-files:  delete a specific list of files (used by skill for browser-checks).
# archive-files: bundle a chain of files into docs/archive/<root>-<slug>.json,
#                embed full content verbatim, then remove originals.
#
# Archive set (bundled then removed):  docs/seeds/, docs/prd/, docs/tasks/, docs/handoffs/
# Delete set (removed, not archived):  docs/browser-checks/, docs/tasks/.logs/, docs/offload-output/
# Untouched:                           docs/adr/, docs/prototype/, docs/standups/
#
# Never runs git. Never edits .gitignore. Deletion uses explicit rm per path:
# no rm -f, no bare rm *, no find -delete.
#
# Written for bash 3.2 (stock macOS): no mapfile, no associative arrays.
set -euo pipefail

ARCHIVE_DIRS="docs/seeds docs/prd docs/tasks docs/handoffs"
DELETE_DIRS="docs/browser-checks docs/tasks/.logs docs/offload-output"
DIRS="$ARCHIVE_DIRS $DELETE_DIRS"

list_dir() {
  case "$1" in
    docs/seeds)          find docs/seeds          -maxdepth 1 -type f              2>/dev/null ;;
    docs/prd)            find docs/prd            -maxdepth 1 -type f              2>/dev/null ;;
    docs/tasks)          find docs/tasks          -maxdepth 1 -type f              2>/dev/null ;;
    docs/handoffs)       find docs/handoffs       -maxdepth 1 -name '*.md' -type f 2>/dev/null ;;
    docs/browser-checks) find docs/browser-checks -maxdepth 1 -type f             2>/dev/null ;;
    docs/offload-output) find docs/offload-output -maxdepth 1 -type f             2>/dev/null ;;
    docs/tasks/.logs)    find docs/tasks/.logs    -type f                        2>/dev/null ;;
  esac
}

enumerate() {
  for d in $DIRS; do list_dir "$d"; done
}

preview() {
  if [ -z "$(enumerate)" ]; then
    echo "Nothing to clean."
    return 2
  fi
  echo "About to archive/delete:"
  echo
  total=0
  for d in $DIRS; do
    files=$(list_dir "$d")
    [ -z "$files" ] && continue
    echo "  ${d}/"
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      echo "    ${f##*/}"
      total=$((total + 1))
    done <<EOF
$files
EOF
    echo
  done
  printf "Total: %d files\n" "$total"
  return 0
}

do_delete() {
  total=0
  for d in $DIRS; do
    files=$(list_dir "$d")
    [ -z "$files" ] && continue
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if rm "$f" 2>/dev/null; then
        echo "removed $f"
        total=$((total + 1))
      else
        echo "skipped (could not remove) $f" >&2
      fi
    done <<EOF
$files
EOF
  done
  find docs/tasks/.logs -mindepth 1 -type d -empty -delete 2>/dev/null || true
  rmdir docs/tasks/.logs docs/seeds docs/prd docs/tasks docs/handoffs docs/browser-checks docs/offload-output docs 2>/dev/null || true
  printf "Deleted %d files.\n" "$total"
}

do_delete_files() {
  # Delete a specific list of files (browser-checks, or legacy path).
  total=0
  for f in "$@"; do
    if [ -f "$f" ]; then
      if rm "$f" 2>/dev/null; then
        echo "removed $f"
        total=$((total + 1))
      else
        echo "skipped (could not remove) $f" >&2
      fi
    else
      echo "skipped (not found) $f" >&2
    fi
  done
  find docs/tasks/.logs -mindepth 1 -type d -empty -delete 2>/dev/null || true
  rmdir docs/tasks/.logs docs/seeds docs/prd docs/tasks docs/handoffs docs/browser-checks docs/offload-output docs 2>/dev/null || true
  printf "Deleted %d files.\n" "$total"
}

do_archive_files() {
  # Bundle chain files into docs/archive/<root-basename>, then remove originals.
  # Chain root = file with the lexicographically earliest basename (YYYYMMDD-HHMM prefix sorts chronologically).
  # File list is passed as positional arguments.
  if [ $# -eq 0 ]; then
    echo "Usage: clean-scaffolding.sh archive-files <file...>" >&2
    exit 1
  fi

  mkdir -p docs/archive

  python3 - "$@" <<'PYEOF'
import json, sys, os, re
from datetime import datetime, timezone

files = sys.argv[1:]

# Sort by basename to find chain root (earliest timestamp prefix)
def sort_key(path):
    return os.path.basename(path)

files_sorted = sorted(files, key=sort_key)
root_path = files_sorted[0]
root_basename = os.path.basename(root_path)

# Derive slug from root basename: strip YYYYMMDD-HHMM- prefix and extension
slug_match = re.match(r'^\d{8}-\d{4}-(.+?)(\.[^.]+)?$', root_basename)
slug = slug_match.group(1) if slug_match else root_basename

# Read title from root JSON (if JSON; fallback to slug)
title = slug
if root_path.endswith('.json'):
    try:
        with open(root_path) as f:
            root_data = json.load(f)
        title = root_data.get('title', slug)
    except (json.JSONDecodeError, IOError):
        pass

# Determine artifact type from path
def artifact_type(path):
    d = os.path.dirname(path)
    if 'seeds' in d:    return 'seed'
    if 'tasks' in d:    return 'task'
    if 'handoffs' in d: return 'handoff'
    if 'prd' in d:      return 'prd'
    return 'unknown'

# Build artifacts list with verbatim content
artifacts = []
condensed_from = []
for path in files_sorted:
    bname = os.path.basename(path)
    condensed_from.append(bname)
    atype = artifact_type(path)
    try:
        if path.endswith('.json'):
            with open(path) as f:
                content = json.load(f)
        else:
            with open(path) as f:
                content = f.read()
    except IOError:
        content = None
    artifacts.append({"filename": bname, "type": atype, "content": content})

bundle = {
    "kind": "condensed",
    "producer": "clean-scaffolding",
    "slug": slug,
    "title": title,
    "condensed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "condensed_from": condensed_from,
    "artifacts": artifacts,
}

# Extract timestamp prefix from root basename for archive filename
ts_match = re.match(r'^(\d{8}-\d{4})-', root_basename)
ts_prefix = ts_match.group(1) if ts_match else datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
archive_path = f"docs/archive/{ts_prefix}-{slug}.json"

with open(archive_path, 'w') as f:
    json.dump(bundle, f, indent=2)
print(f"archived {len(files)} file(s) to {archive_path}")

# Remove originals
removed = 0
for path in files:
    try:
        os.remove(path)
        print(f"removed {path}")
        removed += 1
    except OSError as e:
        print(f"skipped (could not remove) {path}: {e}", file=sys.stderr)

# Prune now-empty scaffold dirs
for d in ['docs/seeds', 'docs/prd', 'docs/tasks', 'docs/handoffs', 'docs']:
    try:
        os.rmdir(d)
    except OSError:
        pass

print(f"Archived and removed {removed} file(s).")
PYEOF
}

case "${1:-}" in
  preview)
    if preview; then exit 0; else exit 2; fi
    ;;
  delete)
    do_delete
    ;;
  delete-files)
    shift
    if [ $# -eq 0 ]; then
      echo "Usage: clean-scaffolding.sh delete-files <file...>" >&2
      exit 1
    fi
    do_delete_files "$@"
    ;;
  archive-files)
    shift
    do_archive_files "$@"
    ;;
  *)
    echo "Usage: clean-scaffolding.sh preview|delete|delete-files|archive-files <file...>" >&2
    exit 1
    ;;
esac
