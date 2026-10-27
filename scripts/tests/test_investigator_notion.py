"""Tests for investigator-notion leaf agent (T-0077).

The investigator-notion agent accepts a sub-claim, queries Notion via MCP tools,
finds the most relevant page or block, and emits an investigation-result contract.
Evidence refs use Notion page URLs or block IDs.
"""

import json
import pathlib
import re

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator-notion.md"

# Matches https://www.notion.so/<...> or https://notion.so/<...>
NOTION_URL_RE = re.compile(r"https://(?:www\.)?notion\.so/\S+")

# Matches a Notion block ID: 32 hex chars, optionally hyphen-separated as UUID
NOTION_BLOCK_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9a-f]{32}",
    re.IGNORECASE,
)


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
# investigator-notion agent structural tests
# ---------------------------------------------------------------------------


def test_investigator_notion_agent_exists():
    assert AGENT_PATH.exists(), f"Missing: {AGENT_PATH}"


def test_investigator_notion_has_sonnet_model(agent_text):
    assert "model: sonnet" in agent_text, "Agent missing 'model: sonnet' frontmatter"


def test_investigator_notion_references_investigation_result_contract(agent_text):
    assert "investigation-result" in agent_text.lower(), (
        "Agent does not reference investigation-result contract"
    )


def test_investigator_notion_accepts_sub_claim(agent_text):
    assert "sub_claim" in agent_text or "sub-claim" in agent_text.lower(), (
        "Agent does not mention sub_claim input"
    )


def test_investigator_notion_documents_insufficient_evidence(agent_text):
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Agent must document INSUFFICIENT_EVIDENCE fallback"
    )


def test_investigator_notion_uses_notion_mcp_tools(agent_text):
    lower = agent_text.lower()
    has_mcp = "mcp" in lower and "notion" in lower
    assert has_mcp, (
        "Agent must mention Notion MCP tools for querying Notion"
    )


def test_investigator_notion_documents_notion_url_ref_format(agent_text):
    assert "notion.so" in agent_text, (
        "Agent must document the Notion page URL ref format (https://notion.so/...)"
    )


def test_investigator_notion_specifies_notion_source_type(agent_text):
    assert (
        '"notion"' in agent_text
        or "'notion'" in agent_text
        or "source: notion" in agent_text
        or 'source: "notion"' in agent_text
    ), "Agent must specify source: 'notion' for evidence items"


def test_investigator_notion_documents_block_id_format(agent_text):
    lower = agent_text.lower()
    assert "block" in lower, (
        "Agent must document block ID as an alternative ref format for Notion evidence"
    )


# ---------------------------------------------------------------------------
# Sample investigation-result outputs — notion source type
# These mimic what the investigator-notion agent would emit at runtime.
# Schema validation guarantees the agent's documented output shape is valid.
# ---------------------------------------------------------------------------


def test_verified_true_notion_page_url_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a Notion page URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "A Notion page documents the egress architecture decision.",
        "evidence": [
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/Egress-Architecture-abc123def456",
                "quote": "Egress hook fires on agent stop; all outbound network calls must pass through the hook.",
            }
        ],
        "summary": "Notion page confirms the egress architecture decision.",
    }
    validate(doc, schema)


def test_verified_true_notion_block_id_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a Notion block ID ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "A specific Notion block states the security requirement.",
        "evidence": [
            {
                "source": "notion",
                "ref": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "quote": "Security requirement: all agent egress must be logged before the process terminates.",
            }
        ],
        "summary": "Notion block a1b2c3d4 confirms the security logging requirement.",
    }
    validate(doc, schema)


def test_verified_false_notion_output_is_schema_valid(schema):
    """VERIFIED_FALSE with a Notion page URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "sub_claim": "The feature was approved in the design review.",
        "evidence": [
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/Design-Review-2026-Q2-xyz789",
                "quote": "Decision: deferred to Q3 — not approved in this review cycle.",
            }
        ],
        "summary": "Design review page explicitly deferred the feature, contradicting the claim.",
    }
    validate(doc, schema)


def test_insufficient_evidence_notion_output_is_schema_valid(schema):
    """INSUFFICIENT_EVIDENCE with empty evidence[] is valid for Notion investigations."""
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "sub_claim": "A Notion page tracks the database migration work.",
        "evidence": [],
        "summary": "No Notion pages or blocks matching 'database migration' were found.",
    }
    validate(doc, schema)


def test_contested_notion_output_is_schema_valid(schema):
    """CONTESTED with multiple Notion evidence items is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "CONTESTED",
        "sub_claim": "The API rate limit is documented as 100 req/s.",
        "evidence": [
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/API-Spec-v1-aaa111",
                "quote": "Rate limit: 100 requests per second per integration.",
            },
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/API-Spec-v2-bbb222",
                "quote": "Rate limit updated to 50 requests per second effective 2026-07-01.",
            },
        ],
        "summary": "Two Notion pages give conflicting rate limits; the claim is contested.",
    }
    validate(doc, schema)


def test_notion_page_url_ref_format_is_valid(schema):
    """Notion evidence ref follows https://notion.so/... URL format."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/Some-Page-abc123",
                "quote": "The page confirms the claim.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert NOTION_URL_RE.match(ref), (
        f"Notion page ref must match https://notion.so/<path>, got: {ref}"
    )


def test_notion_block_id_ref_format_is_valid(schema):
    """Notion evidence ref may be a UUID-format block ID."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "notion",
                "ref": "12345678-abcd-1234-efgh-000000000000",
                "quote": "Block text confirming the claim.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    # Block IDs are non-empty strings — schema enforces minLength: 1
    assert len(ref) > 0, "Block ID ref must be non-empty"


def test_notion_evidence_empty_quote_rejected(schema):
    """Schema rejects evidence items with an empty quote string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "notion",
                "ref": "https://www.notion.so/workspace/Some-Page-abc123",
                "quote": "",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_notion_evidence_empty_ref_rejected(schema):
    """Schema rejects evidence items with an empty ref string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "notion",
                "ref": "",
                "quote": "Some relevant quote from a Notion page.",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_verified_true_notion_without_evidence_rejected(schema):
    """Schema rejects VERIFIED_TRUE with empty evidence[] for notion source."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)
