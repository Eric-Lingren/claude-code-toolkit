# Detector fire drills

`scripts/tests/test_detector_fire_drills.py` runs the attribution and alert pipeline end to end on
fixtures and runs under `run-all-tests.sh` like every other `test_*.py` file. It is the regression
suite for the detectors themselves: if tagging or alerting changes behaviour, a drill fails.

## The pipeline under drill

`scripts/attribution/write-change-records.py` (change records) -> `scripts/learning/log-learning.py`
(`tag-attribution.py` tags the learning; a `regression_of` tag is checked against the real diff by
the judge) -> `scripts/usage/drift-alert.py` (over `correction-rate.py`).

## The drills

| Drill | Expected |
|---|---|
| Regression in the changed section | `regression_of` tag and a verified-regression alert listing the change with its PR link |
| A learning in a different path | `new_defect`, no judge call, no regression alert |
| Recurrence after a fix | `recurrence_of` pointing at the applied learning, no regression alert |
| Rare skill with one learning (under 10 runs), a lone verified `regression_of` | a verified-regression alert listing the tagged change: a tag checked against the real diff alerts however few runs the skill has |
| Rare skill with one learning (under 10 runs), a lone learning that is not a verified `regression_of` (untagged, or the judge rejected the tag) | no alarm: the rate-drift gate (>= 10 runs, before window >= 10 runs, rise >= threshold) still holds |
| Busy week with a normal rate | no alarm; a control test shows a real spike on the same fixtures does alarm |

## Fixtures only

Each drill builds a throwaway git repo (fixture skills, merges, real change records written by
`write-change-records.py`), a throwaway learnings directory (`LOG_LEARNING_DEST`,
`LOG_LEARNING_DOTFILES_DIR`), and throwaway usage snapshots. The judge is a stub
(`LOG_LEARNING_JUDGE_CMD`), the notifier is a stub (`DRIFT_ALERT_NOTIFY_CMD`), and `claude` and
`gh` are poison shims on `PATH` that fail the drill if called. Real bugs are never injected into
live skills, and no real learning row or change record is read or written.

## Every real false alarm or missed regression becomes a new fixture

When a detector raises an alert that was not a real regression (a false alarm), or stays silent on
one that was (a missed regression), do this before changing any detector code:

1. Add a drill to `test_detector_fire_drills.py` that reproduces it, using the `lab` fixture: copy
   the shape of the nearest existing drill and encode the real escape point, change sections, run
   counts and learning counts (anonymised) that fooled the detector.
2. Run it and watch it fail for the right reason.
3. Fix the detector, then confirm the new drill and every existing drill pass.

The drill list only grows. Do not delete or loosen a drill to make a detector change pass.
