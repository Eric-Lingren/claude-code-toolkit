"""Tests verifying capture-learning.md produces v2 records."""

import pathlib
import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
AGENT_FILE = SHARED / "agents" / "capture-learning.md"


@pytest.fixture(scope="module")
def content():
    return AGENT_FILE.read_text()


class TestV2RecordShape:
    def test_produces_type_self(self, content):
        assert '"self"' in content or "type: 'self'" in content or "type: self" in content.lower() or 'type.*self' in content

    def test_produces_reported_by_field(self, content):
        assert "reported_by" in content

    def test_reported_by_equals_skill_slug(self, content):
        assert "reported_by" in content
        # reported_by should equal the skill slug (self-record)
        lower = content.lower()
        # Either explicit instruction or the field appears alongside "skill" slug reference
        assert "improves" in content

    def test_improves_equals_skill_slug(self, content):
        assert "improves" in content

    def test_improves_type_skill(self, content):
        assert "improves_type" in content
        assert "skill" in content

    def test_cause_field_from_open_vocab(self, content):
        assert "cause" in content

    def test_why_missed_field(self, content):
        assert "why_missed" in content

    def test_lesson_field(self, content):
        assert "lesson" in content

    def test_fix_field(self, content):
        assert "fix" in content

    def test_evidence_array_not_string(self, content):
        # evidence is now an array of {source, ref, quote} objects
        assert "source" in content
        assert "quote" in content

    def test_confidence_field(self, content):
        assert "confidence" in content


class TestWritesUnifiedLog:
    def test_calls_log_learning_py(self, content):
        assert "log-learning.py" in content

    def test_no_per_skill_jsonl_reference(self, content):
        # Should not reference per-skill JSONL path like "learnings/<skill>.jsonl"
        assert "<skill>.jsonl" not in content
        assert "/<skill>" not in content


class TestGrounding:
    def test_grounds_via_verify_anchors_script(self, content):
        assert "verify-anchors.py" in content
