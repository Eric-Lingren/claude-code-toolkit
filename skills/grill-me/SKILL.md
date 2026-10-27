---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
argument-hint: "[optional: path to a seed or handoff doc to resume from]"
model: opus
effort: xhigh
invokedBy: human
---

## Resume mode

**Trigger:** argument-presence detection. If ARGUMENTS contains a path, this is a resume. Do not inspect the schema or validate the file type — just check whether a path was passed.

**On resume, route the path through resolve-ref.sh before reading** (see `resources/resolve-ref-pattern.md`):
- Run `bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh $(basename <path>)`. On archive hit (output starts with `ARCHIVE:`), use the extracted content. On not-found (exit non-zero), surface the diagnostic and ask "Continue anyway?" — bypass rebuilds context from conversation. Then:

**Check for `open_threads`:**

**(a) open_threads is non-empty:** enter resume mode.
- Treat `decisions` (from the seed or handoff's embedded base seed) as settled givens. Do not re-litigate them.
- Treat `disposed_threads` as the off-limits lock list. Load every id from this list. Do not allow any thread matching a locked id to be raised again — not even under a different name. Relabel-resurrection (same semantic content, different wording) is blocked.
- Drill only the `open_threads` items. Each one is a judgment call that must be resolved, deferred, or rejected before the session ends.
- Apply the standard one-question-per-turn protocol to each open thread in sequence.

**(b) open_threads is empty (status: ready):** stop immediately.
- Tell the user: "This seed is already solidified — all threads are resolved. No grilling needed."
- Show next steps by running: `python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py grill-me` and printing the output.
- Do not start a grill session.

**(c) path is unreadable or missing:** say so plainly and ask what the user wants.
- Do not guess or fall back to a fresh grill. State the exact error and stop until the user responds.

---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one.

**First question (always):** Before diving into the design tree, ask: "Are there any design decisions made outside this thread — verbal, in a doc, offline — that should override what I see here?" Accept any declared prior decisions as settled constraints and do not recommend against them later. If the user overrides a recommendation and cites an offline or out-of-band decision, accept the override without re-litigating.

For each question:
1. Ask exactly ONE question per turn. One question mark. No sub-questions, no compound "X and Y?" questions, no follow-ups in the same message. Asking multiple questions at once is bewildering.
2. Provide your recommended answer for the question you ask. State what you think the answer should be and why.
3. If the user's initial prompt signals low familiarity with the topic (e.g., "I don't do this often", hedging language, or asking basic clarifications), use plain concrete language before introducing any domain-specific term. Prepend a one-sentence plain-language definition the first time you use jargon, internal names, or implementation vocabulary in a question.
4. When asking your first question, briefly signal the other major branches of the decision tree you plan to explore later, so the user knows you see the full picture.
5. Before recommending a CLI flag or configuration knob, ask: "Would you prefer a flag you pass explicitly, or an interactive prompt that asks at runtime?" Do not default to flags — users often forget to use them.
6. Before locking any caller (direct or indirect) to a single mode or default, ask: "Could any direct caller of this ever need the non-default option?" Do not assume all callers share the same usage pattern.
7. Before accepting a plan that deletes or replaces a wrapper or component, have a Haiku subagent inspect the deleted component for implicit behaviors, such as a `key=` prop that forces a remount when route params change. Then ask: "Should the replacement carry this behavior forward, or drop it on purpose?"

Before formulating your question, if the answer could be in the codebase, spawn a read-only Haiku subagent for file exploration rather than reading files inline. If a *fact* can be answered by reading code or project files, delegate that lookup to a Haiku subagent to look it up instead of asking the user. The *decisions* however are the users - put each one to them and wait for their answer.

If the user's input references a Linear ticket ID (e.g. KEY-1234), fetch it with `mcp__claude_ai_Linear__get_issue`. If that tool is missing or returns an auth error, do not retry or call the authenticate tool; fall back to `mcp__claude_ai_Notion__notion-ai-search` with the ticket ID. If that also finds nothing, ask the user to paste the ticket context.

Branch naming is not a grill topic. Do not propose a branch rename, even when a memory rule suggests one. Both `feat/`, `fix/`, or `spike/` prefixed branches and Linear `gitBranchName` formats are acceptable. If the user raises branch naming themselves, it is fine to discuss it.

## Session end protocol

When all major branches are resolved, signal the end explicitly:

> "Grill session complete. All branches resolved."

Then print a text recommendation — do NOT use `AskUserQuestion` or any blocking UI element:

> "Next: `/to-seed` to capture these decisions as a JSON IR, then `/to-tasks` or `/to-spec` from there."

**CRITICAL RULES:**
- Do NOT implement any code, create any files, or make any changes after the grill session ends unless the user explicitly asks.
- Answering "yes" or "yep" to a specific scoped question during the session does NOT authorize broader implementation. Specific questions have specific scope.
- Do not block. Print the suggestion and stop.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `grill-me`.
<!-- skill-done: grill-me -->
  - `/to-seed` — decisions are crisp and ready to capture
<!-- learning-capture:end -->
