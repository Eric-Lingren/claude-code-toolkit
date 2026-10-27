---
name: visual-verdict
description: Vision-model judgment of whether a Baseline-to-Candidate visual change matches intent. Produced by visual-judge per (capture, viewport) pair. Embedded in browser-check-result v2 captures[].
---

# Visual Verdict Contract

**Schema:** `contracts/visual-verdict-schema.json`

A Visual Verdict is the vision-model judgment produced by the `visual-judge` agent for one (capture, viewport) pair. It is not a pixel-diff threshold gate — it is a model-reasoned determination of whether the Baseline-to-Candidate change is intentional, a regression, or ambiguous (ADR-0004).

Verdicts are embedded in `browser-check-result` v2 `captures[].visual_verdict` and surfaced in the Verification Report.

## Producers

- `agents/build/visual-judge.md` — Sonnet-tier agent. Runs screener + optional 3-judge panel. Writes one verdict per (capture, viewport) pair.

## Consumers

- `browser-checker` agent — embeds verdicts in `browser-check-result` v2 `captures[]`
- `build-runner` step 4 — routes verdict: `regression` on desktop blocks; `regression` on mobile → `needs_eyes`; `unexpected` or `uncertain` → `needs_eyes` (never blocks)
- Verification Report artifact — displays verdict per capture with Baseline/Candidate pair and rationale

## Schema file

[visual-verdict-schema.json](visual-verdict-schema.json)

## Verdict enum

| Value | Meaning | Caller behavior |
|---|---|---|
| `expected` | Change matches declared intent or zero-diff shortcut applied. | Task proceeds. Evidence discarded if `expected_visual_change` was `none`; kept if `present`. |
| `regression` | Unintended change detected. On desktop: blocks task; rationale fed into retry loop. On mobile: downgraded to `needs_eyes`. | Desktop: block. Mobile: `needs_eyes`. |
| `unexpected` | Change present but not a clear regression (e.g. partial or ambiguous diff). | `needs_eyes` — never blocks. |
| `uncertain` | Panel could not reach 2-of-3 majority. | `needs_eyes` — never blocks. |

## Judging flow

```
diff_ratio < noise_threshold AND expected_visual_change == "none"
  └─► zero-diff shortcut → verdict: expected (no model call)

otherwise
  └─► screener (1 Sonnet judge)
        ├─ "expected" → verdict: expected  (screener-only panel_votes: 1 entry)
        └─ non-expected → 3-Sonnet panel (2-of-3 majority)
              ├─ 2-of-3 agree → majority verdict
              └─ no majority → verdict: uncertain
```

The rubric is delta-only: flaws already present in the Baseline are not findings.

## Field summary

| Field | Required | Notes |
|---|---|---|
| `verdict` | yes | `expected`, `regression`, `unexpected`, or `uncertain`. |
| `rationale` | yes | Majority rationale (or screener rationale for single-vote path). Fed into retry loop on `regression`. |
| `viewport` | yes | Viewport name from the Check Spec (e.g. `desktop`, `mobile`). |
| `capture_ref` | yes | Capture name from the Check Spec `capture` step. |
| `panel_votes` | yes | 1 entry (screener-only) or 3 entries (full panel). |

## Example: expected (screener-only)

```json
{
  "verdict": "expected",
  "rationale": "Button color changed from gray to brand blue. Diff confined to primary action button. No structural changes.",
  "viewport": "desktop",
  "capture_ref": "dashboard-initial",
  "panel_votes": [
    {
      "model": "claude-sonnet-4-5",
      "verdict": "expected",
      "rationale": "Color-only change matching the declared expected_visual_change: present."
    }
  ]
}
```

## Example: regression (3-judge panel, 2-of-3)

```json
{
  "verdict": "regression",
  "rationale": "Nav bar collapses on desktop 1440px. Sidebar overlaps main content. Two of three judges agree this is an unintended layout regression.",
  "viewport": "desktop",
  "capture_ref": "settings-panel-open",
  "panel_votes": [
    {"model": "claude-sonnet-4-5", "verdict": "regression", "rationale": "Sidebar overlap is clearly unintended. Content is obscured."},
    {"model": "claude-sonnet-4-5", "verdict": "regression", "rationale": "Desktop layout is broken. Sidebar was absent in Baseline."},
    {"model": "claude-sonnet-4-5", "verdict": "unexpected", "rationale": "Layout changed but unclear if intentional without more context."}
  ]
}
```
