"""Tests for investigator-github leaf agent (T-0076).

The investigator-github agent accepts a sub-claim, queries GitHub via gh CLI or
GitHub MCP tools, finds the most relevant issue or PR, and emits an
investigation-result contract. Evidence refs use GitHub issue/PR URLs.
"""

import json
import pathlib
import re

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator-github.md"

# Matches https://github.com/<owner>/<repo>/issues/<N> or .../pull/<N>
GITHUB_URL_RE = re.compile(
    r"https://github\.com/[^/]+/[^/]+/(issues|pull)/\d+"
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
# investigator-github agent structural tests
# ---------------------------------------------------------------------------


def test_investigator_github_agent_exists():
    assert AGENT_PATH.exists(), f"Missing: {AGENT_PATH}"


def test_investigator_github_has_sonnet_model(agent_text):
    assert "model: sonnet" in agent_text, "Agent missing 'model: sonnet' frontmatter"


def test_investigator_github_references_investigation_result_contract(agent_text):
    assert "investigation-result" in agent_text.lower(), (
        "Agent does not reference investigation-result contract"
    )


def test_investigator_github_accepts_sub_claim(agent_text):
    assert "sub_claim" in agent_text or "sub-claim" in agent_text.lower(), (
        "Agent does not mention sub_claim input"
    )


def test_investigator_github_documents_insufficient_evidence(agent_text):
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Agent must document INSUFFICIENT_EVIDENCE fallback"
    )


def test_investigator_github_uses_gh_cli_or_mcp(agent_text):
    lower = agent_text.lower()
    has_gh_cli = "gh " in lower or "gh cli" in lower or "`gh`" in lower
    has_mcp = "mcp" in lower
    assert has_gh_cli or has_mcp, (
        "Agent must mention gh CLI or GitHub MCP tools for querying GitHub"
    )


def test_investigator_github_documents_github_url_ref_format(agent_text):
    assert "github.com" in agent_text, (
        "Agent must document the GitHub issue/PR URL ref format (https://github.com/...)"
    )


def test_investigator_github_specifies_github_source_type(agent_text):
    assert (
        '"github"' in agent_text
        or "'github'" in agent_text
        or "source: github" in agent_text
        or 'source: "github"' in agent_text
    ), "Agent must specify source: 'github' for evidence items"


# ---------------------------------------------------------------------------
# Sample investigation-result outputs — github source type
# These mimic what the investigator-github agent would emit at runtime.
# Schema validation guarantees the agent's documented output shape is valid.
# ---------------------------------------------------------------------------


def test_verified_true_github_issue_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a GitHub issue URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "An issue was filed tracking the egress hook regression.",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/42",
                "quote": "Egress hook stops firing after the v2 agent upgrade; all outbound events silently dropped.",
            }
        ],
        "summary": "Issue #42 confirms the egress hook regression was tracked.",
    }
    validate(doc, schema)


def test_verified_true_github_pr_output_is_schema_valid(schema):
    """VERIFIED_TRUE with a GitHub PR URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "sub_claim": "The fix was merged via a pull request before the release.",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/pull/88",
                "quote": "Merged on 2026-07-15 — restores egress hook invocation on agent stop.",
            }
        ],
        "summary": "PR #88 was merged and contains the fix for the egress hook.",
    }
    validate(doc, schema)


def test_verified_false_github_output_is_schema_valid(schema):
    """VERIFIED_FALSE with a GitHub issue URL ref is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_FALSE",
        "sub_claim": "The feature was shipped in v1.0.",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/100",
                "quote": "Closed as won't-fix for v1.0; deferred to the v2.0 milestone.",
            }
        ],
        "summary": "Issue #100 shows the feature was explicitly deferred, not shipped in v1.0.",
    }
    validate(doc, schema)


def test_insufficient_evidence_github_output_is_schema_valid(schema):
    """INSUFFICIENT_EVIDENCE with empty evidence[] is valid for GitHub investigations."""
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "sub_claim": "A GitHub issue exists tracking the database migration.",
        "evidence": [],
        "summary": "No GitHub issues or PRs matching 'database migration' were found.",
    }
    validate(doc, schema)


def test_contested_github_output_is_schema_valid(schema):
    """CONTESTED with multiple GitHub evidence items is schema-valid."""
    doc = {
        "schema_version": "1",
        "verdict": "CONTESTED",
        "sub_claim": "The root cause was the misconfigured load balancer.",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/77",
                "quote": "Root cause confirmed: load balancer health check was misconfigured.",
            },
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/78",
                "quote": "After further analysis, the database connection pool was the actual bottleneck.",
            },
        ],
        "summary": "Two GitHub issues give conflicting root causes; no single authoritative answer.",
    }
    validate(doc, schema)


def test_github_issue_ref_format_is_valid(schema):
    """GitHub evidence ref follows https://github.com/<owner>/<repo>/issues/<N> format."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/512",
                "quote": "Closing — confirmed the bug is reproducible on v2 only.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert ref.startswith("https://github.com/"), (
        f"GitHub ref must start with 'https://github.com/', got: {ref}"
    )
    assert GITHUB_URL_RE.match(ref), (
        f"GitHub ref must match https://github.com/<owner>/<repo>/(issues|pull)/<N>, got: {ref}"
    )


def test_github_pr_ref_format_is_valid(schema):
    """GitHub evidence ref follows https://github.com/<owner>/<repo>/pull/<N> format."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/pull/99",
                "quote": "Merged: adds retry logic to the agent stop lifecycle.",
            }
        ],
    }
    validate(doc, schema)
    ref = doc["evidence"][0]["ref"]
    assert GITHUB_URL_RE.match(ref), (
        f"GitHub PR ref must match https://github.com/<owner>/<repo>/pull/<N>, got: {ref}"
    )


def test_github_evidence_empty_quote_rejected(schema):
    """Schema rejects evidence items with an empty quote string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/acme/infra/issues/1",
                "quote": "",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_github_evidence_empty_ref_rejected(schema):
    """Schema rejects evidence items with an empty ref string."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "github",
                "ref": "",
                "quote": "Some relevant quote from the issue.",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)


def test_verified_true_github_without_evidence_rejected(schema):
    """Schema rejects VERIFIED_TRUE with empty evidence[] for github source."""
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(doc, schema)
