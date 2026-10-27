"""Tests for the investigate skill (T-0079).

The investigate skill is a thin human-facing doorway: it accepts a question,
spawns the investigator orchestrator agent (Opus), and returns the raw
investigation-result. No diligence or verification logic lives in the skill
body — all of that is delegated to the agent layer.

The skill must be Haiku tier (T1: model=haiku, effort=low) because it is a pure
delegation step — no reasoning happens in the skill itself.
"""

import json
import pathlib
import re

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"

SKILL_PATH = SHARED / "skills" / "investigate" / "SKILL.md"
MODEL_TIERS_PATH = SHARED / "resources" / "model-tiers.json"
SKILL_PIPELINE_PATH = SHARED / "skill-pipeline.json"


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


# ---------------------------------------------------------------------------
# Slice 1: File existence and frontmatter identity
# ---------------------------------------------------------------------------


def test_investigate_skill_exists():
    """skills/investigate/SKILL.md must exist."""
    assert SKILL_PATH.exists(), (
        f"Missing: {SKILL_PATH}\n"
        "The investigate skill must live at skills/investigate/SKILL.md."
    )


def test_investigate_skill_has_name_field(skill_text):
    """Frontmatter must declare name: investigate."""
    assert "name: investigate" in skill_text, (
        "SKILL.md frontmatter is missing 'name: investigate'."
    )


# ---------------------------------------------------------------------------
# Slice 2: Haiku tier annotation
# ---------------------------------------------------------------------------


def test_investigate_skill_has_haiku_model(skill_text):
    """Frontmatter must declare model: haiku (T1 tier)."""
    assert "model: haiku" in skill_text, (
        "SKILL.md frontmatter has wrong model. Expected 'model: haiku' (T1 tier). "
        "The investigate skill is a pure delegation doorway — no reasoning runs in the skill."
    )


def test_investigate_skill_has_low_effort(skill_text):
    """Frontmatter must declare effort: low (T1 tier)."""
    assert "effort: low" in skill_text, (
        "SKILL.md frontmatter has wrong effort. Expected 'effort: low' (T1 tier)."
    )


# ---------------------------------------------------------------------------
# Slice 3: model-tiers.json registration at T1
# ---------------------------------------------------------------------------


def test_model_tiers_has_investigate(model_tiers):
    """model-tiers.json must include the investigate skill."""
    skills = model_tiers.get("skills", {})
    assert "investigate" in skills, (
        f"'investigate' not found in model-tiers.json skills map. Keys: {list(skills.keys())}"
    )


def test_model_tiers_investigate_is_t1(model_tiers):
    """model-tiers.json must assign investigate to T1 (Haiku tier)."""
    tier = model_tiers.get("skills", {}).get("investigate")
    assert tier == "T1", (
        f"model-tiers.json has 'investigate': '{tier}', expected 'T1'. "
        "The investigate skill is a pure delegation doorway and belongs at Haiku tier."
    )


# ---------------------------------------------------------------------------
# Slice 4: skill-pipeline.json registration
# ---------------------------------------------------------------------------


def test_skill_pipeline_has_investigate(skill_pipeline):
    """skill-pipeline.json must include 'investigate' as a registered skill."""
    skills = skill_pipeline.get("skills", {})
    assert "investigate" in skills, (
        f"'investigate' not found in skill-pipeline.json. "
        f"Registered skills: {list(skills.keys())}"
    )


# ---------------------------------------------------------------------------
# Slice 5: Skill body delegates to investigator agent
# ---------------------------------------------------------------------------


def test_investigate_skill_references_investigator_agent(skill_text):
    """Skill body must reference or spawn the investigator orchestrator agent."""
    lower = skill_text.lower()
    assert "investigator" in lower, (
        "SKILL.md does not reference the investigator agent. "
        "The skill must spawn the investigator orchestrator to do the actual work."
    )


def test_investigate_skill_uses_agent_tool(skill_text):
    """Skill body must instruct use of the Agent tool to spawn the investigator."""
    lower = skill_text.lower()
    assert "agent" in lower, (
        "SKILL.md does not mention the Agent tool. "
        "The skill must spawn the investigator agent via the Agent tool."
    )


# ---------------------------------------------------------------------------
# Slice 6: Skill body is thin — no diligence or verification logic
# ---------------------------------------------------------------------------


def _extract_body(skill_text: str) -> str:
    """Return the non-frontmatter body of the SKILL.md."""
    # Strip the YAML frontmatter block (--- ... ---)
    parts = skill_text.split("---", 2)
    return parts[2] if len(parts) >= 3 else skill_text


def test_investigate_skill_has_no_insufficient_evidence_logic(skill_text):
    """Skill body must not contain INSUFFICIENT_EVIDENCE routing/branching logic.

    That logic lives entirely in the investigator agent. The skill is a thin
    doorway and must not replicate or second-guess the agent's propagation rule.
    """
    body = _extract_body(skill_text)
    # Allow a brief mention in a reference/context sentence, but not as branching instructions
    # The key anti-pattern: instructions that tell the skill to handle INSUFFICIENT_EVIDENCE
    bad_patterns = [
        "if.*insufficient_evidence",
        "when.*insufficient_evidence",
        "insufficient_evidence.*propagat",
        "insufficient_evidence.*sub-claim",
    ]
    for pattern in bad_patterns:
        matches = re.search(pattern, body.lower())
        assert not matches, (
            f"SKILL.md body contains INSUFFICIENT_EVIDENCE branching logic matching '{pattern}'. "
            "This logic belongs in the investigator agent, not in the skill doorway."
        )


def test_investigate_skill_has_no_aggregation_logic(skill_text):
    """Skill body must not describe evidence aggregation or merging.

    Aggregation of sub-claim results is the orchestrator's job. The skill must
    not duplicate or describe this process.
    """
    body = _extract_body(skill_text)
    lower = body.lower()
    bad_phrases = [
        "aggregate evidence",
        "merge evidence",
        "merging.*evidence",
        "aggregating.*evidence",
        "collect.*evidence.*leaf",
        "leaf.*evidence",
    ]
    for phrase in bad_phrases:
        assert not re.search(phrase, lower), (
            f"SKILL.md body contains aggregation logic: '{phrase}'. "
            "Evidence aggregation belongs in the investigator agent, not the skill."
        )


def test_investigate_skill_has_no_decomposition_instructions(skill_text):
    """Skill body must not instruct how to decompose questions into sub-claims.

    Sub-claim decomposition is the orchestrator's job. The skill passes the
    raw question through and lets the agent handle decomposition.
    """
    body = _extract_body(skill_text)
    lower = body.lower()
    # "sub-claim" or "sub_claim" appearing as instructions to the skill is wrong
    # (it's OK for context/background, but not as instructions for what the skill should do)
    decomposition_instructions = re.search(
        r"decompos.*sub.?claim|sub.?claim.*decompos|break.*into.*sub.?claim",
        lower,
    )
    assert not decomposition_instructions, (
        "SKILL.md body contains sub-claim decomposition instructions. "
        "Decomposition is the investigator orchestrator's job — the skill just passes through."
    )


def test_investigate_skill_has_no_leaf_agent_routing_instructions(skill_text):
    """Skill body must not describe routing sub-claims to specific leaf agents.

    Routing is the orchestrator's responsibility. A thin doorway skill should
    not replicate the routing table (investigator-code, -web, -linear, etc.).
    """
    body = _extract_body(skill_text)
    lower = body.lower()
    # Detecting detailed leaf routing instructions (not just mentions)
    leaf_routing = re.search(
        r"route.*to.*investigator|investigator-(?:code|web|linear|github|notion).*when|"
        r"when to route|routing.*sub-claim",
        lower,
    )
    assert not leaf_routing, (
        "SKILL.md body contains leaf agent routing instructions. "
        "Routing lives in the investigator orchestrator, not in the skill doorway."
    )


# ---------------------------------------------------------------------------
# Slice 7: Input guard (JSON vs prose detection) — T-0097
# ---------------------------------------------------------------------------


def test_investigate_skill_has_input_guard_section(skill_text):
    """SKILL.md must document an input guard step for JSON vs prose detection."""
    body = _extract_body(skill_text)
    lower = body.lower()
    assert "input guard" in lower, (
        "SKILL.md is missing an input guard section. "
        "A step must detect whether input is schema-valid investigation-result JSON "
        "or raw prose, so pre-existing results are never re-investigated."
    )


def test_investigate_skill_guard_covers_prose_path(skill_text):
    """Input guard must describe the prose input path (triggers fresh investigation)."""
    body = _extract_body(skill_text)
    lower = body.lower()
    # The guard must address inputs that are plain prose / questions
    assert re.search(r"prose|plain.*question|plain.*claim|treat as prose", lower), (
        "SKILL.md input guard does not describe handling of raw prose input. "
        "Raw prose must always trigger fresh investigation via the investigator."
    )


def test_investigate_skill_guard_covers_valid_json_path(skill_text):
    """Input guard must describe the valid investigation-result JSON path (skip investigation)."""
    body = _extract_body(skill_text)
    # Check that schema-valid JSON is described as bypassing/skipping investigation
    assert re.search(
        r"skip investigation|pass.*directly.*voic|short.circuit|skip.*investigat",
        body,
        re.IGNORECASE,
    ), (
        "SKILL.md input guard does not describe the valid JSON short-circuit path. "
        "Schema-valid investigation-result JSON must skip investigation and go directly to voicing."
    )


def test_investigate_skill_guard_covers_invalid_json_path(skill_text):
    """Input guard must describe the invalid JSON path (treat as prose)."""
    body = _extract_body(skill_text)
    lower = body.lower()
    # The guard must address JSON that fails schema validation
    assert re.search(
        r"fails.*schema|schema.*validation|invalid.*json|not.*match.*schema|"
        r"missing.*required.*field|wrong.*type",
        lower,
    ), (
        "SKILL.md input guard does not describe handling of invalid JSON (schema mismatch). "
        "JSON that fails schema validation must be treated as prose and trigger fresh investigation."
    )


def test_investigate_skill_guard_appears_before_investigator_step(skill_text):
    """Input guard must appear before the investigator spawn step in the document."""
    body = _extract_body(skill_text)
    guard_pos = body.lower().find("input guard")
    investigator_pos = body.lower().find("spawn the investigator")
    assert guard_pos != -1, "Input guard section not found in SKILL.md body."
    assert investigator_pos != -1, "Investigator spawn step not found in SKILL.md body."
    assert guard_pos < investigator_pos, (
        f"Input guard (pos {guard_pos}) must appear before the investigator spawn step "
        f"(pos {investigator_pos}) in SKILL.md. The guard runs before spawning the investigator."
    )


def test_investigate_skill_guard_references_schema(skill_text):
    """Input guard must reference the investigation-result schema for validation."""
    body = _extract_body(skill_text)
    lower = body.lower()
    assert "schema_version" in lower or "investigation-result-schema" in lower or "investigation-result schema" in lower, (
        "SKILL.md input guard does not reference the investigation-result schema. "
        "The guard must validate against the schema to distinguish valid results from prose."
    )
