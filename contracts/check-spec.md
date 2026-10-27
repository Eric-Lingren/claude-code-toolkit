---
name: check-spec
description: Declarative definition of one browser check. Executed identically against Baseline and Candidate by browser-verify.mjs. Consumed by visual-judge and browser-checker.
---

# Check Spec Contract

**Schema:** `contracts/check-spec-schema.json`

A Check Spec is the unit of browser verification. It fully describes one repeatable check: the authenticated role, the viewports to render, the ordered interaction steps, any dynamic regions to mask, and whether a visual change is expected. The script `browser-verify.mjs` executes it deterministically against both the Baseline (merge-base render) and the Candidate (feature branch render).

Check Specs are ephemeral. They live in excluded `docs/browser-checks/` scaffolding and are never committed to target repos (ADR-0003).

## Producers

- `skills/to-tasks/` — authors a full Check Spec into `browser_verify` when the task has UI impact (Planned check)
- `agents/check-spec-deriver.md` — infers a Check Spec from FE file or API response diffs when no Planned check exists (Derived check; Phase 2)

## Consumers

- `scripts/build/browser-verify.mjs` — executes the spec against a given `base_url` and `storageState`
- `browser-checker` agent — orchestrates `browser-verify.mjs` for Baseline and Candidate and assembles the `browser-check-result` v2
- `visual-judge` agent — receives the spec's `expected_visual_change` field to apply the zero-diff shortcut

## Schema file

[check-spec-schema.json](check-spec-schema.json)

## Field summary

| Field | Required | Notes |
|---|---|---|
| `role` | yes | Named identity from the repo's Auth Profile (e.g. `admin`, `firm`, `anonymous`). |
| `viewports` | yes | One or more `{name, width, height}` entries. Default: desktop 1440×900 and mobile 390×844. |
| `steps` | yes | Ordered steps. Must include at least one `goto` and one `capture`. |
| `masks` | no | CSS selectors for dynamic regions (timestamps, avatars) to blank before diffing. |
| `expected_visual_change` | no | `"none"`, `"present"`, or `null` (derived/unknown). |

## Step types

| Type | Required fields | Description |
|---|---|---|
| `goto` | `url` | Navigate to a URL. Relative paths are resolved against `base_url`. |
| `click` | `locator` | Click an element. Use role/text locators for resilience. |
| `fill` | `locator`, `value` | Fill a form field. |
| `waitFor` | `condition` | Wait for `networkidle`, `load`, `domcontentloaded`, or a CSS selector to appear. |
| `capture` | `name` | Screenshot both Baseline and Candidate at this point. `name` must be unique within the spec. |
| `expect` | `locator`, `assertion` | Assert `visible`, `hidden`, `text`, or `count`. Failure always blocks the task. |

## Viewport defaults

Per the seed decisions, default viewports are:

```json
[
  {"name": "desktop", "width": 1440, "height": 900},
  {"name": "mobile",  "width": 390,  "height": 844}
]
```

Tablet is added only when the spec explicitly requests it. Dark mode only when the app supports it and the diff touches styles/tokens. Per-repo viewport overrides live in `repo-policy.json`.

## expected_visual_change semantics

| Value | Behavior |
|---|---|
| `"none"` | Zero-diff shortcut: if `diff_ratio` is under the noise threshold, verdict is `expected` with no model call. |
| `"present"` | A deliberate visual change is expected. The judge evaluates the delta to confirm it matches intent. |
| `null` | Derived check — intent is unknown. Any visible change resolves to `needs_eyes`, never `expected`. |

## Example

```json
{
  "role": "admin",
  "viewports": [
    {"name": "desktop", "width": 1440, "height": 900},
    {"name": "mobile",  "width": 390,  "height": 844}
  ],
  "steps": [
    {"type": "goto",    "url": "/dashboard"},
    {"type": "waitFor", "condition": "networkidle"},
    {"type": "capture", "name": "dashboard-initial"},
    {"type": "click",   "locator": "role=button[name=\"Open settings\"]"},
    {"type": "waitFor", "condition": ".settings-panel"},
    {"type": "expect",  "locator": "role=dialog", "assertion": "visible"},
    {"type": "capture", "name": "settings-panel-open"}
  ],
  "masks": [
    {"selector": ".user-avatar"},
    {"selector": ".last-login-timestamp"}
  ],
  "expected_visual_change": "present"
}
```
