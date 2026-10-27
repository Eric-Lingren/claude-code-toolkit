# Architecture Audit Steps

Run on every invocation. No opt-out.

## 6a: Spawn the auditor

Spawn `Agent(subagent_type: "architecture-auditor")` with:
- `TARGET_SKILL_PATH`: absolute path to the target skill's SKILL.md
- `REGISTRY_PATH`: `~/.dotfiles/claude-code-shared/agents/registry.json`
- `SHARED_ROOT`: `~/.dotfiles/claude-code-shared`

The auditor returns a JSON array of raw findings (A1–A7). Capture it.

## 6b: Split verification

**Deterministic signals (A1, A2, A7, A8):** Accept directly. No panel needed.

**Judgment signals (A3, A4, A5, A6):** For each raw finding, spawn 3 judges (subagent_type: "general-purpose", model: "haiku") in parallel using the template in `resources/architecture-panel-prompt.md`. Pass each judge the finding + ~20-line excerpt from the skill around the flagged location.

After all 3 judges reply, call:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/architecture-skill-audit/verify_judgment_findings.py
```
Or apply `aggregate_panel_votes(finding, votes)` from `verify_judgment_findings.py` directly.
Confirmed if `confirm_count >= 2`. Drop unconfirmed findings.

## 6c: Score (session model)

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/architecture-skill-audit/architecture_score.py
```
Or call `findings_to_signal_results(confirmed_findings)` then `calculate_architecture_score(signal_results)` from `architecture_score.py`.

`architecture_score = confirmed_passed / 8 * 100`

A signal PASSES if no confirmed finding fired for it. Every signal starts as pass; a finding demotes it to fail.

Cross-skill signals (A5, A6) read `registry.json` as the corpus cache. Do not scan the entire skills tree.

## 6d: Lifecycle tracking

Load the prior run's `architecture.recommendations` from `<runs-dir>/scores.json` (the most recent run entry, if any). Pass it with the current run's confirmed findings to:

```python
from lifecycle_tracking import compute_lifecycle
labeled = compute_lifecycle(current_confirmed_findings, prior_recommendations)
```

Or call `~/.dotfiles/claude-code-shared/scripts/architecture-skill-audit/lifecycle_tracking.py` via Bash.

Use `labeled` as the recommendations array for scores.json and the report. Findings carry NEW, PERSISTING, or RESOLVED. All three lifecycle states appear in the Architecture report section — RESOLVED findings confirm debt is cleared.

## 6e: Architecture is a separate pillar

The architecture pillar is independent of the behavioral assertion matrix. No behavioral assertions are added or affected. It produces its own `architecture_score` alongside `behavioral_score` and `structural_score`.
