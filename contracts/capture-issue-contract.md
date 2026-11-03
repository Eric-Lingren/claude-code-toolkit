# capture-issue Contract

**Schema:** `capture-issue-schema.json` (JSON Schema draft-07)

A capture is a quick idea or fix noted mid-work and filed as a GitHub issue. The capture is first written as a `capture-issue` JSON document, validated, then rendered into a fixed four-section markdown issue body. Validation and rendering are one step: `scripts/capture/validate-capture.py`.

**Producers:** capture entry points (the capture skill and shell wrappers) that gather the fields from the user.
**Consumers:** `scripts/capture/validate-capture.py` (validator and renderer), and the code that files the rendered body as a GitHub issue.

---

## Field reference

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string | Yes | Issue title. Must contain a non-whitespace character. |
| `label` | `"idea"` \| `"fix"` | Yes | Issue label. |
| `sealed` | boolean | Yes | `true` for a capture that must carry no location or session context. |
| `what` | string | Yes | What is being captured. 1 to 3 lines. |
| `why` | string | Yes | Why it matters, or what triggered it. |
| `where` | string | No | Where it applies. Must be empty or absent when `sealed` is `true`. |
| `session` | string | No | Session reference. Must be empty or absent when `sealed` is `true`. |

`additionalProperties` is `false`. Unknown fields are rejected.

---

## Sealed invariant

The schema rejects a sealed capture that has a non-empty `where` or `session` (`if sealed == true then where.maxLength == 0 and session.maxLength == 0`). The validator reports this as a plain error naming the offending field.

---

## Rendered body

The validator prints the issue body to stdout. The body has four sections in fixed order:

```
## What
<what>

## Why / trigger
<why>

## Where
<where>

## Session
<session>
```

An unsealed capture renders all four sections. An empty `where` or `session` renders as `n/a`. A sealed capture renders only `What` and `Why / trigger`; the `Where` and `Session` sections are dropped.

---

## Validator usage

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/capture/validate-capture.py <capture.json | ->
```

- Exit 0: valid. Rendered markdown body on stdout.
- Exit 1: invalid, or unreadable input. Errors on stderr, nothing on stdout.

The schema can also be checked with the shared validator, which validates the embedded `examples[]`:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  ~/.dotfiles/claude-code-shared/contracts/capture-issue-schema.json
```
