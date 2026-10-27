#!/bin/bash
# Test hook — fires on Stop event. Writes to /tmp/cc-hook-test.log to confirm hooks load from dotfiles.
# TEST: After any Claude response ends, run: cat /tmp/cc-hook-test.log
#       Each entry should show timestamp + CLAUDE_CONFIG_DIR (~/.cch or ~/.cco)
LOG="/tmp/cc-hook-test.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Hook fired | config=${CLAUDE_CONFIG_DIR:-~/.claude}" >> "$LOG"
