"""Validation for scores.json run records, including the architecture block."""

ALL_SIGNALS = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]

REQUIRED_RUN_KEYS = {
    "timestamp", "iterations", "exit_reason",
    "behavioral_score", "structural_score",
    "scores", "structural_recommendations",
    "promotions", "learnings_added", "learnings_pruned",
}

REQUIRED_ARCH_KEYS = {"architecture_score", "signals", "recommendations"}

REQUIRED_REC_KEYS = {
    "signal", "finding", "location", "recommendation",
    "proposed_agent", "consumers", "benefit", "effort", "lifecycle",
}

VALID_LIFECYCLE = {"NEW", "PERSISTING", "RESOLVED"}


def validate_run(run: dict) -> tuple:
    """Return (ok: bool, errors: list[str])."""
    errors = []

    missing = REQUIRED_RUN_KEYS - set(run.keys())
    if missing:
        errors.append(f"run missing required keys: {sorted(missing)}")

    if "architecture" in run:
        arch = run["architecture"]
        missing_arch = REQUIRED_ARCH_KEYS - set(arch.keys())
        if missing_arch:
            errors.append(f"architecture block missing keys: {sorted(missing_arch)}")
        else:
            sigs = arch.get("signals", {})
            missing_sigs = set(ALL_SIGNALS) - set(sigs.keys())
            if missing_sigs:
                errors.append(
                    f"architecture.signals missing signal keys: {sorted(missing_sigs)}"
                )

            for i, rec in enumerate(arch.get("recommendations", [])):
                missing_rec = REQUIRED_REC_KEYS - set(rec.keys())
                if missing_rec:
                    errors.append(
                        f"recommendation[{i}] missing keys: {sorted(missing_rec)}"
                    )
                elif rec.get("lifecycle") not in VALID_LIFECYCLE:
                    errors.append(
                        f"recommendation[{i}] invalid lifecycle '{rec.get('lifecycle')}'"
                    )

    return len(errors) == 0, errors
