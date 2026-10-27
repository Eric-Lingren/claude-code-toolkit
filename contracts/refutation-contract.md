---
name: refutation-contract
description: Contract for adversary persona output. Each persona returns a JSON array of refutation objects. Single source of truth for orchestrator parsing and persona instructions.
---

# Refutation Contract

**Schema:** `refutation-schema.json`
**Producer:** adversary persona agents (persona-grounding, persona-accuracy, persona-completeness, persona-coherence)
**Consumer:** to-seed verification stage orchestrator

## Output rule

**Personas output only JSON, never prose or questions.** The entire response must be a valid JSON array. No preamble, no explanation, no markdown fences.

## Normal form

A persona that finds refutations returns a JSON array of one or more refutation objects:

```json
[
  {
    "persona": "grounding",
    "field": "decisions[2]",
    "claim": "exact text of the claim being challenged",
    "problem": "one sentence: what is unsupported or fabricated",
    "transcript_span": "exact quote from transcript that shows the gap, or null if no span exists"
  }
]
```

A persona that finds nothing returns an empty array:

```json
[]
```

## Error form

On unrecoverable failure (e.g. transcript file not found, unreadable input), the persona returns a JSON array containing a single error object instead of raising or printing free text:

```json
[
  {
    "persona": "grounding",
    "error": "transcript file not found: /path/to/session.jsonl",
    "details": "CLAUDE_CODE_SESSION_ID=abc123, encoded path resolved to /Users/eric/.claude/projects/..."
  }
]
```

`details` is optional. `error` is required and must be a short description, not a stack trace.

## Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `persona` | string | yes | One of: `grounding`, `accuracy`, `completeness`, `coherence` |
| `field` | string | yes (normal) | Dot-path to the seed field being challenged, e.g. `decisions[2]` |
| `claim` | string | yes (normal) | Exact text of the challenged claim |
| `problem` | string | yes (normal) | One sentence describing what is wrong |
| `transcript_span` | string or null | yes (normal) | Verbatim quote from transcript, or null if the problem is absence |
| `error` | string | yes (error) | Short description of the failure |
| `details` | string | no (error) | Optional additional context |

### Synthesized content

Synthesized or inferred seed content often draws on several transcript locations. Never stitch separate fragments into one quote. A stitched quote is not verbatim, so the judge rejects it even when every piece exists. Cite the single strongest verbatim span in `transcript_span`. Name the other locations in `problem`.

## Orchestrator handling

The orchestrator validates each returned array against this schema. On a parse mismatch, it retries the persona once. If the retry also fails to return valid JSON, the orchestrator records the failure and continues (the verification stamp is set to `degraded`).

The orchestrator detects error-form objects by checking for the presence of the `error` key (absent in normal-form objects). Error-form objects are never forwarded to the judge stage.
