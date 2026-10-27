"""Lifecycle tracking for architecture audit recommendation records.

Computes NEW / PERSISTING / RESOLVED labels by diffing current run's
confirmed findings against the prior run's findings.

Key: (signal, location) — same signal at same location = same finding.
"""
import copy


def _key(rec: dict) -> tuple:
    return (rec["signal"], rec["location"])


def compute_lifecycle(current: list, prior: list) -> list:
    """Label findings with lifecycle status.

    Args:
        current: confirmed findings from this run (no lifecycle field yet).
        prior:   findings from the previous run (may or may not have lifecycle).

    Returns:
        Combined list of all findings with "lifecycle" field set:
        - NEW: in current, not in prior
        - PERSISTING: in both current and prior
        - RESOLVED: in prior, not in current
    """
    prior_keys = {_key(r) for r in prior}
    current_keys = {_key(r) for r in current}

    result = []

    for rec in current:
        labeled = copy.deepcopy(rec)
        labeled["lifecycle"] = "PERSISTING" if _key(rec) in prior_keys else "NEW"
        result.append(labeled)

    for rec in prior:
        if _key(rec) not in current_keys:
            labeled = copy.deepcopy(rec)
            labeled["lifecycle"] = "RESOLVED"
            result.append(labeled)

    return result
