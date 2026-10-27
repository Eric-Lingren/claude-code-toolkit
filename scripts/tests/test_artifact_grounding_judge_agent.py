"""Tests for artifact-grounding-judge.md agent contract."""

import pathlib
import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
AGENT_FILE = SHARED / "agents" / "artifact-grounding-judge.md"


@pytest.fixture(scope="module")
def content():
    return AGENT_FILE.read_text()


class TestFileExists:
    def test_agent_file_exists(self):
        assert AGENT_FILE.exists(), f"Not found: {AGENT_FILE}"


class TestInputContract:
    def test_accepts_draft_attribution_record(self, content):
        assert "attribution" in content.lower()

    def test_accepts_evidence_array(self, content):
        assert "evidence" in content


class TestOutputContract:
    def test_specifies_pass_verdict(self, content):
        assert "pass" in content

    def test_specifies_rejected_verdict(self, content):
        assert "rejected" in content or "reject" in content.lower()

    def test_specifies_confidence_stamping(self, content):
        assert "confidence" in content

    def test_specifies_reason_on_rejection(self, content):
        assert "reason" in content


class TestVerificationLogic:
    def test_checks_positive_anchor(self, content):
        lower = content.lower()
        assert "positive" in lower or "quote" in lower or "anchor" in lower

    def test_checks_absence_anchor(self, content):
        lower = content.lower()
        assert "absence" in lower or "absent" in lower or "never appear" in lower or "not appear" in lower

    def test_rejects_fabricated_evidence(self, content):
        lower = content.lower()
        assert "fabricat" in lower or "not found" in lower or "does not match" in lower or "reject" in lower


class TestWriteOnPass:
    def test_calls_log_learning_on_pass(self, content):
        assert "log-learning.py" in content

    def test_does_not_call_log_learning_on_rejection(self, content):
        assert "rejected" in content or "reject" in content.lower()


class TestSeparationOfConcerns:
    def test_forbids_self_grading(self, content):
        lower = content.lower()
        assert "self" in lower or "itself" in lower or "draft" in lower
