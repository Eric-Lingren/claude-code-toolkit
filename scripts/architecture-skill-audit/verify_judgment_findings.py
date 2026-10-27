#!/usr/bin/env python3
"""Majority-vote aggregation for architecture audit judgment findings.

Deterministic signals (A1, A2, A7): pass through unconditionally.
Judgment signals (A3, A4, A5, A6): require majority (>=2/3) panel confirmation.
"""

JUDGMENT_SIGNALS = {"A3", "A4", "A5", "A6"}
DETERMINISTIC_SIGNALS = {"A1", "A2", "A7", "A8"}


def is_judgment_signal(signal: str) -> bool:
    return signal in JUDGMENT_SIGNALS


def aggregate_panel_votes(finding: dict, votes: list) -> dict:
    """Aggregate 3 panel votes into a confirmed/dropped result.

    Args:
        finding: structured finding record from the auditor
        votes: list of dicts with {"confirmed": bool, "reason": str}
                Pass [] for deterministic signals (they auto-confirm).

    Returns:
        {
            "confirmed": bool,
            "finding": <original finding>,
            "vote_count": int,
            "confirm_count": int,
        }
    """
    signal = finding.get("signal", "")

    if not is_judgment_signal(signal):
        return {
            "confirmed": True,
            "finding": finding,
            "vote_count": len(votes),
            "confirm_count": len(votes),
        }

    confirm_count = sum(1 for v in votes if v.get("confirmed", False))
    majority = confirm_count >= 2

    return {
        "confirmed": majority,
        "finding": finding,
        "vote_count": len(votes),
        "confirm_count": confirm_count,
    }
