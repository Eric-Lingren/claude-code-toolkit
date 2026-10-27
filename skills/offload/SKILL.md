---
name: offload
description: >
  Offload a skill invocation to a remote cloud agent. Accepts a target skill name and
  optional input content; looks up the skill's model and effort from model-tiers.json;
  spawns an Agent with isolation: 'remote' and the resolved model; instructs the remote
  agent to run the target skill at the looked-up effort level and return the full output
  as text; then writes the result to docs/seeds/ (for seeds) or docs/tasks/ (for tasks).
model: sonnet
effort: medium
invokedBy: human
---

# Offload

Runs a target skill in a remote cloud agent and writes the output to the local workspace.

## Inputs

```
/offload <target-skill> [input-content]
```

- `<target-skill>` — the name of the skill to invoke remotely (e.g. `to-seed`, `to-tasks`).
- `[input-content]` — optional content to pass as input to the remote skill. If omitted,
  compress the current conversation context into a brief (a few hundred words) and use that
  as input.

## Process

### 1. Parse args

Read the user's message. Extract `target-skill` and any `input-content` exactly as provided.

If no target skill is given, print the invocation form above and stop.

If no `input-content` was provided, compress the current conversation to a brief:
- Include the topic, key decisions, and any file paths or data needed by the target skill.
- Keep it under 400 words — enough context for a cold agent to run the skill without this session.
- Always include the current git branch: run `git branch --show-current` and append `Current branch: <result>` to the brief. Skills like `to-seed` use this to skip their interactive branch prompt.

### 2. Look up model and effort, and read the target SKILL.md (parallel)

Issue both reads in the same turn — they are independent:

- Read `~/.dotfiles/claude-code-shared/resources/model-tiers.json` to resolve the tier for `target-skill` (fall back to `default` if not listed). Resolve `model` and `effort` from the `tiers` section.
- Read `~/.dotfiles/claude-code-shared/skills/<target-skill>/SKILL.md`. If the file is not found, stop and report the missing path. Do not guess alternate locations.

Wait for both reads before step 4.

### 4. Spawn the remote agent

Spawn an Agent with:

```
isolation: "remote"
model: <resolved model>
description: "Offloaded <target-skill> run"
prompt: <see template below>
```

**Prompt template:**

```
You are running the /<target-skill> skill at effort level: <resolved effort>.

## Skill instructions

Execute the following skill exactly. Do not deviate from its output format.

<full contents of the target SKILL.md, pasted verbatim>

## Input

<input-content or compressed context brief>

## Remote execution constraints

You are running cold with no access to the local filesystem or session transcript. Apply these rules:

1. **Branch prompts**: do not ask. Use the `Current branch:` value from the Input section above.
2. **Adversarial verification** (e.g. to-seed persona stage): skip it. Set `verification.status: "degraded"` and `verification.skipped_reason: "no local transcript available in remote execution"`. Do not spawn persona sub-agents.
3. **File writes**: do not write files. Return the output as text — the caller writes it to disk.

## Output instructions

1. Follow the skill's output format exactly — same structure, same labels, same ordering.
2. Return ONLY the skill's final output in your response — no truncation, no summary.
   - If the skill outputs plain text (e.g. pr-code-review), return plain text.
   - If the skill outputs a JSON file (e.g. to-seed, to-tasks), return valid JSON.
   - Do NOT invent a wrapper format. Return verbatim what the skill specifies.

Effort level reminder: work at <resolved effort> effort — <effort description>.
```

Effort descriptions by level:
- `low` — fast lookup, minimal reasoning, answer directly.
- `medium` — mechanical execution, well-defined scope, no open-ended exploration.
- `high` — context-aware, standard depth.
- `xhigh` — deep reasoning, architecture-level thinking.

### 5. Receive output and write to disk

Receive the agent's text response.

Determine the output directory and file extension:
- If the target skill produces **seeds** (e.g. `to-seed`): write to `docs/seeds/`, use `json`
- If the target skill produces **tasks** (e.g. `to-tasks`): write to `docs/tasks/`, use `json`
- Otherwise: write to `docs/offload-output/` (create if needed), use `md`

Generate a filename:

```bash
~/.dotfiles/claude-code-shared/scripts/scaffolding/doc-filename.sh <target-skill>-offload <ext>
```

Write the agent's response verbatim to that path.

### 6. Display the output

After writing, display the agent's output directly to the user — exactly as the skill would have shown it locally. Do not summarize or reformat. Print the full content, then report the output path on a final line:

```
Output: <path>
```

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `offload`.
<!-- skill-done: offload -->
<!-- learning-capture:end -->
