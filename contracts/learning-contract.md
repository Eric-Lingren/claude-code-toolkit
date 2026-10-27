# Learning Contract

**Format name:** Learning entry file (`claude-code-shared/learnings/unified-learnings.jsonl`)
**Schema:** `contracts/learning-schema.json` (schema_version: `"2"`)
**Writer:** `scripts/learning/log-learning.py` — the only path for writing entries; agents never write JSONL directly.

## Producers

All 30 shared skills. Plugin skills (caveman, chrome-devtools) are excluded.

- `skills/build-code/`
- `skills/cc-usage-analytics/`
- `skills/ci-fix/`
- `skills/clean-scaffolding/`
- `skills/pr-code-review/`
- `skills/debug/`
- `skills/dispatch-tasks/`
- `skills/find-work/`
- `skills/grill-me/`
- `skills/grill-with-docs/`
- `skills/handoff/`
- `skills/how-to/`
- `skills/improve-agent-benchmarks/`
- `skills/improve-codebase-architecture/`
- `skills/improve-component/`
- `skills/improve-directory-structure/`
- `skills/improve-skill-benchmarks/`
- `skills/improve-skill-learnings/`
- `skills/investigate/`
- `skills/pick/`
- `skills/prototype/`
- `skills/register-skill/`
- `skills/relay/`
- `skills/retro/`
- `skills/pr-revise/`
- `skills/run-task-followups/`
- `skills/standup/`
- `skills/tasks-to-linear/`
- `skills/tdd/`
- `skills/vet/`
- `skills/tldr-tech/`
- `skills/to-e2e-tasks/`
- `skills/to-spec/`
- `skills/to-seed/`
- `skills/to-tasks/`
- `skills/worktree/`

New skills registered via `register-skill` are added to this list automatically.

## Consumers

- `skills/improve-skill-benchmarks/` — future consumer; reads `unified-learnings.jsonl`, applies `fix` candidates, and owns promoting recurring `other` cause_label values into named cause values.
- `skills/improve-skill-learnings/` — primary consumer; reads `unified-learnings.jsonl`, validates learnings via Haiku agents, and applies surviving `fix` candidates to their target files.

## Schema file

[learning-schema.json](learning-schema.json)

## Storage layout

```
claude-code-shared/
  learnings/
    unified-learnings.jsonl
```

Single append-only log. All records from all skills land in one file. Each line is a complete, schema-valid JSON object. Created on first write by `log-learning.py`. Committed to the dotfiles repo like all other shared artifacts.

## Record shape (schema_version: "2")

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `"2"` | Server-injected. Callers must not supply. |
| `id` | UUID string | Server-injected. |
| `timestamp` | ISO-8601 UTC | Server-injected. |
| `type` | `"self"` or `"attribution"` | `self` = skill learning from its own run. `attribution` = cross-skill cause trace. |
| `reported_by` | skill slug | Skill that generated this record. |
| `improves` | skill slug or null | Skill whose behavior the fix targets (null if unknown). |
| `improves_type` | `"skill"` or `"agent"` | What kind of artifact `improves` names. |
| `cause` | string | Open vocabulary — see `learning-cause-vocabulary.json`. `"other"` requires a non-null `cause_label`. |
| `cause_label` | string or null | Required when `cause` is `"other"`. Snake_case free text. Null otherwise. |
| `problem` | string | What went wrong. |
| `why_missed` | string | Why the defect was not caught earlier. |
| `lesson` | string | Actionable rule or guideline. |
| `fix` | string or null | Concrete change that would prevent recurrence. Null if no actionable fix yet. |
| `evidence` | array | Each item: `{source, ref, quote}`. `source` is `"transcript"` or `"artifact"`. |
| `trace` | object or null | Attribution records only. Provenance pointers: `seed`, `tasks`, `task_id`, `pr`, `branch`. |
| `confidence` | `"confirmed"` or `"candidate"` | `confirmed` = verify-anchors.py (self) or artifact-grounding-judge (attribution) verified all anchors. `candidate` = weak but real anchors. |
| `status` | `"active"` | Reserved for future state transitions. |

## Agents

**Self records** (`type: "self"`) — produced at end-of-run by skill tail blocks:

```
skill tail block (brief_evidence, trigger hint, verbatim anchors)
  → spawns capture-learning agent in the background
  → capture-learning drafts full entry (greps prep-transcript.py output only if anchors are missing)
  → verify-anchors.py --write checks each quote against the transcript deterministically
  → grounded=true: verify-anchors.py pipes entry to log-learning.py
  → grounded=false or no correction-event: write nothing
```

**Attribution records** (`type: "attribution"`) — produced after a root cause is confirmed:

```
skill (debug, pr-code-review) confirms root cause + fix
  → spawns attribution-tracer agent with issue_description, fix, transcript_path
  → attribution-tracer walks provenance chain, drafts record
  → attribution-tracer spawns artifact-grounding-judge with draft
  → grounded=true: judge calls log-learning.py
  → grounded=false: judge rejects, nothing written
```

`log-learning.py` injects `schema_version`, `id`, and `timestamp` server-side before validation and append. Callers must not supply those fields.

## Cause vocabulary

The `cause` field uses an **open vocabulary** defined in `learning-cause-vocabulary.json`. Use a named value when one fits. Use `"other"` with a `cause_label` for novel patterns. Recurring labels are candidates for promotion to named values — `improve-skill-benchmarks` owns that process.

## Enumerate-discrete-anchors rule

For any entry whose evidence aggregates or counts repeated events (e.g. `redundant_effort`, `backtrack`, or any "tried N times" observation), the `evidence` array **must list the discrete transcript anchors** rather than asserting a bare count.

Correct: `"Ran Glob for '*.jsonl' three times: first at step 2 ('no results'), again at step 5 ('no results'), again at step 8 ('found learnings/unified-learnings.jsonl')."`

Incorrect: `"Ran Glob for '*.jsonl' three times without finding the file."`

The count is derived as `len(anchors)` by the grounding judge. This keeps the judge in verification (not free counting) range.
