"""Tests for attribution-tracer.md agent contract."""

import pathlib
import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
AGENT_FILE = SHARED / "agents" / "attribution-tracer.md"


@pytest.fixture(scope="module")
def content():
    return AGENT_FILE.read_text()


class TestFileExists:
    def test_agent_file_exists(self):
        assert AGENT_FILE.exists(), f"Not found: {AGENT_FILE}"


class TestInputContract:
    def test_specifies_issue_description_input(self, content):
        assert "issue_description" in content

    def test_specifies_fix_input(self, content):
        assert "fix" in content

    def test_specifies_transcript_path_input(self, content):
        assert "transcript_path" in content

    def test_specifies_optional_seed_path(self, content):
        assert "seed_path" in content

    def test_specifies_optional_tasks_path(self, content):
        assert "tasks_path" in content

    def test_specifies_optional_branch(self, content):
        assert "branch" in content

    def test_specifies_optional_pr_url(self, content):
        assert "pr_url" in content


class TestOutputContract:
    def test_specifies_v2_attribution_record_output(self, content):
        assert "attribution" in content

    def test_specifies_pre_grounding_output(self, content):
        # Draft goes to artifact-grounding-judge before writing
        assert "artifact-grounding-judge" in content


class TestProvenanceChain:
    def test_describes_transcript_handoff_step(self, content):
        assert "transcript" in content.lower()

    def test_describes_tasks_file_step(self, content):
        assert "tasks" in content.lower()

    def test_describes_seed_step(self, content):
        assert "seed" in content.lower()

    def test_describes_code_diff_step(self, content):
        assert "diff" in content.lower() or "pr" in content.lower() or "branch" in content.lower()

    def test_provenance_chain_order_transcript_before_tasks(self, content):
        transcript_pos = content.lower().find("transcript")
        tasks_pos = content.lower().find("tasks")
        assert transcript_pos < tasks_pos, "transcript handoff step should appear before tasks step"


class TestCauseVocabulary:
    def test_includes_requirement_lost_between_docs(self, content):
        assert "requirement_lost_between_docs" in content

    def test_includes_context_lost_in_handoff(self, content):
        assert "context_lost_in_handoff" in content

    def test_includes_other_escape_hatch(self, content):
        assert "other" in content

    def test_includes_cause_label_for_other(self, content):
        assert "cause_label" in content


class TestDemoteOnDoubt:
    def test_includes_demote_on_doubt_rule(self, content):
        lower = content.lower()
        assert "demote" in lower or "candidate" in lower

    def test_candidate_confidence_mentioned(self, content):
        assert "candidate" in content

    def test_improves_null_when_uncertain(self, content):
        assert "null" in content
