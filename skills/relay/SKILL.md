---
name: relay
description: >
  Channel-agnostic egress orchestrator for reply tasks. Reads the task file produced by
  /pr-revise, determines the target channel for each reply task from reply_url, presents
  combined drafts for final HITL approval, then routes each approved draft to the
  appropriate channel adapter under agents/egress/. Use when dispatch-tasks routes the
  reply branch, or the user invokes /relay <task-file>.
model: sonnet
effort: medium
invokedBy: both
---

# Relay

Channel-agnostic egress orchestrator for the PR-feedback pipeline. `/pr-revise` writes
`reply` tasks into a task file; `/dispatch-tasks` routes the reply branch here after the
code branch lands. Relay determines which channel each reply belongs to from `reply_url`,
presents combined drafts for HITL approval, then routes each approved reply to the
appropriate channel adapter under `agents/egress/`. Channel-specific mechanics —
commit permalink construction, thread keying, and future live write-back — live in the
adapter, not here.

**Live posting.** The GitHub adapter (`post-github`) posts live via `gh api`. Other channel
adapters (Linear, Slack) are copy-only stubs until wired for live write-back. relay tracks
each outcome (posted / skipped / failed / copy-only) and prints a result summary at the end.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version `"2"`)
**Role:** consumer (reads `reply` tasks; routes to channel adapters under `agents/egress/`)

**Step-0 — validate input before processing:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <input-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not process the file.

## Process

### 1. Load the task file

Use the path argument (from `/dispatch-tasks` or the user). Run Step-0 validation, then
read the file.

Select eligible items: `task_type == "reply"` with `status` of `not_started` (skip `done`,
`merged`, `blocked`). For each reply task, if `blocked_by` names a code task whose status
is not `done`/`merged`, the item is **not ready** — skip it and note it for the summary.
The fix has not landed; the reply belongs in a later run.

### 2. Determine channel for each eligible reply task

For each eligible task, inspect `reply_url` to determine the target channel adapter:

| reply_url domain    | Channel adapter                          |
|---------------------|------------------------------------------|
| `github.com`        | `agents/egress/github/post-github.md`    |
| `linear.app`        | `agents/egress/linear/post-linear.md`    |
| (other / missing)   | Warn the user; skip the task             |

**Resolve-only tasks** (`resolve_only: true`) post nothing, so they use no channel adapter.
They require a `github.com` `reply_url` and `thread_id_type: "graphql_node_id"`. If either is
missing, warn the user and skip the task.

Also read the blocking code task's `commit` and `pr` fields. These are passed to the channel
adapter so it can construct channel-specific formatting (e.g. commit permalink for GitHub).
relay does not construct commit permalinks directly — that is channel-specific logic owned
by each adapter. If `pr` is null, the run was never pushed, so pass `commit` as null too.
An unpushed SHA would render a dead permalink.

If `pr` is set but `commit` is null, try to resolve the SHA from git log. This happens when
the code task finished in an earlier session and no push recorded it.

- Look for the task's commits on the PR branch. Prefer non-merge commits
  (`git log --no-merges`). One task may map to several commits.
- Resolve each one to a full SHA with `git rev-parse`. Never hand-type a SHA.
- Keep only SHAs on the pushed PR branch. Check with `git branch -r --contains <sha>` or
  `git merge-base --is-ancestor <sha> <pr-head>`. If none are pushed, keep `commit` null.
- The mapping is a best guess. Show it at the approval gate (step 3) so the user can fix it.

### 3. Present for final HITL approval

For each eligible task, build the approval display. If the task carries both
`original_comment_body` and `original_comment_author`, show the original comment above the
draft:

```
── T-0002 · Reply: msw hook mocks ──
Channel: github (post-github)
Thread: <reply_url>

── Original comment by @<original_comment_author> ──
"<original_comment_body>"

── Your draft reply ──
<reply_body>
Commit reference: <commit SHA — will be formatted as permalink by the adapter>
```

If either `original_comment_body` or `original_comment_author` is absent, omit the
original-comment block and show only the draft:

```
── T-0002 · Reply: msw hook mocks ──
Channel: github (post-github)
Thread: <reply_url>
DRAFT:
<reply_body>
Commit reference: <commit SHA — will be formatted as permalink by the adapter>
```

For a resolve-only task, show the thread and original comment with no draft:

```
── T-0003 · Resolve: <short thread descriptor> ──
Action: resolve thread (no reply posted)
Thread: <reply_url>

── Original comment by @<original_comment_author> ──
"<original_comment_body>"
```

Its send confirmation reads `This will resolve the thread at <reply_url>. Proceed? (y/n)`.

If relay resolved the SHA from git log (step 2), list every candidate under the draft:

```
Commit candidates (from git log, not recorded on the task):
  1. <full sha> <subject>   (default, latest)
  2. <full sha> <subject>
```

`post-github` takes one `commit`. The user picks one, or none. Default to the latest.

**Tense.** Replies post after the fix lands, so drafts should read as done. If the task has
a `blocked_by` code task, rewrite future-tense phrasing about the fix to past tense before
showing the draft. Example: "Removing it in the next push" becomes "Removed it". Change
tense only. Do not change meaning, add claims, or restyle the text.

- No `blocked_by`: show `reply_body` as written.
- Leave real future plans (a follow-up, a later PR) in future tense.
- If the commit may not match what the draft promised, keep the original wording and flag
  it to the user.

relay does not write the task file. The rewrite applies to the shown and posted draft only.

Ask the user to approve, edit, or skip each draft. Apply their edits to the printed copy.
This is the content review gate — the reply text is locked in here.

**After the user approves the content**, show a second confirmation gate before posting:

```
This will post to <reply_url>. Proceed? (y/n)
```

If a resolve trigger applies (see **Default scope** in step 4), name it in the same gate:

```
This will post to <reply_url> and resolve the thread. Proceed? (y/n)
```

If the task has no GraphQL thread node id, say the resolve will be skipped.

Only proceed to step 4 if the user confirms with `y`. If the user answers `n`, treat this
task as skipped and record it as `skipped (user declined send)` in the results table.

### 4. Send each approved draft through the channel adapter

For each task that passed both approval gates, run the resolved channel adapter. Pass:

- `draft` — the approved reply_body
- `target` — the reply_url (GitHub PR comment URL, etc.)
- `commit` — the blocking task's commit SHA (may be null)
- `pr` — the blocking task's PR URL (may be null)
- `thread_id` — the thread node id or database id (present only when task carries it)
- `thread_id_type` — `"graphql_node_id"` or `"database_id"` (disambiguates thread_id)
- `thread_database_id` — REST numeric comment id (present only when task carries it); used by the GitHub adapter's idempotency check and as `in_reply_to` for inline review replies

**Default scope.** A reply task posts only its reply. relay does not resolve the thread or
add a separate top-level PR comment. It resolves a thread only for a `resolve_only` task,
when the task's `acceptance_criteria` require the thread resolved, or when the user asks.
It adds a top-level comment only when the user asks. New line-anchored review findings are not
reply tasks. They go through pr-code-review comment mode (`post-pr-review.py`), not relay.

**Run `post-github` in this session, not as a subagent.** Read
`~/.dotfiles/claude-code-shared/agents/egress/github/post-github.md` and execute its steps
yourself with the inputs above. The user approved the post here. Subagent permission
classifiers do not inherit that approval, so a delegated `gh api` POST can be denied.
The adapter file stays the single source of posting mechanics. Do not copy its bash into
this skill or a new script. Its rules still apply: idempotency check, one POST, no
autonomous retry. Copy-only adapters (`post-linear`, `post-slack`) make no external writes
and may still be spawned as subagents.

Risk: if relay itself runs inside a subagent, the same approval gap may apply. If a POST is
denied, record it as failed and ask the user to re-run relay from the main session.

**Resolve-only tasks** skip the adapter. Run the resolve script directly:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/egress/resolve-review-thread.sh --thread-id <thread_id>
```

It prints one JSON line. `status` `resolved` or `already_resolved` → record `resolved`.
`status` `failed` → treat as a failed post (failure prompt below; show the `error` field).
The script makes one write attempt and never retries on its own.

The channel adapter returns a schema-valid egress-result with `status: "posted"`,
`"copy-only"`, or `"failed"`. Record the result for the step 5 summary.

**Verify each live post.** An adapter can report `posted` for a comment that never landed.
When `post-github` returns `status: "posted"` with a `url`, confirm the comment exists before
recording `posted`. The `url` is a browser link, so do not fetch it. Parse the comment id from
its anchor and GET the comment from the API (one read-only call, no retry loop):

```bash
# ...#issuecomment-<id>  (top-level PR comment)
gh api repos/<owner>/<repo>/issues/comments/<id> --jq .id
# ...#discussion_r<id>   (inline review reply)
gh api repos/<owner>/<repo>/pulls/comments/<id> --jq .id
```

- Output matches `<id>` → record `posted`.
- 404, empty output, or no parsable id → record `failed` with error
  `reported posted but comment not found`, then use the failure prompt below.
- Copy-only results skip this check. Resolve-only tasks keep the status the script returned.

Risk: a network or auth error from `gh` does not prove the comment is missing. Show the raw
error and treat it as a failed post so the user can check the thread.

**Resolve after reply.** `post-github` posts only. It does not resolve the thread. After a
reply is verified `posted`, if a resolve trigger applies (see **Default scope**), run the
resolve script as a separate call. Use the same command and status handling as the
resolve-only path above.

- It needs `thread_id_type: "graphql_node_id"`. If the task lacks one, warn and skip the
  resolve. The reply stays `posted`.
- Skip the resolve if the reply did not verify.
- On `status` `failed`, show the `error` and record `posted (resolve failed)`. Do not use
  Retry from the failure prompt. It would post the reply again.

Risk: acceptance criteria are free text. If it is unclear whether they ask for a resolve,
ask the user at the send gate instead of guessing.

**On failure:** if the egress-result has `status: "failed"`, show the error and prompt the
user:

```
Post failed: <error details>

Options:
  1. Retry
  2. Skip this task
  3. Abort remaining tasks
```

- **Retry (1):** re-run the same adapter with the same inputs (GitHub stays in this
  session). Repeat until the post succeeds, the user chooses skip, or the user chooses abort.
  If verification failed, warn first: the comment may have landed under another id, so a
  blind retry can duplicate it. Ask the user to check the thread before retrying.
- **Skip (2):** record this task as `failed` in the results and continue to the next task.
- **Abort (3):** stop processing remaining tasks. Record all unprocessed tasks as
  `skipped (aborted)` in the results.

### 5. Result summary

After all tasks are processed, print a per-task result table followed by totals:

```
── Relay Results ──
T-0002 · Reply: msw hook mocks: posted → https://github.com/owner/repo/pull/42#issuecomment-123
T-0003 · Reply: auth token: skipped (user skipped)
T-0004 · Reply: cache invalidation: failed → <error message>
T-0005 · Resolve: drop stale comment: resolved → https://github.com/owner/repo/pull/42#discussion_r456

Posted: 1 | Resolved: 1 | Skipped: 1 | Failed: 1 | Copy-only: 0
```

Count each outcome:
- **posted** — adapter returned `status: "posted"` and the comment was verified. If the
  thread was also resolved, show `posted + resolved` in the table.
- **resolved** — resolve-only task; the script returned `resolved` or `already_resolved`
- **skipped** — user skipped at content approval, declined the send confirmation, task was
  not ready (blocked_by not done), or unprocessed due to abort
- **failed** — adapter returned `status: "failed"` and user chose skip (or abort triggered)
- **copy-only** — adapter returned `status: "copy-only"` (non-GitHub channel stubs)

For `copy-only` tasks the draft was printed by the adapter for manual paste; include those
in the copy-only count and note them in the table as `copy-only → <draft printed above>`.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `relay`.
<!-- skill-done: relay -->
<!-- learning-capture:end -->
