"""Tests for agents/egress/ channel structure and copy-only post-comment stubs.

Verifies:
- Channel subdirectories exist: github/, linear/, notion/, slack/
- Stub files exist: post-github.md, post-linear.md, post-slack.md
- Each stub contains the 'NO EXTERNAL CALLS MADE' banner
- Each stub contains a STUB marker HTML comment
- Each stub's example egress-result JSON block validates against the schema
"""

import json
import pathlib
import re

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
EGRESS_DIR = SHARED / "agents" / "egress"
SCHEMA_PATH = SHARED / "contracts" / "egress-result-schema.json"

CHANNEL_DIRS = ["github", "linear", "notion", "slack"]
STUB_FILES = {
    "github": "post-github.md",
    "linear": "post-linear.md",
    "slack": "post-slack.md",
}


@pytest.fixture(scope="module")
def schema():
    assert SCHEMA_PATH.exists(), f"Schema not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNEL_DIRS)
def test_channel_directory_exists(channel):
    d = EGRESS_DIR / channel
    assert d.is_dir(), f"Missing channel directory: {d}"


# ---------------------------------------------------------------------------
# Stub files exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel,filename", STUB_FILES.items())
def test_stub_file_exists(channel, filename):
    stub = EGRESS_DIR / channel / filename
    assert stub.exists(), f"Missing stub file: {stub}"


# ---------------------------------------------------------------------------
# Banner — 'NO EXTERNAL CALLS MADE'
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel,filename", STUB_FILES.items())
def test_stub_contains_no_external_calls_banner(channel, filename):
    text = (EGRESS_DIR / channel / filename).read_text()
    assert "NO EXTERNAL CALLS MADE" in text, (
        f"{filename}: missing 'NO EXTERNAL CALLS MADE' banner"
    )


# ---------------------------------------------------------------------------
# STUB marker HTML comment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel,filename", STUB_FILES.items())
def test_stub_contains_stub_marker(channel, filename):
    text = (EGRESS_DIR / channel / filename).read_text()
    assert "<!-- STUB:" in text, (
        f"{filename}: missing '<!-- STUB:' marker comment"
    )


# ---------------------------------------------------------------------------
# Schema-valid copy-only egress-result in example block
# ---------------------------------------------------------------------------


def _extract_json_block(text: str) -> dict:
    """Pull the first JSON code block from a markdown file."""
    pattern = r"```json\s*([\s\S]*?)```"
    match = re.search(pattern, text)
    assert match, "No ```json block found in stub"
    return json.loads(match.group(1).strip())


@pytest.mark.parametrize("channel,filename", STUB_FILES.items())
def test_stub_example_is_schema_valid_copy_only(channel, filename, schema):
    text = (EGRESS_DIR / channel / filename).read_text()
    result = _extract_json_block(text)
    jsonschema.validate(result, schema)
    assert result["status"] == "copy-only", (
        f"{filename}: example egress-result status must be 'copy-only', got {result['status']!r}"
    )
    assert result["posted"] is False, (
        f"{filename}: example egress-result posted must be False for copy-only"
    )
    assert result.get("url") is None, (
        f"{filename}: example egress-result url must be null for copy-only"
    )
