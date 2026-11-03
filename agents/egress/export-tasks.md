---
name: export-tasks
description: Export triage tasks from a task file to configured external destinations. Resolves each item's (deliverable, domain) pair against task-routing.json, shows a dry-run table with wall-crossing warnings, then delegates writes to adapter agents. Updates item status and export_url on success. Spawned by dispatch-tasks as an isolated agent.
tools: Read, Write, Bash, Agent
model: sonnet
---

# Export Tasks

Coordinator for exporting triage-typed items from a task file to external destinations. Resolves routing, validates config, presents a dry-run table for approval, then spawns focused adapter agents to execute writes. Updates item status and `export_url` on success.

**Spawn mode:** this agent is spawned by `dispatch-tasks`. It receives the task file path as the sole argument.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version: `"2"`)
**Routing config:** `~/.dotfiles/claude-code-shared/resources/task-routing.json`
**Role:** triage branch coordinator
**Adapters:** `export-tasks-github` (GitHub Issues), `export-tasks-notion` (Notion MCP + API)

## Process

### 1. Load inputs

Read the task file path from the invocation argument.

Read `~/.dotfiles/claude-code-shared/resources/task-routing.json` to get the destinations map.

Filter the task file to items where:
- `task_type` is `triage`
- `status` is `not_started`

If no eligible items remain, print "No triage items to export." and exit cleanly.

### 2. Preflight validation

#### Placeholder check

For each item in the run, resolve its route (see step 3 for flat vs split shape). Collect all Notion routes. For each Notion route, check whether `location` is a known placeholder value (`"personal-notion-db"`, `"ss-notion-db"`).

**If any required Notion location is a placeholder:**

```
ERROR: Notion DB ID not configured for <domain>/<deliverable>.

The location at destinations.<domain>.non-code.location is a placeholder value
('<placeholder>'). export-tasks cannot write to Notion without a real DB ID.

To find your Notion DB ID:
  1. Open Notion and navigate to the target database (not a page — the database itself).
  2. Click "Open as full page" if it's embedded.
  3. Copy the URL. It looks like:
       https://www.notion.so/My-Database-<DATABASE_ID>?v=<view-id>
     The DB ID is the 32-character hex string after the last '-' in the path
     and before the '?'. It may also appear without hyphens as a bare 32-char string.
  4. Edit ~/.dotfiles/claude-code-shared/resources/task-routing.json
  5. Replace the placeholder at destinations.<domain>.non-code.location with the real ID.
  6. Verify: jq '.destinations.<domain>["non-code"].location' ~/.dotfiles/claude-code-shared/resources/task-routing.json

```

Stop immediately if any required Notion DB ID is a placeholder. Do not write anything.

#### Token check

For any `auth=api-token` Notion route, verify the token is set before showing the dry-run:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/egress/notion-check-token.sh
```

If the script exits non-zero, print its stderr output and stop.

### 3. Resolve routing for each item

Destinations have two shapes. Check which shape applies to `item.domain`:

**Flat destination** (e.g. a client domain like `acme`): `destinations[item.domain]` has a top-level `adapter` key. All deliverables route to the same adapter regardless of `deliverable`.

```
route = destinations[item.domain]
adapter = route.adapter   // e.g. "linear"
location = route.location
```

**Split destination** (e.g. `personal`, `spawned-sapien`): `destinations[item.domain]` has `code` and `non-code` sub-keys. Route by `item.deliverable`.

```
route = destinations[item.domain][item.deliverable]
adapter = route.adapter   // "github-issues" or "notion"
location = route.location // "current-repo" or a Notion DB ID
```

Resolve concrete targets:
- **github-issues + location=current-repo:** extract `Org/Repo` from `git remote get-url origin` (strip protocol/host, remove `.git` suffix). Use as `org_repo` in the adapter input.
- **notion:** use `location` as the Notion database ID. Pass `route.auth` as the `auth` field to the adapter.
- **linear:** Linear adapter is deferred past v1. If a `linear` route is resolved, print a warning and skip the item: "Linear adapter not yet implemented. Item skipped."

### 4. Wall-crossing check

The wall rule lives in one script: `~/.dotfiles/claude-code-shared/scripts/egress/wall-check.sh`. Do not restate or re-derive the rule here. For each item, run it with the item's domain as source and the domain whose `task-routing.json` destinations entry supplied the resolved route (step 3) as destination:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/egress/wall-check.sh "<item.domain>" "<route_domain>"
```

Exit-code contract (default mode):
- **exit 0, no output:** pass.
- **exit 0 with a `WARNING:` line on stdout:** soft boundary (e.g. `spawned-sapien` to `personal`). Carry the line into the dry-run table; it does not block.
- **exit 1:** **HARD WALL VIOLATION**. The reason is on stderr. This covers a corporate domain routed outside its own entry (including another corporate domain, so two clients never cross) and any other domain routed into a corporate entry.
- **exit 2 (or any other non-zero):** config or usage error. Treat as a hard violation (fail closed).

Flag violations in the dry-run table.

### 5. Dry-run table

Print the full plan before making any external write. Do not skip.

```
Export plan — docs/tasks/<filename>

 ID      Title                              Adapter        Target                    Deliverable
 ──────  ─────────────────────────────────  ─────────────  ────────────────────────  ───────────
 T-0010  Round-trip read side               notion         <db-id-prefix>...         non-code
 T-0011  Additional runners                 notion         <db-id-prefix>...         non-code
 T-0012  PR for remaining adapters          gh-issue       Eric-Lingren/dotfiles     code
WARNING: T-0013 — domain=acme item (wall=corporate) routed to personal Notion. Hard wall crossing. Remove from run or fix domain.

Review the plan above. Press Enter to proceed or type "abort" to cancel.
```

Hard wall violations show a WARNING line. If any hard wall violation exists, do not proceed until the user either removes the item from scope or corrects the domain. Ask the user to resolve each violation before continuing.

### 6. Execute writes via adapter agents

After user approval, write each item to its adapter in sequence. Spawn the appropriate adapter agent per item and parse the URL from its response.

#### Body assembly (backlink footer)

Before spawning an adapter for each item, build the final body string:

1. Start with `body = item.description`.
2. Read `destinations[item.domain].wall` from `task-routing.json`.
3. If `wall != "corporate"` AND `item.seed_ref` is present AND `item.task_ref` is present, append the backlink footer:

```
body = item.description + "\n\n---\nseed: " + item.seed_ref + "\ntask: " + item.task_ref + "\ntask_id: " + item.id
```

4. If `wall == "corporate"`, use `body = item.description` unchanged. No footer.
5. Pass `body` (not `item.description`) to the adapter prompt.

#### GitHub Issues adapter

Spawn the `export-tasks-github` adapter:

```
Agent(
  subagent_type="export-tasks-github",
  prompt=JSON.stringify({
    "title": item.title,
    "description": body,
    "org_repo": org_repo
  })
)
```

The adapter responds with the issue URL as its sole content, or `ERROR: <reason>` on failure.

#### Notion adapter

Spawn the `export-tasks-notion` adapter:

```
Agent(
  subagent_type="export-tasks-notion",
  prompt=JSON.stringify({
    "db_id": resolved_db_id,
    "title": item.title,
    "description": body,
    "auth": route.auth
  })
)
```

The adapter responds with the page URL as its sole content, or `ERROR: <reason>` on failure.

#### Parsing adapter responses

Extract the URL from the adapter's response text. The adapter outputs exactly one meaningful line: a URL or `ERROR: ...`. If the response starts with `ERROR:`, treat the write as failed.

### 7. Update task file

After each adapter returns, immediately update the task file JSON:
- **Success:** set `item.status` to `"done"`, set `item.export_url` to the returned URL.
- **Failure:** set `item.status` to `"blocked"`, log the error in the end-of-run summary.

Write the updated JSON to disk after each item (do not batch). Continue to the next item on failure — do not abort the whole run.

### 8. End-of-run summary

```
Export complete — docs/tasks/<filename>

 ID      Title                              Result    Export URL
 ──────  ─────────────────────────────────  ────────  ─────────────────────────────────────────
 T-0010  Round-trip read side               done      https://notion.so/page-id-abc
 T-0011  Additional runners                 done      https://notion.so/page-id-def
 T-0012  PR for remaining adapters          done      https://github.com/org/repo/issues/42

Items exported: 3
Items failed: 0
```

<!-- learning-capture:start -->
## Learning Capture

Run this as the FINAL action of this skill's terminal turn, BEFORE printing the
closing suggestion or handoff. Most runs record nothing — only proceed if an
observable correction-event occurred this run.

<!-- learning-eval: export-tasks -->
If a correction-event occurred: identify the `trigger` (tool_failure | backtrack |
user_correction | instruction_gap | redundant_effort | uncategorized), a one-sentence
description of what happened (`brief_evidence`), and `trigger_label` (snake_case if
uncategorized, else null). Spawn the `capture-learning` agent
(`subagent_type: capture-learning`) with: `skill` (this skill's slug: `export-tasks`),
`trigger`, `trigger_label`, `brief_evidence`, `transcript_path` (absolute path to
session transcript). The agent builds the full schema-valid entry, writes if grounded.
**What's next:**
<!-- skill-done: export-tasks -->
  - `/dispatch-tasks` — return to dispatcher for next branch
<!-- learning-capture:end -->
