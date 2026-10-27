"""Tests for T-0080: investigator diligence gate wired into pr-revise Step 5.

The investigator agent (Opus tier) replaces the prior manual/vet-stub diligence
in Step 5 of pr-revise. Every claim-bearing item is routed through the investigator
before a reply draft is posted. Verdicts control what reaches the seed:

  VERIFIED_FALSE      → claim is dropped from the draft
  INSUFFICIENT_EVIDENCE → claim is downgraded with an explicit caveat (or dropped)
  VERIFIED_TRUE       → claim proceeds unchanged
  CONTESTED           → claim proceeds (genuine disagreement, noted in draft)

These tests parse the SKILL.md to assert the gate contract is described correctly,
and run a reference implementation of the filter function to verify logic.
"""

import pathlib
import re

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SKILL_PATH = SHARED / "skills" / "pr-revise" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text():
    assert SKILL_PATH.exists(), f"Missing: {SKILL_PATH}"
    return SKILL_PATH.read_text()


def _extract_step5(skill_text: str) -> str:
    """Return the text of Step 5 only (between ## Step 5 and ## Step 6 headers)."""
    match = re.search(
        r"(## Step 5[^\n]*\n.*?)(?=\n## Step 6)",
        skill_text,
        re.DOTALL,
    )
    assert match, "Could not locate '## Step 5' section in pr-revise SKILL.md"
    return match.group(1)


# ---------------------------------------------------------------------------
# Slice 1: Step 5 references the investigator agent
# ---------------------------------------------------------------------------


def test_step5_references_investigator(skill_text):
    """Step 5 must reference the investigator agent by name."""
    step5 = _extract_step5(skill_text)
    lower = step5.lower()
    assert "investigator" in lower, (
        "Step 5 of pr-revise does not reference the 'investigator' agent. "
        "The investigator must be wired as the Step 5 diligence gate."
    )


def test_step5_uses_agent_spawn_language(skill_text):
    """Step 5 must describe spawning the investigator (not manual user review)."""
    step5 = _extract_step5(skill_text)
    lower = step5.lower()
    has_spawn_language = (
        "spawn" in lower
        or "agent tool" in lower
        or "subagent" in lower
        or "sub-agent" in lower
        or "investigator agent" in lower
    )
    assert has_spawn_language, (
        "Step 5 does not describe spawning the investigator agent. "
        "It must instruct the skill to spawn the investigator (not ask the user to evaluate manually)."
    )


# ---------------------------------------------------------------------------
# Slice 2: Step 5 drops VERIFIED_FALSE claims
# ---------------------------------------------------------------------------


def test_step5_drops_verified_false_claims(skill_text):
    """Step 5 must state that VERIFIED_FALSE claims are dropped from the draft."""
    step5 = _extract_step5(skill_text)
    assert "VERIFIED_FALSE" in step5, (
        "Step 5 does not mention 'VERIFIED_FALSE'. "
        "The gate must explicitly state that VERIFIED_FALSE claims are dropped."
    )
    lower = step5.lower()
    assert "drop" in lower or "remov" in lower or "exclud" in lower, (
        "Step 5 mentions VERIFIED_FALSE but does not describe dropping/removing/excluding those claims. "
        "VERIFIED_FALSE claims must be dropped from the draft before posting."
    )


# ---------------------------------------------------------------------------
# Slice 3: Step 5 downgrades/caveats INSUFFICIENT_EVIDENCE claims
# ---------------------------------------------------------------------------


def test_step5_handles_insufficient_evidence(skill_text):
    """Step 5 must describe downgrading or caveating INSUFFICIENT_EVIDENCE claims."""
    step5 = _extract_step5(skill_text)
    assert "INSUFFICIENT_EVIDENCE" in step5, (
        "Step 5 does not mention 'INSUFFICIENT_EVIDENCE'. "
        "The gate must explicitly handle INSUFFICIENT_EVIDENCE verdicts."
    )


def test_step5_insufficient_evidence_is_downgraded_not_stated_as_fact(skill_text):
    """Step 5 must describe downgrading or adding a caveat, not stating as fact."""
    step5 = _extract_step5(skill_text)
    lower = step5.lower()
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
        "Step 5 mentions INSUFFICIENT_EVIDENCE but does not describe downgrading or adding a caveat. "
        "Claims with INSUFFICIENT_EVIDENCE must not be stated as fact in the draft."
    )


# ---------------------------------------------------------------------------
# Slice 4: Step 5 allows VERIFIED_TRUE and CONTESTED to proceed
# ---------------------------------------------------------------------------


def test_step5_verified_true_proceeds(skill_text):
    """Step 5 must state that VERIFIED_TRUE claims proceed to the draft."""
    step5 = _extract_step5(skill_text)
    assert "VERIFIED_TRUE" in step5, (
        "Step 5 does not mention 'VERIFIED_TRUE'. "
        "The gate must state which verdicts proceed — VERIFIED_TRUE must proceed."
    )


def test_step5_contested_proceeds(skill_text):
    """Step 5 must state that CONTESTED claims proceed (with their conflict noted)."""
    step5 = _extract_step5(skill_text)
    assert "CONTESTED" in step5, (
        "Step 5 does not mention 'CONTESTED'. "
        "The gate must state that CONTESTED claims proceed (genuine disagreement, noted in draft)."
    )


# ---------------------------------------------------------------------------
# Slice 5: Old vet-stub note is removed
# ---------------------------------------------------------------------------


def test_step5_removes_vet_stub_note(skill_text):
    """The old vet-stub placeholder note must not appear in Step 5."""
    step5 = _extract_step5(skill_text)
    lower = step5.lower()
    assert "when the `vet` skill is built" not in lower and "for v1, diligence is manual" not in lower, (
        "Step 5 still contains the old vet-stub placeholder note "
        "('when the `vet` skill is built' / 'for v1, diligence is manual'). "
        "This note must be removed now that the investigator agent is wired in."
    )


# ---------------------------------------------------------------------------
# Slice 6: Step 5 spawns investigator per-claim (not globally once)
# ---------------------------------------------------------------------------


def test_step5_spawns_investigator_per_item(skill_text):
    """Step 5 must describe running the investigator per-claim or per-item (not once globally)."""
    step5 = _extract_step5(skill_text)
    lower = step5.lower()
    per_item_language = (
        "each claim" in lower
        or "each item" in lower
        or "per claim" in lower
        or "per item" in lower
        or "for each" in lower
        or "per bug" in lower
        or "each bug" in lower
    )
    assert per_item_language, (
        "Step 5 does not describe running the investigator per-claim or per-item. "
        "The gate must spawn investigator for each claim, not just once globally."
    )


# ---------------------------------------------------------------------------
# Reference implementation: gate filter logic
# ---------------------------------------------------------------------------


def _apply_diligence_gate(
    claims: list[dict],
) -> list[dict]:
    """
    Reference implementation of the Step 5 diligence gate filter.

    Each claim dict has:
      - 'text'    : str — the reviewer's claim
      - 'verdict' : str — investigation-result verdict

    Returns the filtered and (possibly downgraded) list of claims that
    proceed to the reply draft, each carrying an 'action' key:
      - 'proceed'   : VERIFIED_TRUE or CONTESTED — goes to draft unchanged
      - 'caveat'    : INSUFFICIENT_EVIDENCE — included with explicit caveat
      - 'drop'      : VERIFIED_FALSE — excluded from draft entirely

    This mirrors the gate semantics the pr-revise Step 5 SKILL.md must describe.
    """
    result = []
    for claim in claims:
        verdict = claim["verdict"]
        if verdict == "VERIFIED_FALSE":
            result.append({**claim, "action": "drop"})
        elif verdict == "INSUFFICIENT_EVIDENCE":
            result.append({**claim, "action": "caveat"})
        elif verdict in ("VERIFIED_TRUE", "CONTESTED"):
            result.append({**claim, "action": "proceed"})
        else:
            raise ValueError(f"Unknown verdict: {verdict!r}")
    return result


# ---------------------------------------------------------------------------
# Integration: gate filter behavior end-to-end
# ---------------------------------------------------------------------------


def test_gate_drops_verified_false():
    """VERIFIED_FALSE claims must be tagged 'drop' (excluded from draft)."""
    claims = [
        {"text": "The nil check is missing.", "verdict": "VERIFIED_FALSE"},
    ]
    result = _apply_diligence_gate(claims)
    assert len(result) == 1
    assert result[0]["action"] == "drop", (
        f"Expected action='drop' for VERIFIED_FALSE, got {result[0]['action']!r}"
    )


def test_gate_caveats_insufficient_evidence():
    """INSUFFICIENT_EVIDENCE claims must be tagged 'caveat' (downgraded in draft)."""
    claims = [
        {"text": "This function never returns None.", "verdict": "INSUFFICIENT_EVIDENCE"},
    ]
    result = _apply_diligence_gate(claims)
    assert len(result) == 1
    assert result[0]["action"] == "caveat", (
        f"Expected action='caveat' for INSUFFICIENT_EVIDENCE, got {result[0]['action']!r}"
    )


def test_gate_proceeds_verified_true():
    """VERIFIED_TRUE claims must be tagged 'proceed' (included in draft unchanged)."""
    claims = [
        {"text": "The timeout is hardcoded to 30s.", "verdict": "VERIFIED_TRUE"},
    ]
    result = _apply_diligence_gate(claims)
    assert len(result) == 1
    assert result[0]["action"] == "proceed"


def test_gate_proceeds_contested():
    """CONTESTED claims must be tagged 'proceed' (included; genuine disagreement noted)."""
    claims = [
        {"text": "This approach is slower than the v1 impl.", "verdict": "CONTESTED"},
    ]
    result = _apply_diligence_gate(claims)
    assert len(result) == 1
    assert result[0]["action"] == "proceed"


def test_gate_mixed_claims_correctly_sorted():
    """Gate correctly handles a mixed batch of all four verdict types."""
    claims = [
        {"text": "Nil check missing.", "verdict": "VERIFIED_FALSE"},
        {"text": "Timeout hardcoded.", "verdict": "VERIFIED_TRUE"},
        {"text": "Race condition possible.", "verdict": "INSUFFICIENT_EVIDENCE"},
        {"text": "Perf regression.", "verdict": "CONTESTED"},
    ]
    result = _apply_diligence_gate(claims)
    actions = [r["action"] for r in result]
    assert actions == ["drop", "proceed", "caveat", "proceed"], (
        f"Gate actions mismatch. Got: {actions}"
    )


def test_gate_empty_claims_list_returns_empty():
    """Gate must handle an empty claim list gracefully."""
    assert _apply_diligence_gate([]) == []


def test_gate_all_verified_false_yields_empty_draft():
    """When every claim is VERIFIED_FALSE, the resulting draft is effectively empty."""
    claims = [
        {"text": "Bug A.", "verdict": "VERIFIED_FALSE"},
        {"text": "Bug B.", "verdict": "VERIFIED_FALSE"},
    ]
    result = _apply_diligence_gate(claims)
    proceeding = [r for r in result if r["action"] != "drop"]
    assert proceeding == [], (
        "Expected no claims to proceed when all are VERIFIED_FALSE."
    )


def test_gate_insufficient_evidence_never_stated_as_fact():
    """
    Claims tagged 'caveat' must NOT be treated as 'proceed'.
    This verifies that INSUFFICIENT_EVIDENCE is never silently upgraded to proceed.
    """
    claims = [
        {"text": "The config is always loaded at startup.", "verdict": "INSUFFICIENT_EVIDENCE"},
    ]
    result = _apply_diligence_gate(claims)
    assert result[0]["action"] != "proceed", (
        "INSUFFICIENT_EVIDENCE claim was tagged 'proceed'. "
        "It must be tagged 'caveat' — the claim must not be stated as fact."
    )
