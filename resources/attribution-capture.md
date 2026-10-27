# Shared Attribution Capture Block

For each confirmed issue/root-cause found this run: run this block BEFORE printing
the closing suggestion or handoff. Fires once per confirmed item — not once per
session. Skip false positives.

Collect available repo pointers from context:
- `transcript_path`: resolve via bash before spawning the agent:
  ```bash
  CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  encoded_cwd=$(pwd | sed 's|[./]|-|g')
  transcript_path="${CLAUDE_CONFIG_DIR}/projects/${encoded_cwd}/${CLAUDE_CODE_SESSION_ID}.jsonl"
  echo "$transcript_path"
  ```
  Run this command and capture the output. Pass the resulting absolute path
  explicitly in the agent prompt. Do not assume the path — derive it.
- `seed_path`: path to the seed file if referenced in this session
- `tasks_path`: path to the tasks file if referenced in this session
- `branch`: current git branch name (run `git rev-parse --abbrev-ref HEAD` if needed)
- `pr_url`: PR URL being reviewed, or merged PR URL if this session followed a merge

Spawn the `attribution-tracer` agent (`subagent_type: attribution-tracer`) with:
- `issue_description`: the confirmed issue or root cause description
- `fix`: the fix applied or recommended
- `transcript_path`: the resolved absolute path from above (required —
  attribution-tracer will fail-fast if absent or unreadable)
- Any optional pointers available (seed_path, tasks_path, branch, pr_url)

The attribution-tracer walks the provenance chain backward to find the earliest
escape point, drafts a v2 attribution record, and passes it to the
`artifact-grounding-judge` agent for verification. The judge writes to
`learnings/unified-learnings.jsonl` if grounded.
