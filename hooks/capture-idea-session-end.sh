#!/bin/bash
# SessionEnd hook: spawn the capture-idea agent in the background for plain
# sessions. Reads the hook JSON on stdin. Never blocks, always exits 0.
# Injectable spawn: CAPTURE_IDEA_SPAWN_CMD (run via bash -c) replaces the real
# `claude -p --agent capture-idea`. Tests set it to a stub and set
# CAPTURE_IDEA_SYNC=1 to wait for it. The command receives env:
# CAPTURE_IDEA_TRANSCRIPT, CAPTURE_IDEA_SESSION, CAPTURE_IDEA_CWD,
# CAPTURE_IDEA_PROMPT. CAPTURE_IDEA_DISABLE=1 turns the hook off.
# The agent runs with CAPTURE_IDEA_ACTIVE=1 so its own SessionEnd does not
# spawn another capture-idea run.
[ -n "${CAPTURE_IDEA_DISABLE:-}" ] && exit 0
[ -n "${CAPTURE_IDEA_ACTIVE:-}" ] && exit 0

INPUT=$(cat)
field() { echo "$INPUT" | jq -r --arg k "$1" '.[$k] // ""' 2>/dev/null; }
export CAPTURE_IDEA_TRANSCRIPT="$(field transcript_path)"
export CAPTURE_IDEA_SESSION="$(field session_id)"
export CAPTURE_IDEA_CWD="$(field cwd)"
[ -n "$CAPTURE_IDEA_TRANSCRIPT" ] || exit 0
export CAPTURE_IDEA_PROMPT="Run capture-idea. transcript_path: $CAPTURE_IDEA_TRANSCRIPT; session: $CAPTURE_IDEA_SESSION; cwd: ${CAPTURE_IDEA_CWD:-unknown}"
export CAPTURE_IDEA_ACTIVE=1

CMD="${CAPTURE_IDEA_SPAWN_CMD:-claude -p --agent capture-idea --permission-mode acceptEdits \"\$CAPTURE_IDEA_PROMPT\"}"
LOG="${CAPTURE_IDEA_LOG:-/dev/null}"
cd "${CAPTURE_IDEA_CWD:-.}" 2>/dev/null || true

if [ -n "${CAPTURE_IDEA_SYNC:-}" ]; then
  bash -c "$CMD" >>"$LOG" 2>&1 </dev/null
else
  nohup bash -c "$CMD" >>"$LOG" 2>&1 </dev/null &
  disown 2>/dev/null
fi
exit 0
