---
name: cc-usage-analytics
description: View and explore Claude Code usage analytics — model mix, estimated cost, request intent, weekly trend, and per-skill tier adherence across the office (cco) and home (cch) profiles. Use when the user wants to see usage stats, run the benchmark, view the weekly report, check if model spend/tiers are trending right, or invokes /cc-usage-analytics.
model: sonnet
effort: medium
invokedBy: human
---

Surface and explore Claude Code usage analytics. All tools live in
`~/.dotfiles/claude-code-shared/`. Run commands, then present a tight summary,
not the raw dump.

## Commands

Benchmark (`scripts/usage/cc-usage-benchmark.py`):
- Full report, both profiles: `python3 ~/.dotfiles/claude-code-shared/scripts/usage/cc-usage-benchmark.py`
- One profile: add `--profile office` or `--profile personal`
- Weekly model trend (mix + est cost): append `--trend`
- Tier adherence (expected vs actual model per skill): append `--adherence`
- Per-skill wall-clock latency + parallelism shape: append `--latency` (add `--min-n 3` to drop rare skills)

Weekly report (`scripts/usage/weekly-usage-report.sh`):
- Regenerate now: `bash ~/.dotfiles/claude-code-shared/scripts/usage/weekly-usage-report.sh`
- Latest saved report: read the newest file in `~/.cache/cc-usage-reports/usage-*.txt`
- Auto-runs Mondays 9am via launchd (`com.ericlingren.cc-usage-report`)

## How to respond

1. Read intent: quick snapshot, trend over time, tier adherence, a single
   profile, or a deep dive. For an open "show my usage", run the full benchmark
   plus `--trend`.
2. Run the matching command(s).
3. Summarize, do not paste the raw output. Lead with headline numbers (model
   mix, est cost, top request intents), then trend direction, then adherence gaps.
4. Surface 1-3 actionable observations. Examples: "Opus share climbing week over
   week", "tldr-tech ran on opus but is tiered sonnet (drift)", "T3 default holding".
5. Offer next actions: re-tier a skill, change the session model, or drill into a
   profile or week.

## Re-tiering from the data

If the data suggests a skill or agent should move tiers:
1. Edit `~/.dotfiles/claude-code-shared/resources/model-tiers.json` (skills map for skills, agents map for agents).
2. `python3 ~/.dotfiles/claude-code-shared/scripts/registration/sync-model-tiers.py --apply`
3. Full tiering docs: `~/.dotfiles/claude-code-shared/resources/model-tiers.md`

## Notes

- Cost is an estimate (list prices; subagent tokens are not folded in).
- Adherence is only meaningful for sessions after the tiering rollout (older runs
  used the session model).
- Profiles: office = `~/.cco`, personal = `~/.cch`. Default scans both.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `cc-usage-analytics`.
<!-- skill-done: cc-usage-analytics -->
<!-- learning-capture:end -->
