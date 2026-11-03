#!/usr/bin/env bash
# test-install-plugins.sh — Tests for profile/install-plugins.sh
# What: runs the script against a stub claude binary; checks the owner/repo marketplace form, both accounts, continuing past a failure, and the exit code
# When: run after editing install-plugins.sh; run-all-tests.sh runs it too

set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/profile/install-plugins.sh"

PASS=0
FAIL=0
check() { if eval "$2"; then echo "PASS: $1"; PASS=$((PASS + 1)); else echo "FAIL: $1"; FAIL=$((FAIL + 1)); fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.cco" "$TMP/home/.cch"

# stub claude: logs "<config dir> <args>", fails installs when FAIL_INSTALL=1
cat > "$TMP/claude" <<EOF
#!/bin/sh
echo "\$(basename "\$CLAUDE_CONFIG_DIR") \$*" >> "$TMP/claude.log"
case "\$*" in
  "plugins install"*) [ "\${FAIL_INSTALL:-0}" = 1 ] && exit 1 ;;
esac
exit 0
EOF
chmod +x "$TMP/claude"

OUT="$(HOME="$TMP/home" CLAUDE_BIN="$TMP/claude" bash "$SCRIPT" 2>&1)"
RC=$?
check "exits 0 when every step works" '[[ $RC -eq 0 ]]'
check "adds marketplaces as owner/repo" 'grep -qx ".cch plugins marketplace add JuliusBrussee/caveman" "$TMP/claude.log"'
check "never uses the rejected github: form" '! grep -q "github:" "$TMP/claude.log"'
check "installs plugins in both accounts" 'grep -qx ".cco plugins install caveman@caveman" "$TMP/claude.log" && grep -qx ".cch plugins install caveman@caveman" "$TMP/claude.log"'

: > "$TMP/claude.log"
OUT="$(HOME="$TMP/home" CLAUDE_BIN="$TMP/claude" FAIL_INSTALL=1 bash "$SCRIPT" 2>&1)"
RC=$?
check "exits 1 when a plugin fails" '[[ $RC -eq 1 ]]'
check "keeps going to the next account after a failure" 'grep -qx ".cch plugins install caveman@caveman" "$TMP/claude.log"'
check "names the failed plugin" 'grep -q "Failed to install caveman@caveman" <<<"$OUT"'

OUT="$(HOME="$TMP/home" CLAUDE_BIN="$TMP/missing" bash "$SCRIPT" 2>&1)"
RC=$?
check "fails clearly when claude is missing" '[[ $RC -eq 1 ]] && grep -q "Claude Code not found" <<<"$OUT"'

echo
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
