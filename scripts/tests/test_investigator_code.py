"""Tests for investigator-code agent and vet stub retirement (T-0074).

Characterization section documents what the vet stub specified before deletion.
The vet stub described a diligence gate in pr-revise with verdicts:
  confirmed / false_flag / not_an_escape
It was model: sonnet, effort: high, and listed as a wiring anchor in
skill-pipeline.json. It has been superseded by investigator-code (T-0074),
which provides real code-behavior verification against the investigation-result
contract.
"""

import json
import pathlib

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator-code.md"
VET_SKILL_DIR = SHARED / "skills" / "vet"
PIPELINE_PATH = SHARED / "skill-pipeline.json"


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
# Vet stub characterization — captured before deletion, verified after (T-0074)
#
# Prior behavior of skills/vet/SKILL.md (now deleted):
#   - model: sonnet, effort: high
#   - Described verdict types: confirmed / false_flag / not_an_escape
#   - Purpose: verify PR claims against actual code (diligence gate)
#   - Consumer: pr-revise skill
#   - Wiring: registered in skill-pipeline.json as a terminal node (next: [])
#   - Was explicitly marked as a stub ("not yet functionally implemented")
#
# Superseded by investigator-code, which implements real code verification
# against the investigation-result contract.
# ---------------------------------------------------------------------------


def test_vet_stub_has_been_deleted():
    """Vet stub superseded by investigator-code; skills/vet/ must be removed."""
    assert not VET_SKILL_DIR.exists(), (
        f"Vet stub still present at {VET_SKILL_DIR} — should be deleted (T-0074). "
        "investigator-code is the replacement."
    )


def test_vet_removed_from_skill_pipeline():
    """Vet removed from skill-pipeline.json after skills/vet/ was deleted.

    skill-pipeline.json validates that all slugs exist in skills/; the entry
    must be removed to keep test_skill_pipeline.py::TestSlugIntegrity passing.
    """
    pipeline = json.loads(PIPELINE_PATH.read_text())
    assert "vet" not in pipeline["skills"], (
        "vet still registered in skill-pipeline.json — remove the entry when "
        "deleting the vet skill directory"
    )


# ---------------------------------------------------------------------------
# investigator-code agent structural tests
# ---------------------------------------------------------------------------


def test_investigator_code_agent_exists():
    assert AGENT_PATH.exists(), f"Missing: {AGENT_PATH}"


def test_investigator_code_has_sonnet_model(agent_text):
    assert "model: sonnet" in agent_text, "Agent missing 'model: sonnet' frontmatter"


def test_investigator_code_has_opus_escalation_note(agent_text):
    lower = agent_text.lower()
    assert "opus" in lower, "Agent must mention Opus escalation watch"
    # The watch condition is multi-file reasoning
    assert "escalat" in lower or "watch" in lower or "multi-file" in lower, (
        "Agent must note the Opus escalation condition (multi-file reasoning)"
    )


def test_investigator_code_references_investigation_result_contract(agent_text):
    assert "investigation-result" in agent_text.lower(), (
        "Agent does not reference investigation-result contract"
    )


def test_investigator_code_accepts_sub_claim(agent_text):
    assert "sub_claim" in agent_text or "sub-claim" in agent_text.lower(), (
        "Agent does not mention sub_claim input"
    )


def test_investigator_code_documents_insufficient_evidence(agent_text):
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Agent must document INSUFFICIENT_EVIDENCE fallback"
    )


def test_investigator_code_uses_code_reading_tools(agent_text):
    has_grep = "Grep" in agent_text
    has_glob = "Glob" in agent_text
    has_read = "Read" in agent_text
    assert has_grep or has_glob or has_read, (
        "Agent must mention Grep, Glob, or Read tools for code investigation"
    )


def test_investigator_code_documents_github_url_ref_format(agent_text):
    assert "github.com" in agent_text, (
        "Agent must document GitHub URL ref format for code evidence"
    )
    assert "#L" in agent_text, (
        "Agent must document line anchor (#L) in GitHub URL ref format"
    )


def test_investigator_code_specifies_code_source_type(agent_text):
    assert '"code"' in agent_text or "'code'" in agent_text or "source: code" in agent_text or 'source: "code"' in agent_text, (
        "Agent must specify source: 'code' for evidence items"
    )


# ---------------------------------------------------------------------------
# Sample investigation-result outputs — code source type
# These mimic what the investigator-code agent would emit at runtime.
# Schema validation guarantees the agent's documented output shape is valid.
# ---------------------------------------------------------------------------


def test_verified_true_code_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a code GitHub URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "The authenticate function throws on null credentials.",
        "evidence": [
            {
                "source": "code",
                "ref": "https://github.com/org/repo/blob/main/src/auth/authenticate.ts#L42",
                "quote": "if (!credentials) throw new Error('credentials required');",
            }
        ],
        "summary": "Line 42 confirms a null-guard that throws when credentials are absent.",
    }
    validate(doc, schema)


def test_verified_false_code_output_is_schema_valid(schema):
    """VERIFIED_FALSE with a code GitHub URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "sub_claim": "The config module reads from environment variables.",
        "evidence": [
            {
                "source": "code",
                "ref": "https://github.com/org/repo/blob/main/src/config/index.ts#L10",
                "quote": "const config = JSON.parse(fs.readFileSync('./config.json', 'utf8'));",
            }
        ],
        "summary": "Config is loaded from a JSON file, not environment variables.",
    }
    validate(doc, schema)


def test_insufficient_evidence_code_output_is_schema_valid(schema):
    """INSUFFICIENT_EVIDENCE with empty evidence[] is valid for code investigations."""
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "sub_claim": "The retry logic uses exponential backoff.",
        "evidence": [],
        "summary": "No file containing retry logic was found in the searched paths.",
    }
    validate(doc, schema)


def test_code_evidence_ref_uses_github_url_format(schema):
    """Code evidence ref must be a GitHub blob URL with a line anchor."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "https://github.com/org/repo/blob/main/src/utils/parser.py#L88",
                "quote": "raise ValueError('invalid token')",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert ref.startswith("https://github.com/"), (
        f"Code ref must be a GitHub URL, got: {ref}"
    )
    assert "#L" in ref, (
        f"Code ref must include a line anchor (#LNN), got: {ref}"
    )


def test_code_evidence_empty_quote_rejected(schema):
    """Schema rejects evidence items with an empty quote string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "src/foo.py:1",
                "quote": "",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_code_evidence_empty_ref_rejected(schema):
    """Schema rejects evidence items with an empty ref string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "",
                "quote": "some code here",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_verified_true_code_without_evidence_rejected(schema):
    """Schema rejects VERIFIED_TRUE with empty evidence[] for code source."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)
