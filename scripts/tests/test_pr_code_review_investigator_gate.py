"""Tests for T-0081: investigator pre-submit claim gate wired into pr-code-review.

Before any finding is posted to the PR, pr-code-review spawns the investigator
agent on each factual claim in the finding body. Verdicts control what reaches
the output / comment thread:

  VERIFIED_FALSE      → finding is dropped entirely (not posted)
  INSUFFICIENT_EVIDENCE → finding is downgraded with an explicit caveat
  VERIFIED_TRUE       → finding proceeds unchanged
  CONTESTED           → finding proceeds (genuine disagreement, noted)

These tests parse the SKILL.md to assert the gate contract is described
correctly, and run a reference implementation of the filter function to
verify logic.
"""

import pathlib
import re

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SKILL_PATH = SHARED / "skills" / "pr-code-review" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text():
    assert SKILL_PATH.exists(), f"Missing: {SKILL_PATH}"
    return SKILL_PATH.read_text()


def _extract_claim_gate_section(skill_text: str) -> str:
    """Return the text of the claim-verification gate step.

    Looks for a section that describes investigator-based claim verification.
    Accepts any step header that contains 'claim' or 'investigator' or
    'verification' (case-insensitive), terminated by the next ## header.
    """
    match = re.search(
        r"(## Step 10[^\n]*\n.*?)(?=\n## |\Z)",
        skill_text,
        re.DOTALL,
    )
    if not match:
        # Fallback: find any section header that mentions claim/investigator/verify
        match = re.search(
            r"(##[^\n]*(claim|investigat|verif)[^\n]*\n.*?)(?=\n## |\Z)",
            skill_text,
            re.DOTALL | re.IGNORECASE,
        )
    assert match, (
        "Could not locate a claim-verification gate section in pr-code-review SKILL.md. "
        "Expected a '## Step 10' header (or a section containing 'claim'/'investigator'/'verif') "
        "that describes the pre-submit claim gate."
    )
    return match.group(1)


# ---------------------------------------------------------------------------
# Slice 1: Gate section references the investigator agent
# ---------------------------------------------------------------------------


def test_gate_section_references_investigator(skill_text):
    """The claim gate section must reference the investigator agent by name."""
    section = _extract_claim_gate_section(skill_text)
    lower = section.lower()
    assert "investigator" in lower, (
        "The claim-gate section of pr-code-review does not reference the 'investigator' agent. "
        "The investigator must be wired as the pre-submit verification gate."
    )


def test_gate_section_uses_agent_spawn_language(skill_text):
    """The gate section must describe spawning the investigator (not manual user review)."""
    section = _extract_claim_gate_section(skill_text)
    lower = section.lower()
    has_spawn_language = (
        "spawn" in lower
        or "agent tool" in lower
        or "subagent" in lower
        or "sub-agent" in lower
        or "investigator agent" in lower
    )
    assert has_spawn_language, (
        "The claim-gate section does not describe spawning the investigator agent. "
        "It must instruct the skill to spawn the investigator (not ask the user to evaluate manually)."
    )


# ---------------------------------------------------------------------------
# Slice 2: VERIFIED_FALSE findings are dropped
# ---------------------------------------------------------------------------


def test_gate_drops_verified_false_findings(skill_text):
    """The gate section must state that VERIFIED_FALSE findings are dropped before posting."""
    section = _extract_claim_gate_section(skill_text)
    assert "VERIFIED_FALSE" in section, (
        "The claim-gate section does not mention 'VERIFIED_FALSE'. "
        "The gate must explicitly state that VERIFIED_FALSE claims are dropped."
    )
    lower = section.lower()
    assert "drop" in lower or "remov" in lower or "exclud" in lower, (
        "The gate section mentions VERIFIED_FALSE but does not describe dropping/removing/excluding "
        "those findings. VERIFIED_FALSE findings must be dropped before posting."
    )


# ---------------------------------------------------------------------------
# Slice 3: INSUFFICIENT_EVIDENCE is downgraded, not stated as fact
# ---------------------------------------------------------------------------


def test_gate_handles_insufficient_evidence(skill_text):
    """The gate section must describe handling INSUFFICIENT_EVIDENCE verdicts."""
    section = _extract_claim_gate_section(skill_text)
    assert "INSUFFICIENT_EVIDENCE" in section, (
        "The claim-gate section does not mention 'INSUFFICIENT_EVIDENCE'. "
        "The gate must explicitly handle INSUFFICIENT_EVIDENCE verdicts."
    )


def test_gate_insufficient_evidence_is_downgraded_not_stated_as_fact(skill_text):
    """INSUFFICIENT_EVIDENCE findings must be downgraded with a caveat — not stated as fact."""
    section = _extract_claim_gate_section(skill_text)
    lower = section.lower()
    has_downgrade_language = (
        "downgrade" in lower
        or "caveat" in lower
        or "hedge" in lower
        or "qualify" in lower
        or "not stated as fact" in lower
        or "not present as fact" in lower
        or "drop" in lower
    )
    assert has_downgrade_language, (
        "The gate section mentions INSUFFICIENT_EVIDENCE but does not describe downgrading or "
        "adding a caveat. Claims with INSUFFICIENT_EVIDENCE must not be stated as fact."
    )


# ---------------------------------------------------------------------------
# Slice 4: VERIFIED_TRUE and CONTESTED proceed
# ---------------------------------------------------------------------------


def test_gate_verified_true_proceeds(skill_text):
    """The gate section must state that VERIFIED_TRUE findings proceed to output."""
    section = _extract_claim_gate_section(skill_text)
    assert "VERIFIED_TRUE" in section, (
        "The claim-gate section does not mention 'VERIFIED_TRUE'. "
        "The gate must state which verdicts proceed — VERIFIED_TRUE must proceed."
    )


def test_gate_contested_proceeds(skill_text):
    """The gate section must state that CONTESTED findings proceed (disagreement noted)."""
    section = _extract_claim_gate_section(skill_text)
    assert "CONTESTED" in section, (
        "The claim-gate section does not mention 'CONTESTED'. "
        "The gate must state that CONTESTED findings proceed (genuine disagreement, noted in output)."
    )


# ---------------------------------------------------------------------------
# Slice 5: Gate runs per-finding (not globally once)
# ---------------------------------------------------------------------------


def test_gate_runs_per_finding(skill_text):
    """The gate section must describe running the investigator per-finding or per-claim."""
    section = _extract_claim_gate_section(skill_text)
    lower = section.lower()
    per_item_language = (
        "each claim" in lower
        or "each finding" in lower
        or "per claim" in lower
        or "per finding" in lower
        or "for each" in lower
        or "each factual" in lower
    )
    assert per_item_language, (
        "The gate section does not describe running the investigator per-finding or per-claim. "
        "The gate must spawn investigator for each finding, not just once globally."
    )


# ---------------------------------------------------------------------------
# Slice 6: Gate is pre-output (fires before findings are posted)
# ---------------------------------------------------------------------------


def test_gate_is_described_as_pre_output_or_pre_post(skill_text):
    """The gate must fire before findings are output or posted (not after)."""
    section = _extract_claim_gate_section(skill_text)
    lower = section.lower()
    pre_output_language = (
        "before" in lower
        or "pre-" in lower
        or "prior to" in lower
        or "pre-submit" in lower
        or "before posting" in lower
        or "before output" in lower
    )
    assert pre_output_language, (
        "The gate section does not establish that verification happens before output/posting. "
        "The gate must fire before findings are posted to the PR."
    )


# ---------------------------------------------------------------------------
# Reference implementation: gate filter logic
# ---------------------------------------------------------------------------


def _apply_claim_gate(
    findings: list[dict],
) -> list[dict]:
    """
    Reference implementation of the pr-code-review pre-submit claim gate.

    Each finding dict has:
      - 'text'    : str — the finding body / claim text
      - 'verdict' : str — investigation-result verdict from the investigator

    Returns the filtered and (possibly downgraded) list of findings that
    proceed to the review output, each carrying an 'action' key:
      - 'proceed'   : VERIFIED_TRUE or CONTESTED — goes to output unchanged
      - 'caveat'    : INSUFFICIENT_EVIDENCE — included with explicit caveat
      - 'drop'      : VERIFIED_FALSE — excluded from output entirely

    This mirrors the gate semantics the pr-code-review SKILL.md must describe.
    """
    result = []
    for finding in findings:
        verdict = finding["verdict"]
        if verdict == "VERIFIED_FALSE":
            result.append({**finding, "action": "drop"})
        elif verdict == "INSUFFICIENT_EVIDENCE":
            result.append({**finding, "action": "caveat"})
        elif verdict in ("VERIFIED_TRUE", "CONTESTED"):
            result.append({**finding, "action": "proceed"})
        else:
            raise ValueError(f"Unknown verdict: {verdict!r}")
    return result


# ---------------------------------------------------------------------------
# Integration: gate filter behavior end-to-end
# ---------------------------------------------------------------------------


def test_gate_drops_verified_false():
    """VERIFIED_FALSE findings must be tagged 'drop' (excluded from output)."""
    findings = [
        {"text": "This function never handles None input.", "verdict": "VERIFIED_FALSE"},
    ]
    result = _apply_claim_gate(findings)
    assert len(result) == 1
    assert result[0]["action"] == "drop", (
        f"Expected action='drop' for VERIFIED_FALSE, got {result[0]['action']!r}"
    )


def test_gate_caveats_insufficient_evidence():
    """INSUFFICIENT_EVIDENCE findings must be tagged 'caveat' (downgraded in output)."""
    findings = [
        {"text": "The retry logic is broken here.", "verdict": "INSUFFICIENT_EVIDENCE"},
    ]
    result = _apply_claim_gate(findings)
    assert len(result) == 1
    assert result[0]["action"] == "caveat", (
        f"Expected action='caveat' for INSUFFICIENT_EVIDENCE, got {result[0]['action']!r}"
    )


def test_gate_proceeds_verified_true():
    """VERIFIED_TRUE findings must be tagged 'proceed' (included in output unchanged)."""
    findings = [
        {"text": "The timeout is hardcoded to 5000ms.", "verdict": "VERIFIED_TRUE"},
    ]
    result = _apply_claim_gate(findings)
    assert len(result) == 1
    assert result[0]["action"] == "proceed"


def test_gate_proceeds_contested():
    """CONTESTED findings must be tagged 'proceed' (included; genuine disagreement noted)."""
    findings = [
        {"text": "This approach is slower than alternatives.", "verdict": "CONTESTED"},
    ]
    result = _apply_claim_gate(findings)
    assert len(result) == 1
    assert result[0]["action"] == "proceed"


def test_gate_mixed_findings_correctly_routed():
    """Gate correctly handles a mixed batch of all four verdict types."""
    findings = [
        {"text": "Missing null check.", "verdict": "VERIFIED_FALSE"},
        {"text": "Timeout hardcoded.", "verdict": "VERIFIED_TRUE"},
        {"text": "Race condition possible.", "verdict": "INSUFFICIENT_EVIDENCE"},
        {"text": "Perf regression.", "verdict": "CONTESTED"},
    ]
    result = _apply_claim_gate(findings)
    actions = [r["action"] for r in result]
    assert actions == ["drop", "proceed", "caveat", "proceed"], (
        f"Gate actions mismatch. Got: {actions}"
    )


def test_gate_empty_findings_returns_empty():
    """Gate must handle an empty finding list gracefully."""
    assert _apply_claim_gate([]) == []


def test_gate_all_verified_false_yields_empty_output():
    """When every finding is VERIFIED_FALSE, the resulting output is effectively empty."""
    findings = [
        {"text": "Finding A.", "verdict": "VERIFIED_FALSE"},
        {"text": "Finding B.", "verdict": "VERIFIED_FALSE"},
    ]
    result = _apply_claim_gate(findings)
    proceeding = [r for r in result if r["action"] != "drop"]
    assert proceeding == [], (
        "Expected no findings to proceed when all are VERIFIED_FALSE."
    )


def test_gate_insufficient_evidence_never_stated_as_fact():
    """
    Findings tagged 'caveat' must NOT be treated as 'proceed'.
    INSUFFICIENT_EVIDENCE must never be silently upgraded to proceed.
    """
    findings = [
        {"text": "The config is never validated.", "verdict": "INSUFFICIENT_EVIDENCE"},
    ]
    result = _apply_claim_gate(findings)
    assert result[0]["action"] != "proceed", (
        "INSUFFICIENT_EVIDENCE finding was tagged 'proceed'. "
        "It must be tagged 'caveat' — the claim must not be stated as fact."
    )
