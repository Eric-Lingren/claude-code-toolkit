---
name: verdict-contract
description: Contract for judge agent output. The judge returns a JSON array of verdicts, one per refutation in the batch it received. Single source of truth for orchestrator parsing and judge instructions.
---

# Verdict Contract

**Schema:** `verdict-schema.json`
**Producer:** persona-judge agent (batched — 1 round-1 screener call, then 3 round-2 panel calls over the upheld subset)
**Consumer:** to-seed verification stage orchestrator

## Output rule

**Judges output only JSON, never prose or questions.** The entire response must be a single valid JSON array. No preamble, no explanation, no markdown fences.

## Batched adjudication

The judge no longer adjudicates one refutation per invocation. It receives the full batch of refutations (each with a stable `ref_id`) plus a windowed evidence pack, and returns one verdict per `ref_id`. This collapses what was previously `N + 3·upheld` judge spawns into at most `1 + 3` spawns per run.

## Normal form

```json
[
  {"ref_id": "r0", "verdict": "upheld", "reason": "The cited span at the r0 pack section contradicts decisions[2]."},
  {"ref_id": "r1", "verdict": "rejected", "reason": "Pack section r1 is SPAN NOT FOUND; the claim is unsupported."}
]
```

`verdict` must be exactly `"upheld"` or `"rejected"`. No other values are valid. The array must contain exactly one object per `ref_id` the judge received, preserving the ids.

## Error form

On unrecoverable failure, the judge returns a single-element array carrying an error object:

```json
[
  {"error": "could not read evidence pack", "details": "path /tmp/evidence-pack-... does not exist"}
]
```

## Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `ref_id` | string | yes (normal) | Stable id of the refutation being judged, echoed from input |
| `verdict` | string | yes (normal) | `"upheld"` or `"rejected"` |
| `reason` | string | yes (normal) | One sentence citing the specific span or its absence |
| `error` | string | yes (error) | Short description of the failure |
| `details` | string | no (error) | Optional additional context |

## Adjudication

The escalation ladder is unchanged in spirit, only batched:

- **Round 1 (screener):** one judge call over all refutations. Each refutation with verdict `rejected` is terminated (counted in `refutations_screened`). Each `upheld` escalates.
- **Round 2 (panel):** three fresh judge calls, each over the upheld subset. Apply a flat 2-of-3 majority per `ref_id`:
  - 2 or 3 `upheld` → refutation upheld; apply it to the draft seed
  - 2 or 3 `rejected` → refutation rejected; no change
  - If 2 or more judge calls return error-form arrays, the whole round is degraded; record the failure

The orchestrator validates each verdict against this schema. On a parse mismatch, it retries that judge call once.
