---
name: investigate
description: >
  Investigates a question, claim, external signal, or incident by routing it to
  the investigator orchestrator agent (Opus tier), which decomposes it into
  sub-claims, routes each to the appropriate source, and returns a schema-valid
  investigation-result with a unified verdict and merged evidence.
model: haiku
effort: low
invokedBy: human
---

## Step 0 — Parse flags and resolve depth

### --out flag

Parse `--out` from the user's args before doing anything else.

**Accepted values:** `raw`, `slack`
**Default:** `slack`

```
--out raw    Return the investigation-result JSON directly, without voicing.
--out slack  Route the result through answer-composer for a voiced Slack reply (default).
```

Strip `--out <value>` from the args before passing the question or claim to the investigator.

When another skill calls investigate from the terminal (for example, pr-code-review scope checks), the caller should pass `--out raw` and read the structured result itself. A caller that wants a Slack draft passes `--out slack`. Do not guess the caller when `--out` is omitted. The default stays `slack`.

If `--out` is set to a value other than `raw` or `slack`, stop and tell the user the valid options.

### Depth

Check whether `$ARGUMENTS` contains the explicit word `fast` or `deep` (case-insensitive, whole-word match against the flag list):

- If `fast` is present → set `depth = fast`. Strip the word from the args.
- If `deep` is present → set `depth = deep`. Strip the word from the args.
- If neither is present → ask:

```
AskUserQuestion({
  header: "Depth",
  options: [
    {
      label: "Fast",
      description: "Literal answer only — verdict + evidence, skips adjacent-facts, fewer spawns, quicker."
    },
    {
      label: "Deep",
      description: "Full investigation + adjacent facts, more spawns, slower."
    }
  ]
})
```

Set `depth` to whichever option the user selects (`fast` or `deep`).

## Step 1 — Input guard (JSON vs prose detection)

Before doing anything else with the cleaned input (after stripping `--out`), check whether it is a pre-existing investigation result.

**Rule:** if the input starts with `{`, attempt to parse it as JSON and validate it against the `investigation-result` schema (`contracts/investigation-result-schema.json`). Three outcomes:

| Input shape | Treatment |
|---|---|
| Valid `investigation-result` JSON (schema_version, verdict, evidence all present and valid) | **Skip investigation.** Pass the result directly to Step 4 (voicing). |
| Starts with `{` but fails schema validation (missing required fields, wrong types, etc.) | **Treat as prose.** Continue to Step 2 (link detection) and then Step 3 (investigator). |
| Does not start with `{` (plain prose, URL, question) | **Treat as prose.** Continue to Step 2 (link detection) and then Step 3 (investigator). |

**Validation checklist for schema-valid investigation-result JSON:**
- Top-level object with `schema_version` field equal to `"1"`
- `verdict` field present and set to one of the four valid verdicts:
  - `VERIFIED_TRUE`
  - `VERIFIED_FALSE`
  - `INSUFFICIENT_EVIDENCE`
  - `CONTESTED`
- `evidence` field is an array (empty arrays are valid; required to be non-empty only for `VERIFIED_TRUE` and `VERIFIED_FALSE`)

If the input passes all three checks, it is schema-valid. Do not re-investigate it — use it as-is for voicing.

If the input fails any check (or is not JSON at all), treat it as prose and proceed to Step 2.

## Step 2 — Detect link input

If the user's input is (or contains) a URL, fetch the content first to derive the question or claim.

**Slack permalink** (`https://app.slack.com/...` or `https://*.slack.com/archives/...`):
- Use `mcp__claude_ai_Slack__slack_read_thread` with the channel and message timestamp extracted from the URL.
- Slack permalink format: `.../archives/<channel_id>/p<ts_digits>` where ts = `<10digits>.<6digits>`.
- Read the thread. Summarise the core question or claim being asked. That summary becomes the investigation input.

**GitHub URL** (issue, PR, comment):
- Use `gh issue view` or `gh pr view` via Bash to fetch the body and comments.
- Extract the core question or claim. That becomes the investigation input.

**Generic URL**:
- Use `WebFetch` to retrieve the page content.
- Extract the core question or claim from the content.

If the input is already a plain question or claim (no URL), skip Step 2 entirely.

## Step 3 — Spawn the investigator

Use the Agent tool with `subagent_type: investigator`. Pass the derived (or original) question or claim. Include `cwd` if the investigation involves codebase claims. Always append `depth: <value>` (resolved in Step 0) to the prompt string.

When `depth == fast`, also pass `model: "sonnet"` to override the default Opus tier. Fast mode skips adjacent_facts and runs on Sonnet, making it meaningfully quicker. Deep mode uses the default (Opus) and no model override is needed.

**Example — question form (fast):**

```
Agent({
  description: "Investigate: Was the egress hook regression introduced in PR #204?",
  subagent_type: "investigator",
  model: "sonnet",
  prompt: "question: Was the egress hook regression introduced in PR #204?\ncwd: /path/to/repo\ndepth: fast"
})
```

**Example — claim form (deep):**

```
Agent({
  description: "Investigate: The feature flag was disabled before the incident on 2026-08-10.",
  subagent_type: "investigator",
  prompt: "claim: The feature flag was disabled before the incident on 2026-08-10.\ndepth: deep"
})
```

**Example — Slack link input:**

User pastes `https://acme.slack.com/archives/C012AB3CD/p1723660012345678`.
1. Parse: channel=`C012AB3CD`, ts=`1723660012.345678`.
2. Call `slack_read_thread` to fetch thread content.
3. Derive question from thread (e.g., "Was the deploy on 2026-08-14 the cause of the latency spike?").
4. Pass derived question to investigator with the resolved `depth` value appended.

## Step 4 — Route output based on --out flag

After the investigator returns its `investigation-result` (or when Step 1 passes a pre-existing result directly here), branch on the `--out` value resolved in Step 0.

### --out raw

Return the `investigation-result` JSON directly to the caller. Do not spawn any additional agent. This applies both when the investigator produced the result (Step 3) and when Step 1 short-circuited directly to this step.

### --out slack (default)

Pass the `investigation-result` to `answer-composer` for voicing. This applies both when the investigator produced the result (Step 3) and when Step 1 short-circuited directly to this step.

```
Agent({
  description: "Voice investigation result as Slack reply",
  subagent_type: "answer-composer",
  prompt: "investigation_result: <paste investigation-result JSON here>\nquestion: <original question>"
})
```

Return the voiced Slack reply from `answer-composer` to the caller.

While `answer-composer` runs, do not summarize the findings yourself. Emit at most a one-line
status, such as "Drafting the Slack reply." When it returns, relay its output as the reply.
Do not add a separate summary before or after it. If the composer fails or returns nothing,
say so in one line and offer the raw `investigation-result` instead.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `investigate`.
<!-- skill-done: investigate -->
<!-- learning-capture:end -->
