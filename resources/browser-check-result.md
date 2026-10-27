---
name: browser-check-result
version: "2"
description: JSON contract returned by the browser-checker agent to its caller (build-runner, debug). v2 adds visual evidence per capture (Baseline/Candidate PNGs, diff ratio, heatmap, visual verdict) and replaces assertions[] with step_results[]. Schema: contracts/browser-check-result-schema.json.
---

# Browser Check Result Contract (v2)

The browser-checker agent returns a single JSON object on stdout. All consumers (build-runner, debug) parse this contract. Do not define the schema inline in skill or agent files.

**Schema:** `contracts/browser-check-result-schema.json`

## What changed from v1

v1 had a single `screenshot` field (one PNG, taken on failure). v2 adds `captures[]` with per-step Baseline/Candidate PNG pairs, diff ratio, diff heatmap, and an optional visual verdict. The `assertions[]` field is renamed `step_results[]` (same shape: `description/passed/detail`). The `screenshot` field is removed (evidence lives in `captures[].candidate_png`). A `version: "2"` discriminator is added.

## Schema

```json
{
  "version": "2",
  "status": "pass" | "fail" | "skipped",
  "url": "string — full URL that was checked (base_url + first goto path)",
  "captures": [
    {
      "name": "string — capture name from the spec's capture step",
      "viewport": "string — viewport name (e.g. 'desktop', 'mobile')",
      "baseline_png": "string | null — absolute path to Baseline PNG; null on first run (no cached baseline)",
      "candidate_png": "string | null — absolute path to Candidate PNG; null only on skipped",
      "diff_ratio": "number | null — fraction of differing pixels (0.0–1.0); null when either PNG is absent",
      "diff_heatmap": "string | null — absolute path to diff heatmap PNG; null when either PNG is absent",
      "visual_verdict": {
        "verdict": "expected" | "regression" | "unexpected" | "uncertain",
        "rationale": "string",
        "viewport": "string",
        "capture_ref": "string",
        "panel_votes": [{ "model": "string", "verdict": "...", "rationale": "string" }]
      } | null
    }
  ],
  "step_results": [
    {
      "description": "string — human-readable step description",
      "passed": true | false,
      "detail": "string | null — failure detail or DOM excerpt; null on pass"
    }
  ],
  "console_errors": ["string — each console.error / unhandled rejection captured during the run"],
  "artifacts_dir": "string | null — absolute path to the run dir (docs/browser-checks/YYYYMMDD-HHMM-<slug>/); null on clean pass with no kept evidence",
  "skipped_reason": "string | null — human-readable reason when status is skipped; null otherwise"
}
```

## Field definitions

| Field | Always present | Notes |
|---|---|---|
| `version` | yes | Always `"2"`. |
| `status` | yes | `"pass"`, `"fail"`, or `"skipped"`. |
| `url` | yes | Full URL: `base_url + first goto path`. |
| `captures` | yes | One entry per capture step per viewport. Empty on skipped runs. |
| `captures[].name` | yes | Matches the `capture` step's `name` field in the Check Spec. |
| `captures[].viewport` | yes | Matches a viewport `name` from the Check Spec. |
| `captures[].baseline_png` | yes | `null` on first run for this spec/SHA/role/viewport (no cached baseline yet). |
| `captures[].candidate_png` | yes | `null` only on skipped. |
| `captures[].diff_ratio` | yes | `null` when either PNG is absent. Signal only — never a pass/fail gate. |
| `captures[].diff_heatmap` | yes | `null` when either PNG is absent. |
| `captures[].visual_verdict` | yes | `null` for zero-diff shortcut, missing baseline, or skipped. See visual-verdict contract. |
| `step_results` | yes | One entry per executed step (goto, click, fill, waitFor, capture, expect). |
| `console_errors` | yes | Empty array if none captured. |
| `artifacts_dir` | yes | `null` on clean pass (expected with `expected_visual_change: none`). Non-null when any evidence is kept or on fail/skipped. |
| `skipped_reason` | yes | `null` unless `status` is `"skipped"`. |

## Statuses

### `"pass"`

All `step_results` passed and no `regression` visual verdict was reached on a desktop viewport. Evidence for `expected` captures with `expected_visual_change: none` is discarded and `artifacts_dir` is null. Evidence for intentional changes, unexpected, or uncertain is kept.

**Caller behavior:** task may proceed. Check `captures[].visual_verdict` — any `unexpected` or `uncertain` verdict sets `needs_eyes` on the task but does not block it.

### `"fail"`

One or more `step_results` failed, or a `regression` visual verdict was returned for a desktop capture.

**Caller behavior:**
1. Log the failing `step_results`, `console_errors`, and any `regression` verdict rationale.
2. Attempt to fix the source code.
3. Re-spawn browser-checker with the same spec.
4. Repeat up to a hard cap of 3 total attempts. Bail early if two consecutive runs produce identical failures (no-progress detection).
5. On cap or no-progress bail: mark task `blocked`, pause for HITL. Surface: failing `step_results`, the regression rationale, iteration log, and `artifacts_dir`.

### `"skipped"`

Playwright was unavailable or the server could not be reached after the startup timeout.

**Caller behavior:** do not block the task. Report `skipped_reason` in the run summary and move on. Skipped is not a failure.

---

## Evidence retention policy

| Capture verdict | `expected_visual_change` | Evidence kept? |
|---|---|---|
| `expected` | `none` | No — `artifacts_dir` null on clean pass |
| `expected` | `present` | Yes — intentional change documented |
| `unexpected` | any | Yes — needs review |
| `uncertain` | any | Yes — needs review |
| `regression` | any | Yes — blocking evidence |
| No verdict (no baseline) | any | Candidate PNG kept for next run to cache as baseline |

---

## Example payloads

### Pass — first run, no baseline cached

```json
{
  "version": "2",
  "status": "pass",
  "url": "http://localhost:5173/dashboard",
  "captures": [
    {
      "name": "dashboard-initial",
      "viewport": "desktop",
      "baseline_png": null,
      "candidate_png": "/Users/eric/.cache/claude-browser-verify/run-abc/desktop-dashboard-initial-candidate.png",
      "diff_ratio": null,
      "diff_heatmap": null,
      "visual_verdict": null
    }
  ],
  "step_results": [
    {"description": "goto /dashboard", "passed": true, "detail": null},
    {"description": "waitFor networkidle", "passed": true, "detail": null},
    {"description": "capture dashboard-initial", "passed": true, "detail": null}
  ],
  "console_errors": [],
  "artifacts_dir": null,
  "skipped_reason": null
}
```

### Pass — zero diff, expected_visual_change: none

```json
{
  "version": "2",
  "status": "pass",
  "url": "http://localhost:5173/dashboard",
  "captures": [
    {
      "name": "dashboard-initial",
      "viewport": "desktop",
      "baseline_png": "/Users/eric/.cache/claude-browser-verify/baseline/desktop-dashboard-initial.png",
      "candidate_png": "/Users/eric/.cache/claude-browser-verify/run-def/desktop-dashboard-initial-candidate.png",
      "diff_ratio": 0.0,
      "diff_heatmap": "/Users/eric/.cache/claude-browser-verify/run-def/desktop-dashboard-initial-heatmap.png",
      "visual_verdict": null
    }
  ],
  "step_results": [
    {"description": "goto /dashboard", "passed": true, "detail": null},
    {"description": "waitFor networkidle", "passed": true, "detail": null},
    {"description": "capture dashboard-initial", "passed": true, "detail": null}
  ],
  "console_errors": [],
  "artifacts_dir": null,
  "skipped_reason": null
}
```

### Fail — step failure and desktop regression

```json
{
  "version": "2",
  "status": "fail",
  "url": "http://localhost:5173/dashboard",
  "captures": [
    {
      "name": "settings-panel-open",
      "viewport": "desktop",
      "baseline_png": "/Users/eric/.cache/claude-browser-verify/baseline/desktop-settings-panel-open.png",
      "candidate_png": "/Users/eric/project/docs/browser-checks/20260930-1200-dashboard/desktop-settings-panel-open-candidate.png",
      "diff_ratio": 0.142,
      "diff_heatmap": "/Users/eric/project/docs/browser-checks/20260930-1200-dashboard/desktop-settings-panel-open-heatmap.png",
      "visual_verdict": {
        "verdict": "regression",
        "rationale": "Nav bar collapses on desktop. Sidebar overlaps main content.",
        "viewport": "desktop",
        "capture_ref": "settings-panel-open",
        "panel_votes": [
          {"model": "claude-sonnet-4-5", "verdict": "regression", "rationale": "Sidebar overlap is unintended."},
          {"model": "claude-sonnet-4-5", "verdict": "regression", "rationale": "Layout is broken on desktop."},
          {"model": "claude-sonnet-4-5", "verdict": "unexpected", "rationale": "Unclear if intentional."}
        ]
      }
    }
  ],
  "step_results": [
    {"description": "goto /dashboard", "passed": true, "detail": null},
    {"description": "waitFor networkidle", "passed": true, "detail": null},
    {"description": "click \"Open settings\"", "passed": true, "detail": null},
    {"description": "expect role=dialog visible", "passed": false, "detail": "Element with role 'dialog' not found after 5000ms."}
  ],
  "console_errors": [
    "TypeError: Cannot read properties of undefined (reading 'open') at SettingsPanel.tsx:88"
  ],
  "artifacts_dir": "/Users/eric/project/docs/browser-checks/20260930-1200-dashboard",
  "skipped_reason": null
}
```

### Skipped

```json
{
  "version": "2",
  "status": "skipped",
  "url": "http://localhost:5173/dashboard",
  "captures": [],
  "step_results": [],
  "console_errors": [],
  "artifacts_dir": null,
  "skipped_reason": "Playwright module not found in project node_modules or global install"
}
```
