#!/usr/bin/env bash
# Tests scripts/capture/resolve-labels.sh against the real repo-policy.json.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$HERE/../capture/resolve-labels.sh"
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1: expected '$2' got '$3'"; fail=1; fi
}
check "dotfiles idea" "idea" "$("$S" Eric-Lingren/dotfiles idea)"
check "dotfiles fix" "fix" "$("$S" Eric-Lingren/dotfiles fix)"
check "dotfiles bug->fix" "fix" "$("$S" Eric-Lingren/dotfiles bug)"
check "spawnedsapien idea" "feature-request,triage" "$("$S" Eric-Lingren/SpawnedSapien idea)"
check "spawnedsapien fix" "bug,triage" "$("$S" Eric-Lingren/SpawnedSapien fix)"
check "spawnedsapien bug" "bug,triage" "$("$S" Eric-Lingren/SpawnedSapien bug)"
err="$("$S" example-org/not-a-target idea 2>&1 >/dev/null)"; rc=$?
check "no entry exits nonzero" "1" "$rc"
case "$err" in *"not a capture target"*) echo "PASS message";; *) echo "FAIL message: $err"; fail=1;; esac
if jq -e . "$HERE/../../resources/repo-policy.json" >/dev/null; then echo "PASS policy parses"; else echo "FAIL policy parse"; fail=1; fi
exit $fail
