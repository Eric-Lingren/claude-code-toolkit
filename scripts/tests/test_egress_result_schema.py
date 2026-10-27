"""Tests for egress-result-schema.json and egress-result-contract.md."""

import json
import pathlib

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "egress-result-schema.json"
CONTRACT_PATH = SHARED / "contracts" / "egress-result-contract.md"


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


def test_schema_has_required_fields(schema):
    props = schema.get("properties", {})
    assert "posted" in props
    assert "url" in props
    assert "thread_id" in props
    assert "status" in props
    assert "schema_version" in props


def test_schema_status_enum_values(schema):
    status_enum = schema["properties"]["status"]["enum"]
    assert "copy-only" in status_enum
    assert "posted" in status_enum
    assert "failed" in status_enum


# ---------------------------------------------------------------------------
# Valid: copy-only stub result
# ---------------------------------------------------------------------------


def test_valid_copy_only_stub(schema):
    """Acceptance criterion: validates a copy-only stub result."""
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": None,
        "thread_id": None,
        "status": "copy-only",
    }
    validate(doc, schema)  # must not raise


def test_valid_copy_only_without_thread_id(schema):
    """thread_id is optional; copy-only stub may omit it."""
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": None,
        "status": "copy-only",
    }
    validate(doc, schema)


# ---------------------------------------------------------------------------
# Valid: posted result with non-null url and thread_id
# ---------------------------------------------------------------------------


def test_valid_posted_result(schema):
    """Acceptance criterion: validates a posted result with non-null url and thread_id."""
    doc = {
        "schema_version": "1",
        "posted": True,
        "url": "https://slack.com/archives/C12345/p1234567890",
        "thread_id": "1234567890.123456",
        "status": "posted",
    }
    validate(doc, schema)  # must not raise


# ---------------------------------------------------------------------------
# Valid: failed result
# ---------------------------------------------------------------------------


def test_valid_failed_result(schema):
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": None,
        "thread_id": None,
        "status": "failed",
    }
    validate(doc, schema)


# ---------------------------------------------------------------------------
# Invalid cases — must be REJECTED by the schema
# ---------------------------------------------------------------------------


def test_rejects_missing_status(schema):
    """Acceptance criterion: rejects a result missing the status field."""
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": None,
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_missing_posted(schema):
    doc = {
        "schema_version": "1",
        "url": None,
        "status": "copy-only",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_missing_schema_version(schema):
    doc = {
        "posted": False,
        "url": None,
        "status": "copy-only",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_unknown_status(schema):
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": None,
        "status": "pending",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_posted_true_with_null_url(schema):
    """When status is 'posted', url must be a non-null string."""
    doc = {
        "schema_version": "1",
        "posted": True,
        "url": None,
        "status": "posted",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_copy_only_with_posted_true(schema):
    """When status is 'copy-only', posted must be false."""
    doc = {
        "schema_version": "1",
        "posted": True,
        "url": None,
        "status": "copy-only",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_rejects_copy_only_with_non_null_url(schema):
    """When status is 'copy-only', url must be null."""
    doc = {
        "schema_version": "1",
        "posted": False,
        "url": "https://slack.com/archives/C12345/p1234567890",
        "status": "copy-only",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


# ---------------------------------------------------------------------------
# Contract markdown checks
# ---------------------------------------------------------------------------


def test_contract_md_exists():
    assert CONTRACT_PATH.exists(), f"Missing: {CONTRACT_PATH}"


def test_contract_md_documents_copy_only_behavior():
    text = CONTRACT_PATH.read_text()
    assert "copy-only" in text, "contract.md missing copy-only stub behavior"


def test_contract_md_documents_live_adapter_shape():
    text = CONTRACT_PATH.read_text()
    assert "url" in text
    assert "thread_id" in text


def test_contract_md_documents_all_status_values():
    text = CONTRACT_PATH.read_text()
    assert "copy-only" in text
    assert "posted" in text
    assert "failed" in text
