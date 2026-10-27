---
name: retro
description: Generate sprint retro talking points (3 things that went well, 3 ideas to improve) from your current-cycle GitHub PRs and Linear tickets. Prints inline for saying out loud in a synchronous retro. Use when the user says /retro, "sprint retro", "retro talking points", or "what went well this sprint".
invokedBy: human
model: sonnet
effort: medium
---

# Retro

Turn this cycle's PR and ticket data into six short talking points for a synchronous sprint retro: three that went well, three ideas to improve. Plain spoken sentences, printed inline. Nothing is saved.

## Process

### 1. Build retro data

Run from inside the target repo (gh resolves the repo from cwd):

```bash
bash ~/.dotfiles/claude-code-shared/scripts/retro/build-retro-data.sh
```

If it exits non-zero, show stderr and stop. The output is compact JSON. Field meanings are in the script header.

### 2. Pick the talking points (model turn)

Every point must rest on a number or a PR/ticket from the JSON. Never invent a metric.

**Went well (3).** Pick the strongest of:
- Delivery volume: `shipped.count`, named by theme (group `items` by `tag`, untagged items are "polish")
- Fast turnaround: `shipped.fast_merges`, `shipped.median_days_to_merge`
- Small, reviewable slices: `signals.small_prs`, chains split into standalone pieces
- Review spread: `signals.distinct_reviewers`
- CI health, only when `signals.ci_green_ratio` >= 0.8

**Ideas to improve (3).** Pick the most actionable of:
- Approved work not landing: `approved_unmerged` with high `open_days`
- Approved but CI red: `approved_ci_red`
- Feedback stalls: `changes_requested`, `unresolved_threads`
- Long chains: `chains` with several `open_prs`, where low-chain feedback blocks everything above it. Suggest parallel PRs off main
- Slow merges: shipped items with large `days_to_merge`
- CI health, when `signals.ci_green_ratio` < 0.8

Each improvement names the problem, the evidence, and one concrete change. Frame it as a team idea, not blame.

### 3. Print inline

```
**Went well**
1. **<short label>.** <one or two spoken sentences with the number or PR>.
2. ...
3. ...

**Ideas to improve**
1. **<short label>.** <problem + evidence>. <one concrete change>.
2. ...
3. ...
```

Rules:
- Write PRs as `#123` and tickets as `KEY-123`. No links needed.
- Sentences a person can say out loud. Short. No em dashes. No jargon like "bucket" or "rollup".
- First person ("I", "we"). Positive and constructive.
- Nothing before or after the two lists except a one-line note if a data source was empty.

## Notes

- Do not use the Linear MCP. All data comes from the script.
- The script scopes to the whole current cycle, not "since last standup".
- Do not save a file. The output is for reading in a live meeting.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `retro`.
<!-- skill-done: retro -->
<!-- learning-capture:end -->
