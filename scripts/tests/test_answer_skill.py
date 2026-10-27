"""Tests for T-0083: answer skill (thin Slack Q&A doorway).

The answer skill is a Haiku-tier (T1) thin doorway. It accepts a question,
spawns the investigator orchestrator agent to verify claims, passes the
investigation-result to the answer-composer agent, and returns the composed
Slack reply for manual paste.

No verification or composition logic lives in the skill body — all of that
is delegated to the investigator (Opus) and answer-composer (Sonnet) agents.
"""

import json
import pathlib
import re

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"

SKILL_PATH = SHARED / "skills" / "answer" / "SKILL.md"
MODEL_TIERS_PATH = SHARED / "resources" / "model-tiers.json"
SKILL_PIPELINE_PATH = SHARED / "skill-pipeline.json"
LEARNING_CONTRACT_PATH = SHARED / "contracts" / "learning-contract.md"


@pytest.fixture(scope="module")
def skill_text():
    assert SKILL_PATH.exists(), f"Missing: {SKILL_PATH}"
    return SKILL_PATH.read_text()


@pytest.fixture(scope="module")
def model_tiers():
    assert MODEL_TIERS_PATH.exists(), f"Missing: {MODEL_TIERS_PATH}"
    return json.loads(MODEL_TIERS_PATH.read_text())


@pytest.fixture(scope="module")
def skill_pipeline():
    assert SKILL_PIPELINE_PATH.exists(), f"Missing: {SKILL_PIPELINE_PATH}"
    return json.loads(SKILL_PIPELINE_PATH.read_text())


@pytest.fixture(scope="module")
def learning_contract():
    assert LEARNING_CONTRACT_PATH.exists(), f"Missing: {LEARNING_CONTRACT_PATH}"
    return LEARNING_CONTRACT_PATH.read_text()


def _extract_body(skill_text: str) -> str:
    """Return the non-frontmatter body of the SKILL.md."""
    parts = skill_text.split("---", 2)
    return parts[2] if len(parts) >= 3 else skill_text


# ---------------------------------------------------------------------------
# Slice 1: File existence and frontmatter identity
# ---------------------------------------------------------------------------


def test_answer_skill_exists():
    """skills/answer/SKILL.md must exist."""
    assert SKILL_PATH.exists(), (
        f"Missing: {SKILL_PATH}\n"
        "The answer skill must live at skills/answer/SKILL.md."
    )


def test_answer_skill_has_name_field(skill_text):
    """Frontmatter must declare name: answer."""
    assert "name: answer" in skill_text, (
        "SKILL.md frontmatter is missing 'name: answer'."
    )


# ---------------------------------------------------------------------------
# Slice 2: Haiku tier annotation
# ---------------------------------------------------------------------------


def test_answer_skill_has_haiku_model(skill_text):
    """Frontmatter must declare model: haiku (T1 tier)."""
    assert "model: haiku" in skill_text, (
        "SKILL.md frontmatter has wrong model. Expected 'model: haiku' (T1 tier). "
        "The answer skill is a pure delegation doorway — no reasoning runs in the skill."
    )


def test_answer_skill_has_low_effort(skill_text):
    """Frontmatter must declare effort: low (T1 tier)."""
    assert "effort: low" in skill_text, (
        "SKILL.md frontmatter has wrong effort. Expected 'effort: low' (T1 tier)."
    )


# ---------------------------------------------------------------------------
# Slice 3: model-tiers.json registration at T1
# ---------------------------------------------------------------------------


def test_model_tiers_has_answer(model_tiers):
    """model-tiers.json must include the answer skill."""
    skills = model_tiers.get("skills", {})
    assert "answer" in skills, (
        f"'answer' not found in model-tiers.json skills map. Keys: {list(skills.keys())}"
    )


def test_model_tiers_answer_is_t1(model_tiers):
    """model-tiers.json must assign answer to T1 (Haiku tier)."""
    tier = model_tiers.get("skills", {}).get("answer")
    assert tier == "T1", (
        f"model-tiers.json has 'answer': '{tier}', expected 'T1'. "
        "The answer skill is a pure delegation doorway and belongs at Haiku tier."
    )


# ---------------------------------------------------------------------------
# Slice 4: skill-pipeline.json registration
# ---------------------------------------------------------------------------


def test_skill_pipeline_has_answer(skill_pipeline):
    """skill-pipeline.json must include 'answer' as a registered skill."""
    skills = skill_pipeline.get("skills", {})
    assert "answer" in skills, (
        f"'answer' not found in skill-pipeline.json. "
        f"Registered skills: {list(skills.keys())}"
    )


# ---------------------------------------------------------------------------
# Slice 5: Skill body delegates to investigator and answer-composer agents
# ---------------------------------------------------------------------------


def test_answer_skill_references_investigator_agent(skill_text):
    """Skill body must reference the investigator orchestrator agent."""
    lower = skill_text.lower()
    assert "investigator" in lower, (
        "SKILL.md does not reference the investigator agent. "
        "The skill must spawn the investigator orchestrator to verify claims."
    )


def test_answer_skill_references_answer_composer_agent(skill_text):
    """Skill body must reference the answer-composer egress agent."""
    lower = skill_text.lower()
    assert "answer-composer" in lower, (
        "SKILL.md does not reference the answer-composer agent. "
        "The skill must pass the investigation-result to answer-composer for composition."
    )


def test_answer_skill_uses_agent_tool(skill_text):
    """Skill body must instruct use of the Agent tool for both delegation steps."""
    lower = skill_text.lower()
    assert "agent" in lower, (
        "SKILL.md does not mention the Agent tool. "
        "The skill must spawn both investigator and answer-composer via the Agent tool."
    )


def test_answer_skill_instructs_returning_reply_verbatim(skill_text):
    """Skill body must instruct returning the composed reply verbatim."""
    lower = skill_text.lower()
    assert "verbatim" in lower or "as-is" in lower or "as is" in lower, (
        "SKILL.md does not instruct returning the answer-composer reply verbatim. "
        "The skill must return the reply without editing, reformatting, or adding framing."
    )


# ---------------------------------------------------------------------------
# Slice 6: Skill body is thin — no verification or composition logic
# ---------------------------------------------------------------------------


def test_answer_skill_has_no_verdict_handling_logic(skill_text):
    """Skill body must not contain verdict-based branching logic.

    Claim verification and verdict handling live in the investigator and
    answer-composer agents. The skill is a thin doorway.
    """
    body = _extract_body(skill_text)
    bad_patterns = [
        "if.*verified_true",
        "if.*insufficient_evidence",
        "if.*verified_false",
        "when.*verdict",
        "switch.*verdict",
    ]
    for pattern in bad_patterns:
        match = re.search(pattern, body.lower())
        assert not match, (
            f"SKILL.md body contains verdict-handling logic matching '{pattern}'. "
            "Verdict handling belongs in the answer-composer agent, not in the skill doorway."
        )


def test_answer_skill_has_no_citation_formatting_logic(skill_text):
    """Skill body must not describe how to format citations.

    Citation formatting is the answer-composer agent's responsibility. The
    skill must not replicate or describe the inline citation format.
    """
    body = _extract_body(skill_text)
    lower = body.lower()
    citation_instructions = re.search(
        r"inline citation|bracket notation|citation.*format|format.*citation|"
        r"append.*\[ref\]|citation.*inline",
        lower,
    )
    assert not citation_instructions, (
        "SKILL.md body contains citation formatting instructions. "
        "Citation formatting lives in the answer-composer agent, not in the skill doorway."
    )


def test_answer_skill_has_no_insufficient_evidence_handling(skill_text):
    """Skill body must not instruct how to handle INSUFFICIENT_EVIDENCE.

    The 'couldn't confirm' tag and all uncertainty handling belong in the
    answer-composer agent. The skill simply passes the investigation-result through.
    """
    body = _extract_body(skill_text)
    lower = body.lower()
    bad_patterns = [
        "couldn't confirm",
        "insufficient_evidence.*tag",
        "tag.*insufficient",
        "uncertainty.*handling",
    ]
    for pattern in bad_patterns:
        match = re.search(pattern, lower)
        assert not match, (
            f"SKILL.md body contains INSUFFICIENT_EVIDENCE handling matching '{pattern}'. "
            "This belongs in the answer-composer agent, not in the skill doorway."
        )


# ---------------------------------------------------------------------------
# Slice 7: Learning-capture tail block
# ---------------------------------------------------------------------------


def test_answer_skill_has_learning_capture_tail(skill_text):
    """SKILL.md must have the learning-capture tail block."""
    assert "learning-capture:start" in skill_text, (
        "SKILL.md is missing the learning-capture tail block. "
        "Run inject-learning-tail.py --apply to add it."
    )


def test_answer_skill_tail_has_correct_slug(skill_text):
    """Learning-capture tail block must use slug 'answer'."""
    assert "skill-done: answer" in skill_text, (
        "Learning-capture tail block has wrong skill slug. Expected 'skill-done: answer'."
    )


# ---------------------------------------------------------------------------
# Slice 8: learning-contract.md producer registration
# ---------------------------------------------------------------------------


def test_learning_contract_has_answer_skill(learning_contract):
    """learning-contract.md producers list must include skills/answer/."""
    assert "skills/answer/" in learning_contract, (
        "'skills/answer/' not found in learning-contract.md producers list. "
        "Add '- `skills/answer/`' in alphabetical order (before skills/build-code/)."
    )
