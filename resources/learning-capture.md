# Shared Learning Capture Block

Run this at the end of the skill's terminal turn. Always spawn the agent. It
decides whether anything is worth recording. Do not self-assess and skip.

## 1. Resolve the transcript path

```bash
bash ~/.dotfiles/claude-code-shared/scripts/learning/resolve-transcript-path.sh
```

Pass the printed absolute path as `transcript_path` (pass `null` if it printed `null`).

## 2. Spawn `capture-learning` in the background

Agent tool: `subagent_type: capture-learning`, `run_in_background: true`.
Prompt fields:

- `skill`: the slug given in the skill stub that referenced this file
- `transcript_path`: from step 1
- `brief_evidence`: one or two sentences on what happened this run. Name any
  backtracks, tool failures, user corrections, instruction gaps or redundant
  effort. Say "clean run" if there were none.
- `trigger`: your best guess (`tool_failure | backtrack | user_correction |
  instruction_gap | redundant_effort | uncategorized`), or omit it
- `anchors`: 1 to 3 short verbatim quotes copied from your own context that
  show the event (exact error text, the user's correction words, the output
  you retracted). Copy characters exactly, no paraphrase. Empty list if clean run.

Supplying `anchors` is the main speed lever. With them, the agent skips
searching the transcript entirely.

## 3. Print the closing message

Print the skill's closing suggestion (the next-step list from the skill stub).
Then, as the very last lines of the turn, print exactly:

```
⏳ Capturing learnings in the background (capture-learning). Keep this session open until it reports back.
<!-- skill-done: <slug> --> <!-- learning-eval: <slug> -->
```

## 4. When the background agent finishes

On its completion notification, print one line and nothing else:

- written: `✅ Learning captured: <lesson> (<id>)`. Take both from the agent's
  output line: `id=` value, and text after `| lesson: `. Shorten lesson to one
  sentence if longer.
- skipped: `Learning capture: <the agent's SKIP line>`
