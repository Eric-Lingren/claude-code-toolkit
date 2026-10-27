# cc-usage-nudge.sh — print the weekly Claude Code usage digest once per new
# report. Source this from an interactive shell (see ~/.dotfiles/.zshrc).
#
# Fires once when digest-latest.txt is newer than the .digest-seen marker, then
# marks it seen so it never nags every shell. The weekly launchd report writes a
# fresh digest, so you get exactly one nudge per week (even an all-green week —
# keeps adherence top of mind). Re-read anytime with: ccusage
#
# Output dir override: CC_USAGE_REPORT_DIR (default ~/.cache/cc-usage-reports)

_cc_usage_nudge() {
  local dir="${CC_USAGE_REPORT_DIR:-$HOME/.cache/cc-usage-reports}"
  local digest="$dir/digest-latest.txt"
  local seen="$dir/.digest-seen"
  [ -f "$digest" ] || return 0
  if [ ! -e "$seen" ] || [ "$digest" -nt "$seen" ]; then
    printf '\n'
    cat "$digest"
    printf '\n'
    touch "$seen"
  fi
}

# show once on interactive shell startup
case $- in
  *i*) _cc_usage_nudge ;;
esac

# reprint on demand
alias ccusage='cat "${CC_USAGE_REPORT_DIR:-$HOME/.cache/cc-usage-reports}/digest-latest.txt"'
