---
name: pr-code-review
description: >
  Comprehensive code review of staged changes or a PR. Checks for bugs, security issues,
  performance, and adherence to project conventions. Use when user says "review my changes",
  "review this PR", "code review", or invokes /review.
model: sonnet
effort: high
invokedBy: human
---

<!-- tier-delegate: managed by sync-model-tiers.py -->
## Delegate menial lookups to Haiku (cost control)

During this skill, push pure read-only lookups DOWN to a cheap subagent instead
of running them on the current model. This covers: multi-file grep/glob,
"where is X defined / what calls Y", mapping a directory, reading many files to
locate something, or fetching a URL for reference.

Use the Agent tool with the `caveman:cavecrew-investigator` subagent (Haiku,
returns a compressed file:line answer). If that subagent is unavailable, spawn a
general agent with `model: haiku`. Keep all reasoning, decisions, and edits on
the current model. Delegate only the menial searching.
<!-- /tier-delegate -->

## Contract

**Format:** task file — `contracts/task-schema.json` (schema_version: `"2"`)
**Role:** always produces a task file with `review_finding` entries after the investigator gate (Step 5)

**Caller-context output modes:**
- `sprout-seed` caller: auto-select `fix` mode (the task file feeds to `build-code`)
- args contain `--fix` or `--comment`: preselect that mode (`--comment` still runs the pick, preview, and approval gate)
- standalone invocation with no flag: review first, then offer actions in Step 6

Every mode produces the same task file. The mode only decides what happens after.

---

You are performing a thorough code review. Follow this process exactly.

Do not ask the user anything before the review. Start at Step 1.

## Step 1: Gather the diff

First, identify the current branch and find its associated PR:

```
git branch --show-current
```

If a PR number was explicitly provided, use it directly:
```
gh pr diff <number>
```

Otherwise, look up the PR for the current branch:
```
gh pr view --json number,url 2>/dev/null
```

If a PR exists, fetch its diff:
```
gh pr diff <number>
```

If no PR exists (local-only branch with no upstream PR), fall back to diffing against the merge base with the main branch:
```
git diff $(git merge-base HEAD origin/main)..HEAD
```

Read CLAUDE.md if present — it defines the project's conventions you must enforce.

## Step 1a: Resolve voice profile

Before drafting review comments, resolve this skill's voice profile: look up `pr-code-review` in `claude-code-shared/resources/voice-routing.json`'s `skills` map to get the mapped profile name(s), then resolve each name in the `profiles` map to its `file` path (relative to `claude-code-shared/resources/voice-profiles/`) and read that file. Write review comments in that voice. Do not hardcode a profile filename — always resolve it through `voice-routing.json` at run time.

## Step 1b: Derive accurate line numbers from the diff

**Never cite a line number from the raw diff file's row count.** That number is meaningless to a reader navigating the source file.

Every hunk in a unified diff starts with a header:
```
@@ -old_start,old_count +new_start,new_count @@
```

`new_start` is the first line number in the **new file** for that hunk. Walk each line after the header and maintain a running counter:

- Line starts with ` ` (context): this line exists in the new file at the current counter. Increment counter.
- Line starts with `+` (added): this line exists in the new file at the current counter. **This is the line number to cite for added code.** Increment counter.
- Line starts with `-` (removed): this line does **not** exist in the new file. Do **not** increment counter.
- A new `@@ ... @@` header: reset counter to the new `new_start` value.

When you form a finding, cite the **new-file line number** derived this way, not the diff row.

If the changed line is a removal (a `-` line) with no replacement, cite the nearest surrounding context line in the new file instead, and note it was removed.

## Step 2: Spawn dimension agents in parallel

Spawn all five dimension agents in a **single parallel batch** — one Agent tool call per dimension, all sent in the same message. Do not wait for any agent to finish before launching the next. Pass each agent: (a) the full diff text from Step 1, (b) the CLAUDE.md content if present, and (c) the dimension-specific prompt below.

### Finding format (all dimensions)

Each agent must return findings as a JSON array. Each element:

```json
{
  "file": "path/to/file.ts",
  "line": 42,
  "severity": "bug|risk|nit|q",
  "category": "correctness|security|performance|conventions|test-coverage",
  "description": "One-sentence problem statement.",
  "suggested_fix": "One-sentence recommended fix.",
  "context": "Optional: a brief code snippet anchoring the finding. Omit this key entirely if not useful."
}
```

Positive observations (praise) use `severity: "praise"`. Line 0 means file-level.

### Diff-scope rule (all dimensions)

Findings must anchor to a line added or modified in the diff. You may flag a pre-existing issue only if a diff change makes it newly reachable, newly dangerous, or changes its semantics. Do not comment on unchanged code that was already present and unaffected by this PR.

Read surrounding context to understand the change, but the context is for comprehension, not for generating findings.

### API-bound pre-screen (all dimensions)

Before flagging an API-contract concern, confirm the code path reaches the server. This covers renumbering, schema changes, and contract mismatches. Trace the value from the changed line to a network call or serializer. If it stays client-side (e.g. FE-only input filtering or display state), drop the finding. Do not send it to the investigator gate.

---

### Dimension: correctness (model: sonnet)

You are performing the correctness dimension of a parallel code review. Return only a JSON array of findings — no prose, no markdown, just the raw array.

Review scope:
- Read the files surrounding each changed section for context. A line that looks wrong in isolation may be correct in context. A line that looks fine may conflict with a neighboring invariant.
- **Design holistically:** overall approach, user impact, complexity (unnecessary indirection/over-abstraction/premature generalization), YAGNI (speculative features → nit), parallel safety (race conditions, missing awaits, stale closures), naming clarity, comment quality (why not what).
- **Fowler code smells:** Mysterious Name, Duplicated Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man, Refused Bequest. Documented project conventions override the baseline. Each smell is a judgement call — label as "possible X". Most map to nit; raise to risk only when fragile.
- **Severity labels:** `bug` (broken behavior), `risk` (works today but fragile), `nit` (style/naming/minor), `q` (genuine question — unsure if a problem).
- **Runtime-dependent claims:** If a claim depends on runtime behavior not evidenced in the diff (lazy evaluation, DOM state, event propagation), use `q` instead of `bug` or `risk`. State what you would need to confirm it.
- **Apparent syntax errors in diffs:** If a hunk seems to show an unclosed JSX tag or a missing bracket, read the actual source file with the Read tool before reporting it. Diff text can carry HTML-encoded entities (e.g. `&lt;`, `&gt;`) that make valid syntax look broken. Report only what the source file confirms.
- **Loading races:** Before flagging a tab flash or loading race in a sub-component, trace the parent render path. Check for a page-level guard (Suspense boundary, `isLoading` gate, redirect) that keeps the component from rendering before data arrives. If one exists, drop the finding.
- Acknowledge genuinely praiseworthy code with `severity: "praise"`.
- Tone: ask open-ended questions before strong statements; offer alternatives; assume you may be missing context; reserve `bug` for things you are confident are broken.

---

### Dimension: security (model: sonnet)

You are performing the security dimension of a parallel code review. Return only a JSON array of findings — no prose, no markdown, just the raw array.

Review scope — run against **every changed file**:

**Injection & input validation**
- No user input concatenated into SQL, shell commands, or HTML
- All external data validated server-side with allowlists
- Data encoded for target context (HTML entities, parameterized SQL)
- No `dangerouslySetInnerHTML` without explicit sanitization

**Authentication & session management**
- Sessions created server-side with strong random identifiers
- Sessions fully invalidated on logout (not just cleared client-side)
- No auth state in client-controllable locations

**Access control**
- Authorization checks on every request using server-side session state
- No reliance on client-supplied roles/flags/IDs to gate access
- Sensitive operations don't assume caller is authorized just because they reached the endpoint

**Cryptography**
- No hardcoded secrets, tokens, or credentials in source
- No weak algorithms (MD5, SHA1, DES) for security purposes
- Key material not logged, exposed in errors, or stored in plaintext

**Error handling**
- Errors fail closed (deny by default)
- No stack traces/internal paths/system details in client responses
- Sensitive data not in log lines (passwords, tokens, PII)

**Supply chain**
- New third-party dependencies from maintained, reputable sources
- No packages with known CVEs
- Dependency version ranges not dangerously wide (`*` or `>=0.0.0`)

Use severity `bug` for confirmed vulnerabilities, `risk` for likely-exploitable patterns, `nit` for hygiene issues, `q` when uncertain.

Credential leaks: before you flag an axios or fetch call as a credential leak, check the client config and the URL. Cross-origin cookies are sent only when the axios instance sets `withCredentials: true` (or fetch uses `credentials: 'include'`). Confirm whether the URL is a relative path or an absolute presigned URL (e.g. S3), which carries its own auth and is not a cookie leak.

---

### Dimension: performance (model: haiku)

You are performing the performance dimension of a parallel code review. Return only a JSON array of findings — no prose, no markdown, just the raw array.

Review scope:
- N+1 query patterns or loops that hit a database/network per iteration
- Missing memoization on expensive pure computations called in render hot paths
- Unnecessary re-renders (unstable references passed as props/deps)
- Large synchronous operations blocking the event loop
- Unbounded data structures (arrays/maps that grow without eviction)
- Missing pagination/streaming on large result sets
- Inefficient data-structure choices (linear scan where a set/map is warranted)

Only flag real performance risks — not micro-optimizations. Use `risk` for patterns that will cause problems at scale, `nit` for minor inefficiencies, `q` when uncertain.

Exclusion: setState in the render body can be the documented React derived-state pattern. It is intentional when guarded by a changed-value check (e.g., `if (next !== prev) setState(next)`). Do not flag it as a performance issue unless you confirm it is not an intentional derived-state update.

Instance count: before you flag a recursive or polling loop as a scale risk from "N concurrent instances," read the component's call sites. Confirm how many instances can be mounted at once. Do not assume the worst-case N.

Callback identity: before you flag a missing `useCallback` on a callback passed to a third-party library, check how that library stores it. Search its source or `.d.ts` for internal ref or `useEvent`-style wrapping. If the library already stabilizes callback identity, do not raise the finding.

React context callback identity: when a context hook wraps some action callbacks in `useCallback` but not others, flag the inconsistency as `risk`. One unwrapped callback in an otherwise-memoized context value creates a new reference on every render and invalidates all consumers. If none use `useCallback`, flag as `q` — the context value may not be memoized at all, which is a separate issue.

---

### Dimension: conventions (model: haiku)

You are performing the conventions dimension of a parallel code review. Return only a JSON array of findings — no prose, no markdown, just the raw array.

Review scope:

**TypeScript / React**
- No `@ts-ignore` or `@ts-expect-error`
- No unconstrained `any` — prefer `unknown` + type guard
- No default exports — named exports only
- No class components — functional only
- No `==` equality — use `===`
- No `~~` floor trick — use `Math.floor()`
- No `+value` coercion — use `Number(value)` or `parseInt`
- Multi-arg functions that could take a single options object — flag as nit
- Booleans that represent more than two states — suggest enum

**Style / formatting**
- Single quotes, no semicolons, trailing commas, 2-space indent
- camelCase variables/functions, PascalCase components/types, kebab-case files, SCREAMING_SNAKE_CASE constants
- No single-letter variable names (including loop counters and callback params); flag every occurrence
- No magic numbers — named constants instead

**Comments**
- No comments that restate what the code does — well-named identifiers already say that
- No comments referencing the current task, ticket, or caller ("added for X", "used by Y", "handles the case from issue #123") — those belong in the PR description and rot as the codebase evolves
- Only comment when the WHY is non-obvious: a hidden constraint, a subtle invariant, a workaround for a specific bug, behavior that would surprise a reader
- Flag TODO/FIXME/HACK comments that lack a ticket reference — they rot without accountability

**Design system (frontend only)**
- Spacing uses `theme.space()` — not raw `px`/`rem` numbers
- Non-spacing lengths use `theme.unit()` — not raw numbers
- No inline Styled Components — use existing DS primitives or `shadcn`
- New UI checks `/src/design-system` before inventing a component
- New domain-aware UI checks `/src/shared-ui` before writing one-off

All findings here are `nit` unless the violation causes a type error or runtime breakage (then `risk` or `bug`).

---

### Dimension: test-coverage (model: haiku)

You are performing the test-coverage dimension of a parallel code review. Return only a JSON array of findings — no prose, no markdown, just the raw array.

Review scope:
- New behavior has a test
- Tests use `renderSM` and Testing Library queries (role > text > testId)
- Network calls use MSW, not internal function stubs
- Flag any `expect()` call inside an MSW request handler as `bug` severity. This is confirmed MSW behavior, so do not downgrade it to `q`. MSW swallows thrown errors and returns a 500. Use a request spy and assert outside the handler after `waitFor`.
- Happy path, error path, and edge cases are each covered
- Tests are not testing implementation details (internal function calls, state shape) — they test observable behavior

Use `risk` when new behavior has no test coverage, `nit` for coverage gaps in edge cases, `q` when you are unsure whether a test is needed.
If a claim depends on runtime or test-library semantics not evidenced in the diff (lazy mocks, focusability, event behavior), use `q` instead of `bug` or `risk`.

Mock cleanup: before you flag a spy or mock that is never cleared between tests, read the project's vitest or jest config. If `clearMocks`, `resetMocks`, or `restoreMocks` is enabled, the runner already cleans up. Do not raise the finding in that case.

Cache-reset state changes: a test may clear the query cache (e.g. `resetQueries` or `removeQueries`) to simulate a state change such as logout. If a `beforeEach` MSW handler still serves the old data, an active query can refetch right away. The assertion may then pass on a transient no-data gap, not a stable end state. Check whether the test overrides that handler (e.g. to return a 401) before it clears the cache. Flag as `risk` when the handler and an active observer are both visible in the test file. Use `q` when the refetch depends on something the diff does not show, such as `enabled`, `retry`, or whether the query has a mounted observer.

---

**Synchronization checkpoint:** Collect ALL five dimension agent notifications before proceeding to Step 2a. Do not begin Step 2a until every dimension agent has returned. Emitting review output or writing the task file before all agents have returned is a process violation.

## Step 2a: Validate line numbers (mandatory)

**This step is mandatory.** Run it after all five dimension agents return and before Step 3. Dedup groups by (file, line), so a wrong line breaks the merge.

Do not trust any agent's `line` value. Haiku-tier agents are the usual offenders. They often return the raw diff row instead of the new-file line. Check every finding from every dimension. Skip findings with `line: 0`.

1. **Hunk range check.** Find the hunk for the finding's `file`. The line must fall within `new_start..new_start+new_count-1` of some hunk in that file.
2. **File length check.** The line must not exceed the new file's actual line count.
3. **Recompute on failure.** If either check fails, treat the value as a raw diff row. Recompute the new-file line with the Step 1b hunk walk. Update the finding. Note the correction in your working notes.
4. **Unresolvable lines.** If recomputation still fails, set `line` to 0 (file-level) and prefix the description with "Line unverified."

Pass the corrected array to Step 3.

## Step 3: Dedup findings

After Step 2a validates line numbers, apply mechanical dedup to the combined JSON arrays:

1. **Group by (file, line).** Findings with the same `file` and `line` values are duplicates regardless of dimension.
2. **Keep max severity.** Severity rank: `bug` > `risk` > `q` > `nit` > `praise`. Retain the highest-ranked severity for the group.
3. **Concatenate descriptions.** If multiple dimensions flagged the same location, join their descriptions with a space. Prefix each description with the category name in brackets, e.g. `[correctness] Problem A. [security] Problem B.` — but only when more than one dimension contributed. Use the primary category (highest severity contributor) as the merged finding's `category` field.
4. **No reconciliation agent.** This is a mechanical merge — no additional agent spawned.

Produce a single flat array of deduped findings. Proceed to the investigator gate with this array.

## Step 4: Pre-submit claim verification (investigator gate)

**This gate is non-optional.** Every finding that contains a factual claim must pass through it before being included in the output or posted as a PR comment. There is no bypass path.

Expect dimension agents to produce false positives, especially at the `risk` tier. A `VERIFIED_FALSE` drop means the gate is working. It is not a failure. Never skip or thin out the gate because the dimension agents seem reliable on a given PR.

### Pre-check: local HEAD matches PR head

Skip this check when Step 1 fell back to the local merge-base diff. When a PR exists, compare SHAs before spawning investigators:

```bash
git rev-parse HEAD
gh pr view <number> --json headRefOid -q .headRefOid
```

If the SHAs differ, do not silently proceed. Tell the user the local HEAD and the PR head differ. Pass the PR head SHA to each investigator so it reads files with `git show <sha>:<path>`, not the stale worktree. Do not checkout or reset the user's worktree without asking. It may hold uncommitted work.

Review findings are factual claims about the code. Before any finding is output or posted, spawn the `investigator` agent using the Agent tool for each factual claim in the finding body. Pass the finding text as the `claim` input along with `cwd` (the repo root) so the investigator can search the codebase.

**Do not substitute Read tool calls for the investigator spawn.** Inline reads bypass sub-claim decomposition and the investigation-result contract.

The investigator is the Opus-tier orchestrator defined in `agents/investigate/investigator.md`. It decomposes each claim into sub-claims, routes each to the correct leaf agent (code, web, GitHub, Linear, Notion), and returns a schema-valid `investigation-result` per `contracts/investigation-result-schema.json`.

**Wait-guard:** Task-notification delivery is not guaranteed. If notifications for running investigators have not arrived after a reasonable wait, call ListAgents to check their status. If a completed agent is found without a notification, use SendMessage to retrieve its result. Do not rely solely on task-notification delivery when waiting for parallel investigators.

### Verdict-to-action mapping

The investigator returns one of four verdicts. Apply this gate filter to each finding:

| Verdict | Gate action |
|---------|-------------|
| `VERIFIED_TRUE` | **Proceed** — claim confirmed; include the finding unchanged |
| `CONTESTED` | **Proceed** — genuine expert disagreement; note the contested status in the finding |
| `INSUFFICIENT_EVIDENCE` | **Downgrade** — caveat the claim explicitly; do not state it as fact, or drop the finding if the review comment depends entirely on the unverified claim being true |
| `VERIFIED_FALSE` | **Drop** — exclude this finding from output and from any comment posted to the PR |

**Self-judging prohibition:** The session model is never permitted to write `VERIFIED_TRUE` itself. `VERIFIED_TRUE` may only appear in a finding's record after the investigator agent returns that verdict. Self-judging a finding as `VERIFIED_TRUE` without spawning the investigator is a process violation, regardless of how obvious the claim appears from the diff.

**VERIFIED_FALSE — drop the finding:** Remove it from the review output entirely. Do not post it. Annotate your working notes: "dropped: VERIFIED_FALSE".

**INSUFFICIENT_EVIDENCE — downgrade with explicit caveat:** Include the finding only with a hedge (e.g., "I wasn't able to confirm this from the code — could you point me to the specific path?"). Never present an INSUFFICIENT_EVIDENCE claim as an established fact. If the comment cannot be written without asserting the unverified claim, drop it instead.

**VERIFIED_TRUE / CONTESTED — proceed:** These findings proceed to output. VERIFIED_TRUE findings proceed unchanged; CONTESTED findings note the disagreement in the finding body.

### Scope: which findings require the gate

Run the gate on findings that make a verifiable factual assertion about the code (`bug`, `risk`, and any `q` that asserts a specific fact). For each such finding, spawn one investigator call before output.

Pure style/naming `nit` findings that make no factual claim about runtime behavior may skip the gate.

---

## Step 5: Write task file

After the investigator gate, write all verified findings to a task file with `task_type: "review_finding"` entries.

**ID assignment:** Call `next-task-id.sh` once to get the first available ID, then increment numerically for each additional finding:
```bash
FIRST_ID=$(bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/next-task-id.sh docs/tasks)
```

**Shape of each task entry** (one per verified finding):
```json
{
  "id": "T-XXXX",
  "title": "<category>: <file>:L<line> - <brief description under 15 words>",
  "type": "AFK",
  "task_type": "review_finding",
  "description": "<finding.description>",
  "acceptance_criteria": ["The issue at <file>:L<line> is resolved per the suggested fix."],
  "blocked_by": [],
  "status": "not_started",
  "branch": null,
  "pr": null,
  "severity": "<finding.severity>",
  "category": "<finding.category>",
  "suggested_fix": "<finding.suggested_fix>"
}
```

Include `"context": "<finding.context>"` only when the finding includes a context snippet. Omit the field entirely when absent.

**Write the tasks array** as JSON to `/tmp/pr-review-tasks.json` with the Write tool. Do not use a `python3 -c` one-liner. The data-exfil hook blocks it.

Then call `create-task-envelope.py` to build the validated envelope:

```bash
# Generate the task file slug
SLUG="pr-review"
FILENAME=$(bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/task-filename.sh "$SLUG")

# Build the envelope (validates against task-schema.json automatically)
python3 ~/.dotfiles/claude-code-shared/scripts/scaffolding/create-task-envelope.py \
  --producer pr-code-review \
  --source-type session \
  --strategy per-task \
  --tasks-file /tmp/pr-review-tasks.json \
  --output "docs/tasks/$FILENAME"
```

On non-zero exit: STOP. Report stderr. Do not print the `task_file_path:` output line.

**Output line (after human-readable findings):**
```
task_file_path: docs/tasks/<filename>
```

The caller reads this line to route the task file to `build-code` (sprout-seed mode). In standalone mode, the Step 6 handoff block follows it and is the last thing printed.

---

## Provenance

pr-code-review always writes a task file (Step 5), stamped with `"producer": "pr-code-review"` and `"source": {"type": "session", "ref": null}` per `contracts/task-schema.json`. The caller — not this skill — determines how the task file is consumed (see Contract above).

## Output format

**Severity-to-emoji mapping** (for rendering deduped findings from the JSON array):

| JSON severity | Display |
|---------------|---------|
| `bug`         | 🔴 **bug** |
| `risk`        | 🟡 **risk** |
| `nit`         | 🔵 **nit** |
| `q`           | ❓ **q** |
| `praise`      | (inline, no count) |

Start with a one-line summary: `N findings: X 🔴 Y 🟡 Z 🔵 W ❓`

Then list findings grouped by file starting with **FILE:** `<FILE_PATH>` and in order of severity. End with a **Verdict**: `Approve` / `Request changes` / `Needs discussion`.

Finding line format: `<file>:L<line>: <label>: <description>`

Never list findings the gate dropped or that fell out of diff scope. They're noise to the user.

Write nothing that doesn't belong in a comment thread. No preamble, no "Overall this looks great."

**The findings block is mandatory and verbatim.** Render it after Step 5 writes the task file, as the last text before Step 6. Never replace it with a prose summary, even for a single finding or a zero-finding review. Progress notes printed while waiting on investigators do not count as the findings block.

## Step 6: Route based on review_mode

**Gate:** the full findings block from Output format (emoji summary line, per-file findings, Verdict) must already be printed in this turn. If it is not, print it now. Do not call `AskUserQuestion` until it is on screen. The user decides from that block.

Then resolve `review_mode`:

- `sprout-seed` caller → `fix`
- `--fix` / `--comment` arg → that mode
- otherwise → ask with `AskUserQuestion` (single question, header `Next`):
  - `Comment` → pick findings, preview drafts, post only on approval, then hand off
  - `Fix` → hand off the task file to a new session for fixing
  - `Done` → hand off the task file for later

  Omit `Comment` when no PR exists. Act on the answer using the modes below. If the question times out, is dismissed, or gets any other answer, treat it as `show`. This is safe because Step 5 already wrote the task file, so no finding depends on the answer.

**Never run fixes in this session.** A review fills most of the context window. Every mode ends with the new-session handoff block below. Do not invoke `dispatch-tasks`, `build-code`, or any fix work inline. Do not ask a follow-up question after the handoff.

### New-session handoff block (every mode)

Resolve the absolute task file path with `realpath docs/tasks/<filename>`. Print this block as the last output of the skill, after any mode-specific output:

````
task_file_path: docs/tasks/<filename>

Context is spent. Start a new session to act on the findings.
Task file:
```
<absolute task file path>
```
In the new session, run:
```
/dispatch-tasks <absolute task file path>
```
````

Use the absolute path in both fences, so the user can paste it into a session started from any directory.

### `comment` mode

Post the findings the user picks as one PR review, with an inline comment on each finding's line. Requires a PR to exist (from Step 1).

**Posting is a hard HITL gate.** Choosing `Comment` or passing `--comment` only starts this flow. It never approves a post. Nothing reaches GitHub until the user picks the findings, sees every draft, and approves in a new message.

**1. Pick findings.** Ask with `AskUserQuestion` (single question, header `Post which`):
- `Bugs + risks (Recommended)`
- `Bugs + risks + questions`
- `Everything` (adds nits and praise)

The user can also answer with finding numbers from the findings block via "Other". If the question times out, is dismissed, or the answer is unclear, do not post. Fall through to `show` mode.

**2. Draft and preview.** Write only the picked findings to the JSON below. Then print every draft verbatim as `<file>:<line>` followed by its full `body`, and end with a count (`N comments will post to PR #<number>`). Ask the user to reply `post` to send, name changes, or `cancel`. End your turn here.

**3. Approve.** Post only when the user's next message explicitly approves (`post`, `yes`, `send`). If they ask for changes, edit the JSON, re-print the full preview, and wait again. On `cancel` or any other reply, do not post. Fall through to `show` mode.

Write a JSON array to `/tmp/pr-review-comments.json` with the Write tool. Add one entry per picked finding, each anchored to its own line:

```json
[
  {"file": "src/foo.ts", "line": 42, "body": "risk: <description>\n\n<suggested_fix>"},
  {"file": "src/bar.ts", "line": 7, "body": "<praise, no label>"}
]
```

Posted bodies use a plain-text label (`bug:`, `risk:`, `q:`, `nit -`). Praise has no label. Never post the severity emoji, finding counts, or the verdict. Those are for the in-session output only. Drop praise with `line: 0` rather than fold it into a summary.

Write each `body` in the Step 1a voice. After approval, post with no `--summary`:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/egress/post-pr-review.py \
  --pr <number> \
  --findings /tmp/pr-review-comments.json
```

The script checks each line against the PR diff. It moves `line: 0` findings and findings outside the diff into the review summary body. This avoids a 422 that would reject the whole review. On non-zero exit, STOP and report stderr. Then check `gh api repos/<owner>/<repo>/pulls/<number>/reviews` for a review that landed anyway. Never re-run the script. On success, relay the `inline: N  folded: M` line and the review URL. Then print the handoff block.

### `fix` mode

Print the handoff block. Do not invoke `dispatch-tasks` here. The user runs it in the new session.

### `show` mode

Print the handoff block. Take no further action.

<!-- attribution-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/attribution-capture.md`.
<!-- attribution-capture:end -->

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `pr-code-review`.
<!-- skill-done: pr-code-review -->
  - `/dispatch-tasks` — start a new session and pass it the absolute task file path to fix the findings
<!-- learning-capture:end -->
