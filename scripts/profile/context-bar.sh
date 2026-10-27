#!/bin/bash

# Color theme: gray, orange, blue, teal, green, lavender, rose, gold, slate, cyan
# Preview colors with: bash scripts/color-preview.sh
COLOR="blue"

# Color codes
C_RESET='\033[0m'
C_GRAY='\033[38;5;245m'  # explicit gray for default text
C_BAR_EMPTY='\033[38;5;238m'
C_EFFORT='\033[38;5;172m'  # match caveman badge color
C_WARN='\033[38;5;196m'  # red for context window warning (>=80%)
C_NUDGE='\033[38;5;214m'  # amber nudge for context window (>=50%)
C_OFFICE='\033[1;38;5;39m'   # bold blue: office (CCO)
C_HOME='\033[1;38;5;39m'     # bold blue: home (CCH)
case "$COLOR" in
    orange)   C_ACCENT='\033[38;5;173m' ;;
    blue)     C_ACCENT='\033[38;5;74m' ;;
    teal)     C_ACCENT='\033[38;5;66m' ;;
    green)    C_ACCENT='\033[38;5;71m' ;;
    lavender) C_ACCENT='\033[38;5;139m' ;;
    rose)     C_ACCENT='\033[38;5;132m' ;;
    gold)     C_ACCENT='\033[38;5;136m' ;;
    slate)    C_ACCENT='\033[38;5;60m' ;;
    cyan)     C_ACCENT='\033[38;5;37m' ;;
    *)        C_ACCENT="$C_GRAY" ;;  # gray: all same color
esac

input=$(cat)

# Extract model, effort level, directory, and cwd
model=$(echo "$input" | jq -r '.model.display_name // .model.id // "?"')
effort=$(echo "$input" | jq -r '.effort.level // empty')
cwd=$(echo "$input" | jq -r '.cwd // empty')
dir=$(basename "$cwd" 2>/dev/null || echo "?")

# Profile badge: CCO (office) vs CCH (home), so it's clear which instance this
# terminal is when several run at once. Derived from the active config dir,
# falling back to the transcript path.
profile=""
profile_color=""
case "${CLAUDE_CONFIG_DIR:-}" in
    *.cco*) profile="CCO"; profile_color="$C_OFFICE" ;;
    *.cch*) profile="CCH"; profile_color="$C_HOME" ;;
esac
if [[ -z "$profile" ]]; then
    tp=$(echo "$input" | jq -r '.transcript_path // empty')
    case "$tp" in
        */.cco/*) profile="CCO"; profile_color="$C_OFFICE" ;;
        */.cch/*) profile="CCH"; profile_color="$C_HOME" ;;
    esac
fi

# Get git branch, uncommitted file count, and sync status
branch=""
git_status=""
if [[ -n "$cwd" && -d "$cwd" ]]; then
    branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
    if [[ -n "$branch" ]]; then
        # Count uncommitted files
        file_count=$(git -C "$cwd" --no-optional-locks status --porcelain -uall 2>/dev/null | wc -l | tr -d ' ')

        # Check sync status with upstream
        sync_status=""
        upstream=$(git -C "$cwd" rev-parse --abbrev-ref @{upstream} 2>/dev/null)
        if [[ -n "$upstream" ]]; then
            # Get last fetch time
            fetch_head="$cwd/.git/FETCH_HEAD"
            fetch_ago=""
            if [[ -f "$fetch_head" ]]; then
                fetch_time=$(stat -f %m "$fetch_head" 2>/dev/null || stat -c %Y "$fetch_head" 2>/dev/null)
                if [[ -n "$fetch_time" ]]; then
                    now=$(date +%s)
                    diff=$((now - fetch_time))
                    if [[ $diff -lt 60 ]]; then
                        fetch_ago="<1m ago"
                    elif [[ $diff -lt 3600 ]]; then
                        fetch_ago="$((diff / 60))m ago"
                    elif [[ $diff -lt 86400 ]]; then
                        fetch_ago="$((diff / 3600))h ago"
                    else
                        fetch_ago="$((diff / 86400))d ago"
                    fi
                fi
            fi

            counts=$(git -C "$cwd" rev-list --left-right --count HEAD...@{upstream} 2>/dev/null)
            ahead=$(echo "$counts" | cut -f1)
            behind=$(echo "$counts" | cut -f2)
            if [[ "$ahead" -eq 0 && "$behind" -eq 0 ]]; then
                if [[ -n "$fetch_ago" ]]; then
                    sync_status="synced ${fetch_ago}"
                else
                    sync_status="synced"
                fi
            elif [[ "$ahead" -gt 0 && "$behind" -eq 0 ]]; then
                sync_status="${ahead} ahead"
            elif [[ "$ahead" -eq 0 && "$behind" -gt 0 ]]; then
                sync_status="${behind} behind"
            else
                sync_status="${ahead} ahead, ${behind} behind"
            fi
        else
            sync_status="no upstream"
        fi

        # Build git status string
        if [[ "$file_count" -eq 0 ]]; then
            git_status="(0 files uncommitted, ${sync_status})"
        elif [[ "$file_count" -eq 1 ]]; then
            # Show the actual filename when only one file is uncommitted
            single_file=$(git -C "$cwd" --no-optional-locks status --porcelain -uall 2>/dev/null | head -1 | sed 's/^...//')
            git_status="(${single_file} uncommitted, ${sync_status})"
        else
            git_status="(${file_count} files uncommitted, ${sync_status})"
        fi
    fi
fi

max_context=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
max_k=$((max_context / 1000))
if [[ $max_k -ge 1000 ]]; then
    max_display="$((max_k / 1000))M"
else
    max_display="${max_k}k"
fi

# Calculate context bar — prefer five_hour session usage over per-conversation context window
bar_width=10
session_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
if [[ -n "$session_pct" && "$session_pct" != "null" ]]; then
    pct=$(printf '%.0f' "$session_pct")
    pct_label="used"
else
    pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
    pct_label="of ${max_display} ctx"
fi
pct=$(( pct > 100 ? 100 : pct ))

bar=""
for ((i=0; i<bar_width; i++)); do
    bar_start=$((i * 10))
    progress=$((pct - bar_start))
    if [[ $progress -ge 8 ]]; then
        bar+="${C_ACCENT}█${C_RESET}"
    elif [[ $progress -ge 3 ]]; then
        bar+="${C_ACCENT}▄${C_RESET}"
    else
        bar+="${C_BAR_EMPTY}░${C_RESET}"
    fi
done

# Seven-day (weekly) rate-limit. Some plans expose a seven_day window on top of the
# five_hour one; when the weekly budget is the binding constraint the 5h bar can read
# ~0% while you're actually throttled. Computed here, rendered as its own block (pct +
# its own reset countdown) later. Absent on plans without the window (e.g. home/CCH).
sd_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
sd_color=""
if [[ -n "$sd_pct" && "$sd_pct" != "null" ]]; then
    sd_pct=$(printf '%.0f' "$sd_pct")
    sd_pct=$(( sd_pct > 100 ? 100 : sd_pct ))
    if [[ $sd_pct -ge 80 ]]; then
        sd_color="$C_WARN"
    elif [[ $sd_pct -ge 50 ]]; then
        sd_color="$C_NUDGE"
    else
        sd_color="$C_GRAY"
    fi
fi

ctx="${bar} ${C_GRAY}${pct}% ${pct_label}"

# Current conversation context window usage (compact/restart signal).
# Only show when the bar is tracking the 5h session — otherwise the bar
# already displays the context window pct and this would duplicate it.
ctxwin=""
cw_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
if [[ -n "$session_pct" && "$session_pct" != "null" && -n "$cw_pct" && "$cw_pct" != "null" ]]; then
    cw_pct=$(printf '%.0f' "$cw_pct")
    cw_pct=$(( cw_pct > 100 ? 100 : cw_pct ))
    cw_tokens=$(echo "$input" | jq -r '.context_window.current_usage | (.input_tokens + .cache_read_input_tokens + .cache_creation_input_tokens + .output_tokens) // empty' 2>/dev/null)
    if [[ -n "$cw_tokens" && "$cw_tokens" != "null" ]]; then
        cw_k=$((cw_tokens / 1000))
    else
        cw_k=$(( max_context * cw_pct / 100 / 1000 ))
        cw_tokens=$(( cw_k * 1000 ))
    fi
    if [[ $cw_tokens -ge 150000 ]]; then
        ctxwin="  |  ${C_WARN}${cw_k}k/${max_display} (${cw_pct}%)"
    elif [[ $cw_tokens -ge 100000 ]]; then
        ctxwin="  |  ${C_NUDGE}${cw_k}k/${max_display} (${cw_pct}%)"
    else
        ctxwin="  |  ${C_GRAY}${cw_k}k/${max_display} (${cw_pct}%)"
    fi
fi

# Calculate time until token refresh from rate_limits.five_hour.resets_at (Unix epoch seconds)
resets_at=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
token_refresh=""
if [[ -n "$resets_at" && "$resets_at" != "null" ]]; then
    now=$(date +%s)
    secs_until_reset=$((resets_at - now))
    if [[ $secs_until_reset -le 0 ]]; then
        token_refresh=" ${C_GRAY}🔄 now"
    else
        refresh_hrs=$((secs_until_reset / 3600))
        refresh_min=$(( (secs_until_reset % 3600) / 60 ))
        if [[ $refresh_hrs -gt 0 ]]; then
            token_refresh=" ${C_GRAY}🔄 ${refresh_hrs}h ${refresh_min}m"
        else
            token_refresh=" ${C_GRAY}🔄 ${refresh_min}m"
        fi
    fi
fi

# Seven-day block: its own section with weekly pct + a separate reset countdown,
# kept distinct from the 5h bar/refresh. Only rendered when the window is present.
sevenday=""
if [[ -n "$sd_pct" && "$sd_pct" != "null" ]]; then
    sd_resets_at=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')
    sd_refresh=""
    if [[ -n "$sd_resets_at" && "$sd_resets_at" != "null" ]]; then
        now=$(date +%s)
        sd_until=$((sd_resets_at - now))
        if [[ $sd_until -le 0 ]]; then
            sd_refresh=" 🔄 now"
        else
            sd_days=$((sd_until / 86400))
            sd_hrs=$(( (sd_until % 86400) / 3600 ))
            sd_min=$(( (sd_until % 3600) / 60 ))
            if [[ $sd_days -gt 0 ]]; then
                sd_refresh=" 🔄 ${sd_days}d ${sd_hrs}h"
            elif [[ $sd_hrs -gt 0 ]]; then
                sd_refresh=" 🔄 ${sd_hrs}h ${sd_min}m"
            else
                sd_refresh=" 🔄 ${sd_min}m"
            fi
        fi
    fi
    sevenday="  |  ${sd_color}⚠7d:${sd_pct}%${sd_refresh}${C_RESET}"
fi

# Get caveman mode badge
caveman_badge=""
CAVEMAN_FLAG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.caveman-active"
if [ ! -L "$CAVEMAN_FLAG" ] && [ -f "$CAVEMAN_FLAG" ]; then
    CAVEMAN_MODE=$(head -c 64 "$CAVEMAN_FLAG" 2>/dev/null | tr -d '\n\r' | tr '[:upper:]' '[:lower:]')
    CAVEMAN_MODE=$(printf '%s' "$CAVEMAN_MODE" | tr -cd 'a-z0-9-')
    case "$CAVEMAN_MODE" in
      off|lite|full|ultra|wenyan-lite|wenyan|wenyan-full|wenyan-ultra|commit|review|compress)
        if [ "$CAVEMAN_MODE" = "full" ]; then
            caveman_badge="  |  \033[38;5;172m[CAVEMAN]\033[0m"
        else
            SUFFIX=$(printf '%s' "$CAVEMAN_MODE" | tr '[:lower:]' '[:upper:]')
            caveman_badge="  |  \033[38;5;172m[CAVEMAN:${SUFFIX}]\033[0m"
        fi
        ;;
    esac
else
    caveman_badge="  |  \033[38;5;196m[CAVEMAN:DISABLED]\033[0m"
fi

# Line 1: [CCO|CCH] Model [effort] | ctx window | 5h bar + refresh | 7d block
line1=""
[[ -n "$profile" ]] && line1+="${profile_color}${profile} ${C_RESET}"
line1+="${C_ACCENT}${model}"
[[ -n "$effort" ]] && line1+=" ${C_EFFORT}[${effort}]"
line1+="${C_GRAY}${ctxwin}  |  ${ctx}${token_refresh}${sevenday}${C_RESET}"

# Line 2: Dir | Branch | Caveman badge
line2="${C_GRAY}📁 ${dir}"
[[ -n "$branch" ]] && line2+="  | 🔀 ${branch} ${git_status}."
line2+="${caveman_badge}${C_RESET}"

printf '%b\n%b\n' "$line1" "$line2"
