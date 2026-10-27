"""Tests for investigator-web agent and retired fact-check skill."""

import json
import pathlib

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator-web.md"
FACT_CHECK_SKILL_PATH = SHARED / "skills" / "fact-check" / "SKILL.md"


@pytest.fixture(scope="module")
def schema():
    assert SCHEMA_PATH.exists(), f"Schema file not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def agent_text():
    assert AGENT_PATH.exists(), f"Agent file not found: {AGENT_PATH}"
    return AGENT_PATH.read_text()


def validate(instance, schema):
    """Raise ValidationError if instance does not conform to schema."""
    jsonschema.validate(instance, schema)


# ---------------------------------------------------------------------------
# Agent file structural tests
# ---------------------------------------------------------------------------


def test_investigator_web_agent_exists():
    assert AGENT_PATH.exists(), f"Missing: {AGENT_PATH}"


def test_investigator_web_has_sonnet_model(agent_text):
    assert "model: sonnet" in agent_text, "Agent missing 'model: sonnet' frontmatter"


def test_investigator_web_references_investigation_result_contract(agent_text):
    assert "investigation-result" in agent_text.lower(), (
        "Agent does not reference investigation-result contract"
    )


def test_investigator_web_accepts_sub_claim(agent_text):
    assert "sub_claim" in agent_text or "sub-claim" in agent_text.lower(), (
        "Agent does not mention sub_claim input"
    )


def test_investigator_web_documents_insufficient_evidence(agent_text):
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Agent must document INSUFFICIENT_EVIDENCE fallback"
    )


def test_investigator_web_uses_web_search_tools(agent_text):
    assert "WebSearch" in agent_text or "websearch" in agent_text.lower(), (
        "Agent must mention WebSearch tool"
    )


# ---------------------------------------------------------------------------
# Fact-check skill retirement
# ---------------------------------------------------------------------------


def test_fact_check_skill_exists_as_redirect_stub():
    """SKILL.md must still exist (as a redirect stub, not deleted)."""
    assert FACT_CHECK_SKILL_PATH.exists(), (
        f"fact-check SKILL.md missing entirely — should exist as redirect stub: {FACT_CHECK_SKILL_PATH}"
    )


def test_fact_check_skill_does_not_spawn_old_agent():
    """Retired skill must not contain spawn logic for the old fact-checker agent."""
    text = FACT_CHECK_SKILL_PATH.read_text()
    assert "subagent_type: fact-checker" not in text, (
        "Retired skill still spawns the old fact-checker agent — must be removed"
    )


def test_fact_check_skill_redirects_to_investigate():
    """Retired stub must mention /investigate as the replacement."""
    text = FACT_CHECK_SKILL_PATH.read_text()
    assert "investigate" in text.lower(), (
        "Retired fact-check skill must redirect users to /investigate"
    )


# ---------------------------------------------------------------------------
# Sample investigation-result fixture outputs (web verdicts)
# These mimic what the investigator-web agent would emit at runtime.
# Schema validation here guarantees the agent's documented output shape is valid.
# ---------------------------------------------------------------------------


def test_verified_true_web_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a web URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "Python 3.12 was released in October 2023.",
        "evidence": [
            {
                "source": "web",
                "ref": "https://docs.python.org/3/whatsnew/3.12.html",
                "quote": "Python 3.12 was released on October 2, 2023.",
            }
        ],
        "summary": "Official Python docs confirm the October 2023 release date.",
    }
    validate(doc, schema)


def test_verified_false_web_output_is_schema_valid(schema):
    """VERIFIED_FALSE with a web URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "sub_claim": "The Eiffel Tower is located in London.",
        "evidence": [
            {
                "source": "web",
                "ref": "https://www.toureiffel.paris/en",
                "quote": (
                    "The Eiffel Tower is a wrought-iron lattice tower on the "
                    "Champ de Mars in Paris, France."
                ),
            }
        ],
        "summary": "The official Eiffel Tower site confirms it is in Paris, not London.",
    }
    validate(doc, schema)


def test_insufficient_evidence_empty_array_is_schema_valid(schema):
    """INSUFFICIENT_EVIDENCE with empty evidence[] is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "sub_claim": "Acme Corp shipped feature X last Tuesday.",
        "evidence": [],
        "summary": "No indexed public source confirmed or denied this sub-claim.",
    }
    validate(doc, schema)


def test_verified_true_requires_non_empty_evidence(schema):
    """Schema must reject VERIFIED_TRUE with empty evidence[]."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_verified_false_requires_non_empty_evidence(schema):
    """Schema must reject VERIFIED_FALSE with empty evidence[]."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_web_evidence_ref_is_url(schema):
    """Web source refs are URL strings — schema accepts them and they start with http."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "web",
                "ref": "https://example.com/article",
                "quote": "Supporting evidence text.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert ref.startswith("https://") or ref.startswith("http://"), (
        f"Web ref should be a URL, got: {ref}"
    )
