"""Architecture audit scoring utilities for the improve-skill-benchmarks Architecture pillar.

Signals A1-A8: 8-signal denominator as of the A8 addition.
"""

ALL_SIGNALS = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]


def findings_to_signal_results(confirmed_findings: list) -> dict:
    """Convert a list of confirmed findings into a per-signal pass/fail map.

    A signal PASSES (True) if no confirmed finding fired for it.
    A signal FAILS (False) if at least one confirmed finding fired for it.

    Args:
        confirmed_findings: list of finding dicts that survived panel confirmation.
                            Each must have a "signal" key.
    Returns:
        dict mapping each signal A1-A7 to True (pass) or False (fail).
    """
    fired = {f["signal"] for f in confirmed_findings}
    return {s: s not in fired for s in ALL_SIGNALS}


def calculate_architecture_score(signal_results: dict) -> float:
    """Score = (signals passed / 7) * 100.

    Args:
        signal_results: dict mapping each signal to True (pass) or False (fail).
    Returns:
        float 0.0–100.0
    """
    passed = sum(1 for v in signal_results.values() if v)
    return (passed / len(ALL_SIGNALS)) * 100.0
