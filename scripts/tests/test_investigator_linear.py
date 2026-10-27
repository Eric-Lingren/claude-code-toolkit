"""Tests for investigator-linear leaf agent (T-0075).

The investigator-linear agent accepts a sub-claim, queries Linear via MCP tools,
and emits an investigation-result contract. Evidence refs use Linear ticket URLs.
"""

import json
import pathlib
import re

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator-linear.md"

LINEAR_URL_RE = re.compile(r"https://linear\.app/[^/]+/issue/[A-Z]+-\d+")


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
# investigator-linear agent structural tests
# ---------------------------------------------------------------------------


def test_investigator_linear_agent_exists():
    assert AGENT_PATH.exists(), f"Missing: {AGENT_PATH}"


def test_investigator_linear_has_sonnet_model(agent_text):
    assert "model: sonnet" in agent_text, "Agent missing 'model: sonnet' frontmatter"


def test_investigator_linear_references_investigation_result_contract(agent_text):
    assert "investigation-result" in agent_text.lower(), (
        "Agent does not reference investigation-result contract"
    )


def test_investigator_linear_accepts_sub_claim(agent_text):
    assert "sub_claim" in agent_text or "sub-claim" in agent_text.lower(), (
        "Agent does not mention sub_claim input"
    )


def test_investigator_linear_documents_insufficient_evidence(agent_text):
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Agent must document INSUFFICIENT_EVIDENCE fallback"
    )


def test_investigator_linear_uses_linear_mcp_tools(agent_text):
    lower = agent_text.lower()
    has_mcp = "mcp" in lower
    has_linear_tool = "linear" in lower
    assert has_mcp or has_linear_tool, (
        "Agent must mention Linear MCP tools for querying Linear"
    )


def test_investigator_linear_documents_linear_url_ref_format(agent_text):
    assert "linear.app" in agent_text, (
        "Agent must document the Linear ticket URL ref format (https://linear.app/...)"
    )


def test_investigator_linear_specifies_linear_source_type(agent_text):
    assert (
        '"linear"' in agent_text
        or "'linear'" in agent_text
        or "source: linear" in agent_text
        or 'source: "linear"' in agent_text
    ), "Agent must specify source: 'linear' for evidence items"


# ---------------------------------------------------------------------------
# Sample investigation-result outputs — linear source type
# These mimic what the investigator-linear agent would emit at runtime.
# Schema validation guarantees the agent's documented output shape is valid.
# ---------------------------------------------------------------------------


def test_verified_true_linear_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a Linear ticket URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "The egress hook was added in the security sprint.",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/SEC-42",
                "quote": "Added stop hook to capture outbound network events before exfil can occur.",
            }
        ],
        "summary": "SEC-42 confirms the egress hook was introduced during the security sprint.",
    }
    validate(doc, schema)


def test_verified_false_linear_output_is_schema_valid(schema):
    """VERIFIED_FALSE with a Linear ticket URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "sub_claim": "The feature was shipped in v1.0.",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/ENG-100",
                "quote": "Closed as won't-do in v1.0; deferred to v2.0 backlog.",
            }
        ],
        "summary": "ENG-100 shows the feature was explicitly deferred, not shipped in v1.0.",
    }
    validate(doc, schema)


def test_insufficient_evidence_linear_output_is_schema_valid(schema):
    """INSUFFICIENT_EVIDENCE with empty evidence[] is valid for Linear investigations."""
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "sub_claim": "A Linear issue exists tracking the database migration.",
        "evidence": [],
        "summary": "No Linear issues matching 'database migration' were found.",
    }
    validate(doc, schema)


def test_linear_evidence_ref_uses_linear_app_url_format(schema):
    """Linear evidence ref follows https://linear.app/<team>/issue/<ID> format."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/ENG-512",
                "quote": "Closing — confirmed the bug is reproducible on v2 only.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert ref.startswith("https://linear.app/"), (
        f"Linear ref must start with 'https://linear.app/', got: {ref}"
    )
    assert LINEAR_URL_RE.match(ref), (
        f"Linear ref must match https://linear.app/<team>/issue/<ID> pattern, got: {ref}"
    )


def test_linear_evidence_empty_quote_rejected(schema):
    """Schema rejects evidence items with an empty quote string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/ENG-1",
                "quote": "",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_linear_evidence_empty_ref_rejected(schema):
    """Schema rejects evidence items with an empty ref string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "linear",
                "ref": "",
                "quote": "Some relevant quote from the ticket.",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_verified_true_linear_without_evidence_rejected(schema):
    """Schema rejects VERIFIED_TRUE with empty evidence[] for linear source."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_contested_linear_output_is_schema_valid(schema):
    """CONTESTED with multiple Linear evidence items is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "CONTESTED",
        "sub_claim": "The root cause was the misconfigured load balancer.",
        "evidence": [
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/OPS-77",
                "quote": "Root cause confirmed: load balancer health check was misconfigured.",
            },
            {
                "source": "linear",
                "ref": "https://linear.app/acme/issue/OPS-78",
                "quote": "After further analysis, the database connection pool was the actual bottleneck.",
            },
        ],
        "summary": "Two Linear issues give conflicting root causes; no single authoritative answer.",
    }
    validate(doc, schema)
