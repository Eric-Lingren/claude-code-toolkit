---
name: improve-skill-learnings
description: >
  Apply captured learnings from unified-learnings.jsonl to their target skill,
  agent, process, or contract files. Shows a ranked table of targets, lets the
  user pick one or "run all" (offered when TOTAL ≤ 10). A picked target with
  ≤ 10 learnings also offers "run all" for that target. Validation is lazy —
  runs per-learning just before drafting, not upfront in batch. Run-all loops
  through every target and learning automatically with a y/n per diff. Covers
  all four improves_type values: skill, agent, process, contract.
model: sonnet
effort: high
invokedBy: human
---

# Improve Skill Learnings

Apply captured learnings from `unified-learnings.jsonl` to their target files.

## Step 1: Load and filter learnings

Run the following to load all `status == "captured"` entries. Entries with no
`status` field are treated as `captured` (legacy entries predate the field).

```bash
python3 - <<'PYEOF'
import json, os
from collections import defaultdict
from datetime import datetime, timezone

path = os.path.expanduser(
    "~/.dotfiles/claude-code-shared/learnings/unified-learnings.jsonl"
)
entries = []
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if e.get("status", "captured") == "captured":
                        entries.append(e)
                except json.JSONDecodeError:
                    pass
except FileNotFoundError:
    pass

groups = defaultdict(list)
for e in entries:
    key = e.get("improves") or "__unassigned__"
    groups[key].append(e)

now = datetime.now(timezone.utc)

def age_str(items):
    oldest = None
    for e in items:
        ts = e.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if oldest is None or dt < oldest:
                    oldest = dt
            except (ValueError, TypeError):
                pass
    if oldest is None:
        return "?"
    days = (now - oldest).days
    if days < 1:
        return "<1d"
    if days < 14:
        return f"{days}d"
    weeks = days // 7
    if weeks < 9:
        return f"{weeks}w"
    return f"{days // 30}mo"

sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))

print(f"TOTAL={len(entries)}")
for rank, (target, items) in enumerate(sorted_groups, 1):
    display = target if target != "__unassigned__" else "(unassigned)"
    itype = items[0].get("improves_type", "—")
    ids = ",".join(e["id"] for e in items)
    print(f"RANK={rank}|TARGET={target}|TYPE={itype}|COUNT={len(items)}|OLDEST={age_str(items)}|IDS={ids}|DISPLAY={display}")
PYEOF
```

Parse the output into a ranked table:

```
Rank | Target              | Type     | Learnings | Oldest
-----|---------------------|----------|-----------|-------
  1  | debug               | skill    | 4         | 3mo
  2  | to-seed             | skill    | 3         | 2w
  ...
  N  | (unassigned)        | —        | 2         | 5d
```

If `TOTAL=0`, print:

```
No captured learnings found. Nothing to do.
```

And stop.

## Step 2: User selects target (or "run all")

Ask the user which target they want to improve. Accept a rank number or a slug
name. Wait for the response.

**Run-all option:** When `TOTAL ≤ 10`, include a "Run all (N learnings)" option.
When selected, set `run_all = true` and skip to **Run-all mode** below — no
further target selection prompts appear.

If you use AskUserQuestion, pass at most 4 options (the tool maximum). When
run-all is offered, it takes one slot (pass it first). Pass the top 3 targets
in the remaining slots. Lower-ranked targets stay reachable via the built-in
"Other" free-text option.

Collect all captured entries for the selected target (by their `id` values from
step 1).

### Run-all mode

When `run_all = true`:

1. Work through targets in rank order. Skip `__unassigned__` targets unless
   that is the only one.
2. For each target, print a progress header:
   `--- Target <X>/<Y>: <slug> (<N> learnings) ---`
3. Iterate through the target's learnings using the pick-one loop (Steps 5–7).
   Lazy per-learning validation (Step 4) still applies — each learning is
   validated when it comes up in the loop, not upfront.
4. After all learnings for a target are exhausted (applied, skipped, or
   discarded), advance to the next target automatically. No prompt between
   targets.
   A learning retargeted mid-pass to a target outside the original rank list
   is deferred to the next run-all pass. Include it now only if the user
   explicitly asks.
5. After all targets are done, go to Step 8 (summary).

## Step 3: Fetch entry fields (no upfront validation)

Skip batch validation. Each learning is validated lazily in Step 4 when the
user selects it. This keeps parallel agent output out of the session context.

Step 1 prints only IDs. Fetch the full fields for the target's captured
entries. Set `TARGET` to the selected slug (`__unassigned__` for unassigned).
In run-all mode, rerun this for each target.

```bash
TARGET='<slug>' python3 - <<'PYEOF'
import json, os

target = os.environ["TARGET"]
path = os.path.expanduser(
    "~/.dotfiles/claude-code-shared/learnings/unified-learnings.jsonl"
)
keys = ("id", "improves", "improves_type", "reported_by", "problem", "lesson", "fix")
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("status", "captured") != "captured":
                continue
            if (e.get("improves") or "__unassigned__") != target:
                continue
            print(json.dumps({k: e.get(k) for k in keys}))
except FileNotFoundError:
    pass
PYEOF
```

Each output line is one entry. This output is the only source of entry fields.
Step 5 shows `problem` from it. Step 4 and Step 6 fill `{id}`, `{improves}`,
`{improves_type}`, `{problem}`, `{lesson}` and `{fix}` verbatim from it. Never
paraphrase them. Never compose them from memory. If an entry is missing from
the output, rerun this step before dispatching any agent.

Then go to Step 5.

## Step 4: Validate one learning (lazy — called per pick from Step 5)

Spawn ONE Haiku validation agent for the selected learning. Build `path_candidates`
based on `improves_type`:

| improves_type | Paths to check (in order) |
|---------------|--------------------------|
| `skill`       | `~/.dotfiles/claude-code-shared/skills/<improves>/SKILL.md` |
| `agent`       | First hit of `find ~/.dotfiles/claude-code-shared/agents -name '<improves>.md'`, else `~/.dotfiles/claude-code-shared/agents/<improves>.md` |
| `process`     | `~/.dotfiles/claude-code-shared/resources/<improves>.md`, then `~/.dotfiles/claude-code-shared/resources/<improves>.json` |
| `contract`    | `~/.dotfiles/claude-code-shared/contracts/<improves>.json`, then `~/.dotfiles/claude-code-shared/contracts/<improves>.md` |

After the declared type's paths, append the other three types' paths (same
slug) as fallback candidates, in table order.

Resolve the `agent` row with the recursive find every time it is used. Agent
files can be nested (e.g. `agents/seed-review/<improves>.md`). The flat path alone
misses them.

**Unassigned entries (`improves` is null):** Use `reported_by` as the slug.
List all four types' paths for it, in table order. If `reported_by` is also
null, pass an empty list. These paths are a starting point only. The
validator greps for the real owner. Do not add custom rules to the prompt.

Pass this prompt to the Haiku agent (model: haiku). Fill the learning entry
fields verbatim from the Step 3 fetch output:

```
You are a learning validation agent. Assess one learning entry and return a JSON verdict.

Learning entry:
  id: {id}
  improves: {improves}
  improves_type: {improves_type}
  problem: {problem}
  lesson: {lesson}
  fix: {fix}

Target file paths to check (in order):
{path_candidates — one per line}

Steps:
1. Check whether each path exists (use Bash: test -f <path> && echo exists).
   If {improves} is null, skip the rest of this step. The paths come from the
   reporter, not an owner. If none exist, go to step 5.
   If none exist → return {"id": "{id}", "verdict": "invalid", "reason": "file_not_found", "file_path": null}
   If the first existing path belongs to a different type than {improves_type} →
   return {"id": "{id}", "verdict": "misrouted", "reason": "type_mismatch", "file_path": "<path>", "suggested_improves": "{improves}", "suggested_improves_type": "<type of that path>"}
2. Read the first file that exists (Read tool).
3. Does the file ALREADY contain the substance of the fix or lesson?
   If yes → return {"id": "{id}", "verdict": "stale", "reason": "already_applied", "file_path": "<path>"}
   Also check for partial coverage. The file may hold the core lesson but omit
   one dimension (a trigger, scenario, or criterion). If so → return
   {"id": "{id}", "verdict": "partial", "reason": "partial_coverage", "file_path": "<path>",
   "existing_line": <line number of the existing coverage>, "missing": "<the omitted dimension>"}
4. Does this file itself perform the action the fix changes (e.g. edit code, spawn
   the agent, run the command)? If it only delegates that action to another skill
   or agent (e.g. an orchestrator skill that hands code edits to a runner agent) →
   return {"id": "{id}", "verdict": "misrouted", "reason": "<delegate file path>",
   "file_path": "<path>", "suggested_improves": "<slug>", "suggested_improves_type": "<skill|agent|process|contract>"}
5. Does the step or mechanism the fix changes live in a DIFFERENT file? (e.g. the
   fix edits an evidence-pack step, but this file has none.) Grep
   ~/.dotfiles/claude-code-shared/{skills,agents,resources,contracts} for the
   mechanism's distinctive terms. If exactly one other file clearly owns it →
   return {"id": "{id}", "verdict": "misrouted", "reason": "<owner file path>",
   "file_path": "<path>", "suggested_improves": "<slug>", "suggested_improves_type": "<skill|agent|process|contract>"}
   Before returning misrouted in step 4 or 5, read the suggested owner's file and
   confirm it performs the action itself. A name in the fix text is not proof of
   ownership. If the owner only delegates, do not return misrouted; go to step 6.
6. Has the file changed so significantly that the lesson is no longer relevant?
   If yes → return {"id": "{id}", "verdict": "stale", "reason": "no_longer_relevant", "file_path": "<path>"}
7. Does the fix contradict a shared managed resource the target is meant to
   follow? Such resources include files the target pulls in by a managed marker
   block (e.g. `<!-- learning-capture:start -->`) and files it references by
   path under ~/.dotfiles/claude-code-shared/resources/. Grep each one for the
   fix's distinctive terms and read the matching sections. A fix that changes
   the resource itself is not a contradiction. Only a fix that makes the target
   diverge from the resource counts. If it does →
   return {"id": "{id}", "verdict": "invalid", "reason": "contradicts_shared_resource", "file_path": "<path>", "resource_path": "<resource path>"}
   If the conflict is unclear, do not return invalid. Continue to step 8.
8. If {improves} is null and no file owns the mechanism →
   return {"id": "{id}", "verdict": "no_applicable_target", "reason": "no_owner", "file_path": null}
9. Otherwise → return {"id": "{id}", "verdict": "valid", "reason": "ok", "file_path": "<path>"}

Return ONLY the JSON object. No prose.
```

### Apply the verdict

- **`invalid` with reason `file_not_found`:** Confirm before marking. Run
  `find ~/.dotfiles/claude-code-shared -name '<improves>.*'`. If it hits, use
  the first hit as `file_path`. If its type (derived below) matches
  `improves_type`, treat the verdict as `valid`. Otherwise treat it as
  `misrouted` with the derived slug and type. Only mark `invalid` if find
  returns nothing.
- **`stale` or `invalid`:** Run update-learning.py to mark it. Print a one-line
  note. Remove from the picker list. Return to Step 5.
  ```bash
  python3 ~/.dotfiles/claude-code-shared/scripts/learning/update-learning.py \
    --id <id> --status <stale|invalid>
  ```
- **`misrouted`:** Retarget via update-learning.py (status stays `captured`).
  Print a one-line note. Remove from the picker list. Return to Step 5.
  ```bash
  python3 ~/.dotfiles/claude-code-shared/scripts/learning/update-learning.py \
    --id <id> \
    --improves <suggested_improves> \
    --improves-type <suggested_improves_type>
  ```
- **`no_applicable_target`:** No file owns the fix. Mark it `invalid`. Print a
  one-line note. Remove from the picker list. Return to Step 5.
  ```bash
  python3 ~/.dotfiles/claude-code-shared/scripts/learning/update-learning.py \
    --id <id> --status invalid
  ```
- **Malformed verdict:** Read its prose. If it names another owner, handle as
  `misrouted`; otherwise as `stale`.
- **`valid` with `file_path` outside path candidates** (compare after expanding
  `~`): treat as `misrouted`. Derive slug and type from the path:
  `skills/<slug>/SKILL.md` → skill, `agents/<slug>.md` → agent,
  nested `agents/<subdir>/<slug>.md` → agent (slug = file basename),
  `resources/<slug>.md|json` → process, `contracts/<slug>.json|md` → contract.
- **`valid`:** Set `file_path` as the canonical target path. Proceed to Step 6.
- **`partial`:** Handle like `valid`. Set `file_path` and proceed to Step 6.
  Carry `existing_line` and `missing` into the drafter prompt.

No validation summary block is printed. Validation is silent except for a
brief inline note when a learning is discarded or retargeted.

## Step 5: Pick-one loop — show learnings and let user choose

Display ALL captured learnings for the target as a numbered sub-list (no
pre-filtering). Show only the `problem` field per entry (one line each),
taken from the Step 3 fetch output.
These have not been validated yet — validation runs when one is selected.

```
Learnings for <target> (<N> remaining):

  1. filename versioning double-encoded when internal schema_version exists
  2. cleanup step uses shell rm, blocked by destructive-fs hook
  3. accuracy persona silently returns empty on large transcripts

Pick a learning to apply (number), or "done" to stop:
```

**Target run-all option:** When the target has ≤ 10 remaining learnings and
global run-all is not active, also offer "Run all <N> for <target>". This
applies even when the global `TOTAL > 10` hid the Step 2 run-all option. If you
use AskUserQuestion, pass this option first and fill the remaining slots with
the top learnings. When selected, set `target_run_all = true`. Then work
through every remaining learning in list order: Step 4, then Step 6, then Step
7. Do not show the picker between learnings. Each diff still gets its own y/n.
When the list is empty, go to Step 8.

Wait for user response. If "done" or equivalent, jump to Step 8 (summary).

When the user picks a number: run **Step 4** (validate that one learning).
Proceed based on the verdict — Step 4 either returns to this list (stale /
invalid / misrouted) or proceeds to Step 6 (valid or partial).

## Step 6: Draft diff for selected learning

Read the target file (resolved `file_path` from validation). Spawn a
session-model agent (subagent_type: general-purpose) with this prompt:

**Always spawn the drafting agent. Drafting the diff inline in your own turn is
a process violation, because the drafter works from the file and the learning
alone, without this session's context and assumptions. You may add extra
constraints to its prompt (style rules, which section to touch), not the diff.**

Fill `{id}`, `{problem}`, `{lesson}` and `{fix}` verbatim from the Step 3
fetch output. Never paraphrase them.

For a `partial` verdict, append this constraint to the drafter prompt:
`Existing coverage is at line {existing_line}. Extend that text. Add only: {missing}. Do not duplicate what is already there.`

```
You are drafting a unified diff to apply ONE learning to a target file.

Target file: {file_path}

Current file content:
<file>
{full content of file_path}
</file>

Learning to apply:
  id: {id}
  problem: {problem}
  lesson: {lesson}
  fix: {fix}

Instructions:
1. Read the learning's `fix` field carefully.
2. Produce a standard unified diff (diff -u format) suitable for `patch -p0`.
3. Be minimal and surgical. Do not rewrite sections unrelated to this learning.
4. Include 3 context lines before and after each change hunk.
5. If the lesson or fix uses absolute language (must, always, never), check
   for edge cases where it does not hold. If any exist, use softer framing.
   Flag the case as a risk or a question instead of a hard rule.
6. Return ONLY the diff, starting with "--- " and ending after the last hunk.
   No prose, no explanation.
```

Before presenting, check contract edits for type drift. If the target is a
contract `.md` and the diff changes a field's type or shape, grep that field
name in the contract's JSON schema counterpart under `contracts/` (for example
`refutation-schema.json`). Also grep `scripts/` for consumers that read it.
If the schema or a consumer still expects the old shape, flag the mismatch
with the diff. Ask whether to extend the change to those files. Skip this
check for wording-only contract edits.

Present the diff to the user. Show the actual diff, not a prose walkthrough of
it. Format rules:

- One numbered section per file. A diff touching several files gets several
  sections.
- Each section header names the file and the learning it addresses. Write the
  learning as a one-line summary of `problem`, so the user knows why this file
  is changing without scrolling back.
- Put each file's hunks in a fence tagged `diff` so added lines render green
  and removed lines red. An untagged fence renders every line the same color.
- Trim displayed context to 1 line per hunk. Strip `@@` headers. Elide long
  unchanged lines with `...`. Break long added prose lines at about 100 chars
  so nothing wraps mid-word. This is display only. Apply from the full
  drafter diff in Step 7.
- After the last section, add a short **My take** list (2 to 4 bullets): soft
  spots, a hunk worth dropping, risks. Offer matching choices in the prompt.

````
**1. `<file_path>`**
Learning: <one-line summary of problem>

```diff
 <1 line of context>
-<removed line>
+<added line>
```

**2. `<next file_path>`**
Learning: <one-line summary of problem>

```diff
...
```

**My take**
- <soft spot or risk>
- <hunk worth dropping, if any>

Apply? [y/n]
````

Wait for response.

- `n` or rejection: Print "Skipped." Do NOT mark the learning as applied.
  Jump back to Step 5 (show remaining list without the skipped one).
- `y` or acceptance: Proceed to Step 7.

## Step 7: Apply, commit, and loop

Apply each hunk of the approved diff with the Edit tool. Do NOT pipe the diff
to `patch`. Agent-generated diffs pass through model text output and may
contain HTML-escaped entities (e.g. `&lt;`, `&gt;`, `&amp;`) that corrupt a
patch. Use the literal file text as `old_string`/`new_string`, unescaping any
entities first.

If an Edit fails (old_string not found), the diff's context drifted from the
real file. That is a drafting failure, not a problem with the learning. Re-read
the file and re-anchor the hunk against the current text once. If it still
fails, report the error and skip. Leave the learning `captured` (it stays in
the backlog for a later run). Jump back to Step 5 with the remaining list.

Mark the learning as applied:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/learning/update-learning.py \
  --id <id> --status applied
```

Commit:

```bash
git add <file_path>
git commit -m "improve(<target>): apply learning <short problem summary>"
```

Keep the commit message subject under 50 chars. Use a truncated version of the
`problem` field if needed.

**Auto-loop:** Remove the applied learning from the list. If learnings remain
for the current target, jump back to Step 5 and show the updated list. When
`target_run_all = true`, skip the list and validate the next learning. If
none remain, print "All learnings for <target> done." then:
- In single-target mode: continue to Step 8.
- In run-all mode: advance to the next target (Step 2 run-all logic).

## Step 8: Summary report

Re-read unified-learnings.jsonl to tally the remaining backlog:

```bash
python3 - <<'PYEOF'
import json, os
from collections import defaultdict

path = os.path.expanduser(
    "~/.dotfiles/claude-code-shared/learnings/unified-learnings.jsonl"
)
groups = defaultdict(int)
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if e.get("status", "captured") == "captured":
                        key = e.get("improves") or "(unassigned)"
                        groups[key] += 1
                except json.JSONDecodeError:
                    pass
except FileNotFoundError:
    pass

for target, cnt in sorted(groups.items(), key=lambda x: -x[1]):
    print(f"  {target}: {cnt}")
PYEOF
```

Display:

```
Summary for <target>:
  Applied: N  (one commit each)
  Skipped: N  (user declined)
  Retargeted: N  (moved to correct owner, still captured)
  Stale:   N  (marked stale, filtered before picker)
  Invalid: N  (marked invalid, filtered before picker)

Remaining backlog (<M> total across all targets):
  <slug1>: N learnings
  <slug2>: N learnings
  ...
  (unassigned): N learnings
```

If the backlog is empty, print "Backlog clear. No remaining captured learnings."

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `improve-skill-learnings`.
<!-- skill-done: improve-skill-learnings -->
<!-- learning-capture:end -->

## Step 9: Final outcome line

After the learning-capture agent finishes (or while it runs in background),
print a single concise outcome line so the user sees the result without scrolling
back. Format:

```
<target>: <N> applied, <N> skipped, <N> retargeted, <N> stale, <N> invalid. Backlog: <M> remaining.
```

This is the LAST thing printed. It must appear after the learning-capture tail
block, not before it.
