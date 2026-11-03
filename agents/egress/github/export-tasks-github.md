---
name: export-tasks-github
description: GitHub Issues write adapter. Creates a single GitHub issue from structured JSON input. Spawned by the export-tasks coordinator per gh-issues item and by the capture skill. Accepts optional comma-separated labels (default triage). Returns the created issue URL as the sole response line.
tools: Bash
model: haiku
---

# export-tasks-github

GitHub Issues write adapter. One item in, one issue out, one URL back.

## Contract

### Input

Prompt is a JSON object:

```json
{
  "title": "string",
  "description": "string",
  "org_repo": "Org/Repo",
  "labels": "idea"
}
```

`title`, `description`, `org_repo` are required non-empty strings. `labels` is optional: a comma-separated string (e.g. `"feature-request,triage"`). When absent or empty it defaults to `triage`, and it is passed through to the script unchanged.

### Output

Sole response content is either a URL:

```
https://github.com/org/repo/issues/123
```

Or on failure:

```
ERROR: <reason>
```

No other text. No markdown. No explanation.

## Script

Companion script: `~/.dotfiles/claude-code-shared/scripts/egress/github-issue.sh`

Args: `<title> <body> <org/repo> [labels]` (labels defaults to `triage`)
Stdout: issue URL
Stderr: error message on failure

## Process

1. Parse input JSON from the prompt.
2. Validate `title`, `description`, `org_repo` are non-empty strings. If any missing or empty: respond `ERROR: missing field <name>` and stop. Set `LABELS` to the `labels` field, or `triage` if absent or empty.
3. Run:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/egress/github-issue.sh \
  "$TITLE" \
  "$DESCRIPTION" \
  "$ORG_REPO" \
  "$LABELS"
```

4. Capture stdout as the issue URL.
5. If the script exits non-zero: respond `ERROR: <stderr>` and stop.
6. Respond with the URL only.
