"""Tests for investigation-result-schema.json and investigation-result-contract.md."""

import json
import pathlib

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
CONTRACT_PATH = SHARED / "contracts" / "investigation-result-contract.md"


@pytest.fixture(scope="module")
def schema():
    assert SCHEMA_PATH.exists(), f"Schema file not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text())


def validate(instance, schema):
    """Return None on valid, raises ValidationError on invalid."""
    jsonschema.validate(instance, schema)


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"Missing: {SCHEMA_PATH}"


def test_schema_is_draft07(schema):
    assert schema.get("$schema") == "http://json-schema.org/draft-07/schema#"


def test_schema_has_required_top_level_fields(schema):
    props = schema.get("properties", {})
    assert "verdict" in props
    assert "evidence" in props
    assert "schema_version" in props


# ---------------------------------------------------------------------------
# Valid fixtures — all four verdicts
# ---------------------------------------------------------------------------


def test_valid_verified_true(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "web",
                "ref": "https://example.com/article",
                "quote": "The sky is blue.",
            }
        ],
    }
    validate(doc, schema)  # must not raise


def test_valid_verified_false(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "evidence": [
            {
                "source": "code",
                "ref": "src/main.py:42",
                "quote": "return False  # contradicts the claim",
            }
        ],
    }
    validate(doc, schema)


def test_valid_insufficient_evidence_empty_array(schema):
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "evidence": [],
    }
    validate(doc, schema)  # empty evidence[] is allowed for this verdict


def test_valid_insufficient_evidence_with_evidence(schema):
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/org/repo/issues/7",
                "quote": "Partial discussion, no conclusion.",
            }
        ],
    }
    validate(doc, schema)


def test_valid_contested(schema):
    doc = {
        "schema_version": "1",
        "verdict": "CONTESTED",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/team/issue/ENG-123",
                "quote": "Two engineers disagree on the root cause.",
            }
        ],
    }
    validate(doc, schema)


def test_valid_contested_empty_evidence(schema):
    doc = {
        "schema_version": "1",
        "verdict": "CONTESTED",
        "evidence": [],
    }
    validate(doc, schema)  # CONTESTED may have empty evidence


# ---------------------------------------------------------------------------
# All five source types are accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,ref",
    [
        ("web", "https://example.com"),
        ("code", "src/foo.py:10"),
        ("linear", "https://linear.app/team/issue/ENG-1"),
        ("github", "https://github.com/org/repo/issues/5"),
        ("notion", "https://notion.so/workspace/page-abc123"),
    ],
)
def test_valid_source_types(schema, source, ref):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [{"source": source, "ref": ref, "quote": "Supporting text."}],
    }
    validate(doc, schema)


# ---------------------------------------------------------------------------
# Invalid cases — must be REJECTED by the schema
# ---------------------------------------------------------------------------


def test_rejects_verified_true_empty_evidence(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_verified_false_empty_evidence(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_unknown_verdict(schema):
    doc = {
        "schema_version": "1",
        "verdict": "MAYBE",
        "evidence": [{"source": "web", "ref": "https://x.com", "quote": "x"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_unknown_source_type(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [{"source": "slack", "ref": "https://x.com", "quote": "x"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_missing_verdict(schema):
    doc = {
        "schema_version": "1",
        "evidence": [{"source": "web", "ref": "https://x.com", "quote": "x"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_missing_evidence_field(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_evidence_item_missing_quote(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [{"source": "web", "ref": "https://x.com"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_evidence_item_missing_ref(schema):
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [{"source": "web", "quote": "x"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


# ---------------------------------------------------------------------------
# Contract markdown checks
# ---------------------------------------------------------------------------


def test_contract_md_exists():
    assert CONTRACT_PATH.exists(), f"Missing: {CONTRACT_PATH}"


def test_contract_md_documents_all_source_types():
    text = CONTRACT_PATH.read_text()
    for source in ("web", "code", "linear", "github", "notion"):
        assert source in text, f"contract.md missing source type: {source}"


def test_contract_md_documents_propagation_rule():
    text = CONTRACT_PATH.read_text()
    assert "INSUFFICIENT_EVIDENCE" in text, "contract.md missing propagation rule section"
    assert "propagat" in text.lower(), "contract.md missing 'propagat' keyword for propagation rule"
