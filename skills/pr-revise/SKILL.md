---
name: pr-revise
description: >
  Post-publish PR feedback ingestion skill. Harvests reviewer and bugbot comments from an
  already-published PR, runs provisional triage, applies mandatory diligence, presents an
  HITL confirmation gate, fires attribution-tracer for confirmed escapes, and stops at a seed
  (via /to-seed) whose provenance captures both the code fixes and the PR replies to post. Use when
  the user says "revise PR", "process PR feedback", "review my PR comments", or invokes /pr-revise.
slug: pr-revise
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

## Scope

This skill processes feedback on PRs the **user already published** (post-review ingestion).
It is not for pre-vetting your own code before publishing (use `/pr-code-review` for that) and
not for reviewing someone else's PR (use `/pr-code-review` for that too). It does not post
GitHub comments, resolve threads, or push code during its own run. All outward writes are
deferred to downstream skills.

---

## Step 1 — Input: resolve the PR URL

**Primary:** Accept a PR URL as the skill argument (ARGUMENTS field).

**Fallback (no URL given):** Run:
```bash
gh pr list --author @me --state open
```
Present the output as a numbered list. Prompt the user to pick one. Use the selected PR URL
for all subsequent steps.

## Step 2 — Repo assertion

Resolve the PR's **base** repository from the GitHub API (the repo the PR targets, not the
fork it originates from). For fork-based PRs `headRepository` is the fork and would fail the
assertion against cwd. Extract `{owner}`, `{repo}`, and `{number}` from the PR URL, then query via REST:
```bash
gh api repos/{owner}/{repo}/pulls/{number} --jq '.base.repo.full_name'
```

Resolve the cwd repository from git:
```bash
git remote get-url origin
```
Normalize both to `owner/repo` form (strip protocol, `.git` suffix, hostname).

**If they differ:** print a clear error message naming both repos and stop immediately.
Do not proceed. Example:
```
Error: PR repo (acme/api) does not match cwd repo (acme/frontend). cd into the correct repo and re-run.
```

## Step 3 — GraphQL harvest

### 3a. Get authenticated user

```bash
gh api user --jq '.login'
```
Store as `$me`. Used to filter self-authored comments at harvest time.

### 3b. Resolve GraphQL hostname

Extract hostname from the PR URL (e.g. `github.com`).

### 3c. GraphQL query

Call `gh api graphql` to fetch all review threads and comments. Use this query shape:

```graphql
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 50) {
            nodes {
              databaseId
              body
              url
              author { login __typename }
            }
          }
        }
      }
      reviews(first: 100) {
        nodes {
          state
          body
          author { login __typename }
          url
        }
      }
      comments(first: 100) {
        nodes {
          databaseId
          body
          url
          author { login __typename }
        }
      }
    }
  }
}
```

Pass `-f owner=<owner> -f repo=<repo> -F number=<number>` as variables.

### 3c-capture. Per-comment data capture

For each comment node collected from `reviewThreads` and `comments` (top-level), record these
three fields alongside the comment for use in the provenance block at Step 8c:

- **`original_comment_body`** — the comment's `body` field verbatim
- **`original_comment_author`** — the comment's `author.login` value
- **`thread_database_id`** — the comment's numeric `databaseId` value (the REST-accessible ID)

These are carried forward into every provenance item. They are not displayed in the harvest
output at Step 3g, but they must be present in memory when the provenance block is assembled.

### 3d. SSO 403 handling

Inspect the GraphQL response for `errors[].message` containing `Resource not accessible by integration`.

**If detected:** Print the exact recovery command and stop:
```
Error: SSO authorization required for this organization.
Run: gh auth refresh -h <hostname>
Then re-run /pr-revise.
```

Do not attempt to continue with partial data.

### 3e. Harvest filter

From the GraphQL response, collect items as follows.

**Bot-source rule (the noise filter).** A bot's only first-class channel is the inline review
thread. That is where line-level feedback (bugbot, etc.) lands. Bots also emit high-volume
administrative traffic through top-level PR comments and review summaries: CI status
(chromatic), coverage reports (codecov), ticket linkbacks (linear), and "I reviewed your
changes and found N issues" meta-notices that merely restate their own inline threads. None of
that is actionable feedback. So bot-authored top-level comments and bot-authored review
summaries are **dropped silently** — they never reach harvest display or triage, and they are
not counted or surfaced.

An author is a bot when `author.__typename === "Bot"`. This is the deterministic GitHub signal
and it does not depend on the `[bot]` login suffix (integrations like `chromatic-com`,
`linear-code`, and `codecov` lack it but still resolve to `__typename: "Bot"`).

**Review threads** (`reviewThreads.nodes`):
- Keep only threads where `isResolved === false`.
- **Self-note threads.** If the thread's first comment (`comments.nodes[0]`) is by `$me`, the
  author left a revision note on their own PR. Harvest that first comment as one item with
  source type `inline thread (self-note)`. Skip the rest of the thread's comments.
- **All other threads.** Take all `comments.nodes`, excluding comments where
  `author.login === $me` (the author's replies to reviewers are never feedback).
- Bot-authored comments are kept here (inline threads are a first-class bot channel).
- After filtering, drop any non-self-note thread with zero remaining comments.

**Review summary bodies** (`reviews.nodes`):
- Include reviews where `body` is non-empty, `author.login !== $me`, and
  `author.__typename !== "Bot"`. Drop all bot-authored summaries.
- Capture `state` (APPROVE / REQUEST_CHANGES / COMMENT) alongside the body.

**Top-level PR comments** (`comments.nodes`):
- Include only where `author.login !== $me` and `author.__typename !== "Bot"`. Drop all
  bot-authored top-level comments.

### 3f. Verification Report rejected-capture harvest

Check whether the current PR's build run produced a Verification Report with reviewer-rejected
captures.

**Resolve the PR head branch** (if not yet resolved):
```bash
gh pr view <pr_url> --json headRefName --jq '.headRefName'
```
Store as `$head_branch`. Compute `$branch_slug` by replacing `/` with `-`:
```bash
branch_slug=$(echo "$head_branch" | tr '/' '-')
```

**Check for the artifact URL file:**
```bash
cat docs/visual-changes/${branch_slug}/report-url.txt 2>/dev/null
```

**If the file is absent, empty, or unreadable — skip this step silently. Do not print anything.
Continue to Step 3g with only the PR harvest items.**

If the file exists and contains a non-empty URL (e.g. `https://claude.ai/artifacts/<artifact-id>`):

1. Use the `ArtifactData` tool to read the `captures` collection on that artifact:
   ```
   db.collection("captures").get()
   ```
   Filter the returned documents for those where `status === "rejected"`.

   If `ArtifactData` is unavailable in this session, or the artifact cannot be read for any
   reason — skip silently and continue to Step 3g.

2. For each rejected document, build a **Verification Report fix item**:

   | Field | Value |
   |-------|-------|
   | `source_type` | `"verification-report"` |
   | `title` | `"Fix visual regression: <spec_role> <viewport>"` — use the `spec_role` and `viewport` fields from the document |
   | `body` | Two-section body: **Judge rationale:** `<rationale field>` · **Reviewer note:** `<note field>`. Combined, truncated to 400 chars. |
   | `url` | The artifact URL read from `report-url.txt` |
   | `capture_ref` | `capture_ref` field from the document |
   | `viewport` | `viewport` field |
   | `candidate_png` | Published copy in `docs/visual-changes/${branch_slug}/` — reference by capture_ref and viewport (e.g. the file matching `*-<capture_ref>-<viewport>-after.png`) |

3. Append these fix items to the harvest list built in Step 3e. Place them after any PR items.
   Every rejected capture becomes exactly one fix item. There is no author filter, no
   resolved/unresolved check, and no bot-source rule — these items come from a human reviewer's
   explicit rejection action, not from automated comments.

### 3g. Display combined harvest output

Print a numbered list of all harvested items (PR items from Step 3e followed by Verification
Report items from Step 3f). Each entry shows:
- Item number
- Source type (inline thread / inline thread (self-note) / review summary / PR comment / **verification-report**)
- Author login (or `"reviewer"` for verification-report items)
- Comment or artifact URL
- First 120 characters of body (truncated with `…` if longer)

If zero items remain after both harvests, print:
```
No unresolved reviewer or bugbot comments found, and no rejected Verification Report captures. Nothing to triage.
```
and stop.

---

## Step 4 — Provisional triage

After displaying the harvest list, auto-classify each item using a two-axis model.

### Praise filter (before classifying)

Pull out pure praise first. An item is praise when it asks nothing, requests nothing, and claims no defect. Examples: "Thanks for adding this note!", "Bless you for pulling this out into a function". An item that praises *and* asks or suggests something (e.g. "Nice, but could this live in a util?") is not praise. Classify it normally.

Praise items skip the triage table, diligence, the HITL gate, and the seed. They get no `fix`, no `reply_body`, and no task. Print them as one line under the table, keeping their harvest numbers:

```
Skipped as praise (no action): #2, #25
```

The user can pull one back in through the override prompt (e.g. `25: nit`).

### Axis 1: Class

Assign one of these classes to every item:

| Class | Meaning |
|-------|---------|
| `bug` | Claims the code has a defect (logic error, crash, incorrect output, security flaw) |
| `change` | Requests a different approach or refactor without claiming a defect |
| `question` | Asks for clarification; no action claimed required |
| `diligence` | Missing test, doc, or safety check |
| `discuss` | Design/architecture debate; no clear verdict yet |
| `nit` | Style or naming preference; non-blocking. Must still suggest a change. Praise-only items never land here |

### Axis 2: Escape-disposition (bug class only)

For items classified as `bug`, assign one of these dispositions:

| Disposition | Meaning |
|-------------|---------|
| `unverified` | Default. The claim exists but has not been confirmed against code |
| `confirmed_escape` | Diligence confirmed the bug is real and it escaped code review |
| `false_flag` | Diligence showed the claim is incorrect; code is fine |
| `not_an_escape` | Bug is real but was a known tradeoff or intentional decision, not an escape |

**The skeptical default is `unverified`.** Never start a bug at `confirmed_escape`. Diligence (Step 5) is what moves the disposition.

### Triage table

Present the triage table to the user. Minimum columns:

| # | Excerpt (first 120 chars) | Class | Disposition |
|---|--------------------------|-------|-------------|
| 1 | "The nil check is missing..." | bug | unverified |
| 2 | "Consider extracting this..." | change | |
| 3 | "What does this fn return..." | question | |

The **Disposition** column appears only for `bug`-class items. Leave it blank for all other classes.

### User overrides

After presenting the table, prompt: "Override any classifications before diligence? Reply with row changes (e.g. '3: change → bug, unverified') or press Enter to accept."

Apply all stated overrides before proceeding.

**The triage table is a proposal only.** No action fires until the HITL confirmation gate in Step 6.

---

## Step 5 — Automated diligence gate (investigator)

**This gate is non-optional.** Every claim-bearing item must pass through it. There is no bypass path. Partial completion (leaving any item without an explicit verdict) is not allowed before proceeding to Step 6.

`revise-pr` is the only untrusted data inlet in the pipeline. Reviewer and bugbot comments are unverified claims. Other ingestion skills (debug, grill-me, code-review) start from pre-verified human or ruleset input; this one does not. The investigator agent is what makes the output trustworthy.

### Per-claim investigator invocation

For each claim-bearing item — primarily `bug`-class items, plus any `change` or `diligence` item that makes a verifiable factual assertion — spawn the `investigator` agent using the Agent tool. Pass the reviewer's claim text as the `claim` input, along with `cwd` (the repo root) so the investigator can search the codebase.

**Spawn all claim-bearing investigators in a single batch (one Agent call per claim, all in the same message).** Do not wait for one investigator to finish before spawning the next. Collect all results together before applying verdict-to-action mapping.

The investigator is the Opus-tier orchestrator defined in `agents/investigate/investigator.md`. It decomposes the claim into sub-claims, routes each to the correct leaf agent (code, web, GitHub, Linear, Notion), and returns a schema-valid `investigation-result` per `contracts/investigation-result-schema.json`.

Non-claim-bearing items (`question`, `nit`, `discuss`) do not require investigator invocation. Present the claim text and URL to the user for manual review; collect a `reviewed` verdict and any reclassification before proceeding.

**Self-notes bypass the investigator.** An `inline thread (self-note)` item is the PR author's own instruction, not an untrusted claim. Move it straight to the HITL gate with diligence status `author-note`.

**Verification Report items bypass the investigator.** A `source_type: "verification-report"` item carries a screenshot pair confirmed by a human reviewer — the visual evidence IS the claim. Do not spawn an investigator for these items. They move directly to the HITL gate in Step 6 with diligence status `reviewer-confirmed`. Auto-classify them as `change` unless the user overrides in Step 4.

### Verdict-to-action mapping

The investigator returns one of four verdicts. Apply this gate filter to each claim:

| Verdict | Gate action |
|---------|-------------|
| `VERIFIED_TRUE` | **Proceed** — claim confirmed; include in the draft unchanged |
| `CONTESTED` | **Proceed** — genuine expert disagreement; note the contested status in the draft |
| `INSUFFICIENT_EVIDENCE` | **Downgrade** — caveat the claim explicitly; do not state it as fact, or drop if the reply depends entirely on the claim being true |
| `VERIFIED_FALSE` | **Drop** — exclude this claim from the draft entirely; do not post it |

**VERIFIED_FALSE — drop the claim:** Remove it from the `reply_body` and do not create a `fix` task based solely on it. Annotate the triage table row: "dropped: VERIFIED_FALSE". The investigator's evidence block is the reasoning record.

**INSUFFICIENT_EVIDENCE — downgrade with explicit caveat:** Include the item in the draft only with a hedge (e.g., "I haven't been able to confirm this from the code — can you point me to the specific path?"). Never present an INSUFFICIENT_EVIDENCE claim as an established fact. If the reply cannot be written without asserting the unverified claim, drop it instead.

**VERIFIED_TRUE / CONTESTED — proceed:** These claims proceed to the draft. VERIFIED_TRUE items proceed unchanged; CONTESTED items note the disagreement in the reply body. Update the triage table disposition accordingly (VERIFIED_TRUE bug items move toward `confirmed_escape`; CONTESTED items stay at their current disposition).

### Reconciliation

Investigator verdicts may change an item's classification, not just its disposition:
- A `question` that verifies as VERIFIED_TRUE → promote to `bug, confirmed_escape`
- A `bug` returning VERIFIED_FALSE → reclassify to `false_flag`
- A `diligence` item returning INSUFFICIENT_EVIDENCE → caveat or drop

Update the triage table row for any item whose class or disposition changes after the gate.

### Gate exit condition

No claim-bearing item may exit this gate without a verdict from the investigator and a mapped action (proceed / caveat / drop) recorded in the triage table. Non-claim-bearing items need `reviewed`. Do not proceed to Step 6 until every item has a diligence verdict recorded.

---

## Step 6 — HITL confirmation gate

This is the last human checkpoint before any action proceeds. No action fires until this gate is passed.

Present the post-diligence classification as a confirmation table:

| # | Excerpt | Final Class | Disposition | Action |
|---|---------|-------------|-------------|--------|
| 1 | "The nil check..." | bug | confirmed_escape | attribution-tracer + fix task + reply task |
| 2 | "Consider extracting..." | change | | fix task + reply task |
| 3 | "What does fn return..." | question | | reply task only |

The **Action** column shows what Step 8 will capture in the seed for each item after the user confirms (`to-tasks` later turns these into tasks):
- `confirmed_escape` bugs: attribution-tracer spawned (Step 7), plus a non-null `fix` (→ code task) and a `reply_body` (→ reply task blocked by it)
- `false_flag`, `not_an_escape`, `unverified` bugs: a `reply_body` (plus a `fix` when a change is still warranted), no attribution
- Non-bug items: a `reply_body`, plus a `fix` only when the item needs a change on this PR
- Self-notes: a `fix` plus `resolve_only: true` (→ resolve-only reply task; no reply is posted, relay just resolves the thread)
- Every other substantive item gets a `reply_body`; nothing is posted during this skill's run

Prompt the user: "Confirm this categorization and proceed, or override any row before actions fire."

**Bulk options must disclose VERIFIED_FALSE items.** If you offer a sweep option ("agree to all", "accept all removals"), and any item it covers has a VERIFIED_FALSE verdict, name those item numbers in the option text. Add a one-line note that each reviewer's stated reason is factually wrong. Example: "Agree to all removals (note: #2's rationale 'rebase avoids this' is VERIFIED_FALSE)".

If the user still accepts a change on a VERIFIED_FALSE item, find a valid independent reason for it before drafting that item's reply in Step 8. If none exists, ask the user for one.

Apply any final overrides. Then proceed to Step 7.

---

## Step 7 — Attribution-tracer invocation

After the HITL gate is confirmed, for each item whose **final disposition is `confirmed_escape`**:

Spawn one `attribution-tracer` subagent with these exact inputs:
- `issue_description`: the confirmed defect claim text (the reviewer's exact words)
- `pr_url`: the PR URL from Step 1
- `branch`: the PR head branch, resolved via `gh pr view <url> --json headRefName --jq '.headRefName'`
- `transcript_path`: run `~/.dotfiles/claude-code-shared/scripts/learning/resolve-transcript-path.sh`
  once before spawning, and pass its output verbatim. It prints an absolute path or
  `null`. Never pass a placeholder string like `unavailable`. Pass `null` when the
  script prints `null`.

Attribution fires **once per `confirmed_escape` item**, not once per session. Each item gets its own attribution-tracer call.

**Items that must never trigger attribution-tracer:**
- `false_flag` disposition
- `not_an_escape` disposition
- `unverified` disposition
- Any non-bug class item

The `fix` field in the generated attribution record will be empty or TBD. This is intentional. The escape is being attributed before a fix exists; downstream sessions will confirm or fill the fix. Attribution demotes to candidate/`improves: null` when the escape is ambiguous.

**Invariant: this skill never makes outward HTTP writes during its own run.** No GitHub comment is posted. No review thread is resolved. No branch is checked out or pushed. All such actions are deferred to downstream skills (`relay`, `build-code`, etc.).

---

## Step 8 — Draft the response and scope, then seed stop

pr-revise does **not** write tasks directly. It stays on the canonical pipeline tree: it captures the outcome into a **seed** via `/to-seed`, and `/to-tasks` later turns that seed into the `code` fix tasks and `reply` tasks. This keeps lineage intact (seed → tasks → dispatch) exactly like every other planning skill.

The seed must carry **both** halves of the outcome per thread: the code scope to change (`fix`) **and** the reply to post (`reply_body`). You have the harvest, diligence, and session context to write both now; `to-tasks` will not have it later.

### 8a. Per item, decide the fix scope and draft the reply

Walk every substantive item (post-diligence, post-HITL). For each, set two things:

- **`fix`** — the code change to make on THIS PR, or `null` when no code change is warranted:
  - Non-null for: bug `confirmed_escape`; bug `not_an_escape` that still warrants a change; `change` accepted for this PR; `diligence` that adds a test or doc.
  - `null` for: `question`; `nit` accepted-trivial or declined; bug `false_flag`; `discuss`; any `change`/fix deferred to a later PR.
- **`reply_body`** — the ready-to-post draft reply (quality bar below). Every substantive item gets one, except self-notes: omit `reply_body` and set `resolve_only: true` instead, since replying to yourself is noise.

A non-null `fix` becomes a `code` task plus a `reply` task blocked by it (so the reply can cite the fixing commit). A null `fix` becomes a reply-only task. A `resolve_only` item (self-notes) becomes a code task plus a resolve-only reply task blocked by it, so relay resolves the thread once the fix lands. `to-tasks` does this wiring; here you only set `fix` and `reply_body`.

### 8b. Reply-body quality bar

Before drafting, resolve this skill's voice profile: look up `pr-revise` in `claude-code-shared/resources/voice-routing.json`'s `skills` map to get the mapped profile name(s), then resolve each name in the `profiles` map to its `file` path (relative to `claude-code-shared/resources/voice-profiles/`) and read that file. Write every `reply_body` in that voice, not generic AI prose. Do not hardcode a profile filename — always resolve it through `voice-routing.json` at run time.

Write each `reply_body` now, grounded in the diligence finding:
- Match the reviewer's register — terse for a nit, fuller for a design thread. Do not pad.
- Ground claims in the code: cite `file:line` or the symbol when it sharpens the reply. Never invent paths or line numbers; use only what diligence surfaced.
- Reply as the PR author in first person. No hedging, no "as an AI", no meta-commentary about drafting.
- Address the reviewer's actual point; do not restate their comment back at them before answering.
- Never claim work is done that is not done. A planned-but-unwritten fix reads as "fixing by…", not "fixed". **Do not hand-write a commit SHA** into `reply_body`; relay stitches the fixing commit in at post time once the code task lands.
- Keep the reviewer's handle out of the body; the thread already targets them.
- When the user accepted a change on a VERIFIED_FALSE item, the reply cites the independent reason from Step 6. Never repeat or agree with the reviewer's false claim.

Per-class framing: **bug/confirmed_escape** — acknowledge, confirm real, state the fix approach. **bug/not_an_escape** — confirm real, note it's a known facet, point to the fix. **bug/false_flag** — explain with evidence why the code is correct; stay collegial. **change** — state the decision; offer the alternative if they feel strongly. **question** — answer directly from the code. **discuss** — engage the tradeoff, state your lean, name the follow-up; if deferred, say so and point at the tracking item. **nit** — brief accept/decline with a one-line reason.

### 8c. Assemble the enriched provenance block

Build this block and hand it to `/to-seed` (it becomes the seed's `provenance`, per `contracts/seed-schema.json`). One entry per harvested item, in harvest order:

```json
{
  "pr_url": "<the PR URL from Step 1>",
  "head_branch": "<the PR head branch resolved in Step 3f or Step 7>",
  "items": [
    {
      "thread_id": "<the id value>",
      "thread_id_type": "graphql_node_id | database_id",
      "final_class": "<final class after diligence and HITL gate>",
      "disposition": "<final disposition for bug items, or null>",
      "reply_body": "<the drafted reply for this thread>",
      "reply_url": "<the originating comment URL from Step 3 harvest>",
      "fix": "<the code scope to change on this PR, or null>",
      "resolve_only": "<true for self-notes only; omit otherwise. When true, omit reply_body>",
      "original_comment_body": "<the verbatim comment body captured at Step 3c-capture>",
      "original_comment_author": "<the author.login captured at Step 3c-capture>",
      "thread_database_id": "<string: str(databaseId) if databaseId is not None else ''; coerce numeric IDs to string before inserting; schema requires a string, never a number or null>"
    }
  ]
}
```

**ID type discipline:** inline review threads (`reviewThreads.nodes[].id`) yield an opaque GraphQL global node ID → `thread_id_type: "graphql_node_id"`. Top-level PR comments expose a numeric `databaseId` → `"database_id"`. Prefer the thread `id` for inline threads since resolution operates on the thread, not an individual comment. A downstream consumer (relay) keys thread resolution on `thread_id_type`, so it is mandatory on every item.

**Verification Report items** (`source_type: "verification-report"`) have no GitHub thread. Use this shape for their provenance entries:

```json
{
  "thread_id": null,
  "thread_id_type": null,
  "final_class": "change",
  "disposition": null,
  "reply_body": null,
  "reply_url": "<artifact URL from report-url.txt>",
  "fix": "<description of the visual regression to fix, including spec_role, viewport, and candidate_png reference>",
  "original_comment_body": "<judge rationale + reviewer note, as assembled in Step 3f>",
  "original_comment_author": null,
  "thread_database_id": ""
}
```

These items produce a `code` task only (no relay reply task — there is no GitHub thread to post to). `to-tasks` skips the reply-task generation for any provenance entry where `thread_id` is null.

### 8d. Seed stop

Invoke `/to-seed`, passing the full session context plus the provenance block from 8c. `to-seed` writes and validates the seed under `docs/seeds/`.

Before invoking, pass these drafting rules to `/to-seed` along with the context. Check them again on the draft before verification:
1. **Keep conditionality.** If the user or reviewer left a target open ("make sure the stack is configured"), don't flatten it into one concrete branch or PR.
2. **Keep open questions open.** A reviewer question with no answer in the thread goes in as an open question, never as an agreed decision.
3. **Check item numbers.** Any `decisions[]` entry that cites item numbers (overlaps, dependencies) must match the 8c provenance order. Re-read the list before writing the reference.

**Skill boundary:** pr-revise stops after `/to-seed` completes. It does **not** write tasks, invoke `/to-tasks`, `/dispatch-tasks`, `/build-code`, `/relay`, check out a branch, or push. It makes **zero** outward HTTP writes during its own run. The seed is its only output; the downstream tree (`/to-tasks` → `/dispatch-tasks` → build-code + relay) does the rest.

### 8e. Handoff

Then run the managed next-step block below.

---

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `pr-revise`.
<!-- skill-done: pr-revise -->
  - `/to-tasks` — the seed is written and ready to break into code + reply tasks
<!-- learning-capture:end -->
