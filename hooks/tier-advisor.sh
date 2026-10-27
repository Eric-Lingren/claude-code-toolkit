#!/bin/bash
# UserPromptSubmit hook: advisory model-tier nudge (both directions).
#
# Session default is Sonnet/xhigh (T3). This hook suggests:
#   - UPGRADE to Opus (T4) when a prompt smells like deep reasoning.
#   - DOWNGRADE to Haiku (T1) when a prompt is a pure cheap lookup.
#
# Hooks CANNOT set the model for the current turn (off-by-one timing), so this
# never switches models. It emits a one-line advisory the assistant can act on.
#
# Conservative on purpose. Tune the patterns below. Disable entirely with:
#   export CLAUDE_TIER_ADVISOR=0
#
# Exits 0 always (advisory, never blocks). Stdout becomes context for the turn.

[ "${CLAUDE_TIER_ADVISOR:-1}" = "0" ] && exit 0

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

# Skill invocations: look up expected tier and always nudge.
# Skills do NOT auto-switch models via frontmatter — the hook must nudge.
#
# <command-name> is injected AFTER hooks fire, so detect raw /skill-name instead.
# Keep <command-name> path as fallback for any future harness that pre-expands skills.
SKILL_NAME=""
if echo "$PROMPT" | grep -q "<command-name>"; then
  SKILL_NAME=$(echo "$PROMPT" | sed -n 's/.*<command-name>\/*\([^<]*\)<\/command-name>.*/\1/p' | head -1)
elif echo "$PROMPT" | grep -qE '(^|[[:space:]])/[a-z][a-z0-9-]+([[:space:]]|$)'; then
  SKILL_NAME=$(echo "$PROMPT" | grep -oE '(^|[[:space:]])/[a-z][a-z0-9-]+' | tail -1 | sed 's/^[[:space:]]*//' | sed 's/^\///')
fi
TIERS_FILE="$HOME/.dotfiles/claude-code-shared/resources/model-tiers.json"
if [ -n "$SKILL_NAME" ] && [ -f "$TIERS_FILE" ]; then
  TIER=$(jq -r --arg s "$SKILL_NAME" '.skills[$s] // ""' "$TIERS_FILE" 2>/dev/null)
  if [ -n "$TIER" ]; then
    MODEL=$(jq -r --arg t "$TIER" '.tiers[$t].model // "sonnet"' "$TIERS_FILE" 2>/dev/null)
    EFFORT=$(jq -r --arg t "$TIER" '.tiers[$t].effort // "xhigh"' "$TIERS_FILE" 2>/dev/null)

    # ---- Guard: don't nudge when already on the target model AND effort. ----
    # Effort: $CLAUDE_EFFORT is exported by the harness (tracks /effort).
    # Model: not in the env, so read the most recent assistant turn's model from
    # the transcript and normalise it (claude-opus-4-8 -> opus, etc.).
    CUR_EFFORT="${CLAUDE_EFFORT:-}"
    TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null)
    CUR_MODEL=""
    if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
      RAW_MODEL=$(grep '"type":"assistant"' "$TRANSCRIPT" 2>/dev/null | tail -1 | jq -r '.message.model // ""' 2>/dev/null)
      case "$RAW_MODEL" in
        *opus*)   CUR_MODEL="opus" ;;
        *sonnet*) CUR_MODEL="sonnet" ;;
        *haiku*)  CUR_MODEL="haiku" ;;
      esac
    fi
    # Suppress only on a confident full match. Empty CUR_MODEL (couldn't read) -> still nudge.
    if [ -n "$CUR_MODEL" ] && [ "$CUR_MODEL" = "$MODEL" ] && [ "$CUR_EFFORT" = "$EFFORT" ]; then
      exit 0
    fi

    # Reaching here means at least one dimension differs (full match exited above).
    # List ONLY the dimension(s) that are off, so we never tell the user to switch
    # to something they're already on. STEPS_PLAIN feeds the box, STEPS_MD the
    # markdown directive (commands wrapped in backticks).
    STEPS_PLAIN=""; STEPS_MD=""
    if [ "$CUR_MODEL" != "$MODEL" ]; then
      STEPS_PLAIN="/model $MODEL"; STEPS_MD="\`/model $MODEL\`"
    fi
    if [ "$CUR_EFFORT" != "$EFFORT" ]; then
      if [ -n "$STEPS_PLAIN" ]; then
        STEPS_PLAIN="$STEPS_PLAIN then /effort $EFFORT"
        STEPS_MD="$STEPS_MD then \`/effort $EFFORT\`"
      else
        STEPS_PLAIN="/effort $EFFORT"; STEPS_MD="\`/effort $EFFORT\`"
      fi
    fi

    printf '┌─────────────────────────────────────────────────────────────────────┐\n│  ⚡  TIER ADVISORY  │  /%s is %s\n│     → %s\n└─────────────────────────────────────────────────────────────────────┘\n' "$SKILL_NAME" "$TIER" "$STEPS_PLAIN"
    printf '[tier-advisor] /%s is %s. Run %s for best results. Advisory only — proceeding.\n' "$SKILL_NAME" "$TIER" "$STEPS_PLAIN"
    exit 0
  fi
fi

# ---- UPGRADE (T4 / Opus): strong deep-reasoning signals only ----
T4_PATTERN="architect|architecture|system design|design (the|a) (system|schema|data model|api)\
|trade-?off|migration strategy|rearchitect|redesign\
|race condition|deadlock|memory leak|concurrency|complex ?debug|hard bug|why is .* (failing|broken)\
|think (this|it) through|reason through|long.term (viability|strategy)|holistic|end.to.end design"

T4_MATCH=$(echo "$PROMPT" | grep -ioE "$T4_PATTERN" | head -1)
if [ -n "$T4_MATCH" ]; then
  echo "[tier-advisor] Deep-reasoning signal (\"$T4_MATCH\"). Default is Sonnet (T3). If this is genuinely T4 work, suggest the user run \`/model opus\` (xhigh). Do not mention this note otherwise."
  exit 0
fi

# ---- DOWNGRADE (T1 / Haiku): pure lookups, only when no action verb present ----
# Action verbs mean real work, not a lookup -> never downgrade.
ACTION="fix|chang|add|implement|refactor|build|writ|updat|edit|creat|delet|remov|renam|migrat|design|debug|review|test|optimiz|wire|install|configur|set up|generat|run "
echo "$PROMPT" | grep -ioqE "$ACTION" && exit 0

# Pure-lookup openers.
T1_PATTERN="^(where (is|are)|what (is|does|are)|how (do i|to)|find (the|all)|list (the|all)|show me|search for|grep |look (up|for)|which file|locate|status (of|on))\b"
T1_MATCH=$(echo "$PROMPT" | grep -ioE "$T1_PATTERN" | head -1)
if [ -n "$T1_MATCH" ]; then
  echo "[tier-advisor] Looks like a pure lookup (\"$T1_MATCH\"). To save cost, suggest the user run \`/model haiku\` (low) for this. Do not mention this note otherwise."
  exit 0
fi

exit 0
