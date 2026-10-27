#!/usr/bin/env bash
# Usage: next-task-id.sh [tasks-dir]
# Outputs the next globally unique task ID (e.g. T-0023).
# Scans all JSON files in tasks-dir for the highest T-XXXX id.

TASKS_DIR="${1:-docs/tasks}"

if [ ! -d "$TASKS_DIR" ]; then
  echo "T-0001"
  exit 0
fi

JSON_COUNT=$(find "$TASKS_DIR" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
if [ "$JSON_COUNT" -eq 0 ]; then
  echo "T-0001"
  exit 0
fi

MAX=$(find "$TASKS_DIR" -name "*.json" -exec grep -oh '"T-[0-9]\{4\}"' {} \; 2>/dev/null \
  | grep -o '[0-9]\{4\}' \
  | sort -n \
  | tail -1)

if [ -z "$MAX" ]; then
  echo "T-0001"
else
  NEXT=$(printf "%04d" $((10#$MAX + 1)))
  echo "T-$NEXT"
fi
