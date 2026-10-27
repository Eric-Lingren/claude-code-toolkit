"""Tests for attribution capture tail block in pr-code-review SKILL.md."""

import pathlib
import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
SKILL_FILE = SHARED / "skills" / "pr-code-review" / "SKILL.md"


@pytest.fixture(scope="module")
def content():
    return SKILL_FILE.read_text()


class TestAttributionBlockPresent:
    def test_attribution_block_exists(self, content):
        assert "<!-- attribution-capture:start -->" in content

    def test_attribution_block_end_marker(self, content):
        assert "<!-- attribution-capture:end -->" in content


class TestAttributionBlockContent:
    def test_references_attribution_tracer(self, content):
        assert "attribution-tracer" in content

    def test_references_artifact_grounding_judge(self, content):
        assert "artifact-grounding-judge" in content

    def test_iterates_per_confirmed_issue(self, content):
        start = content.find("<!-- attribution-capture:start -->")
        end = content.find("<!-- attribution-capture:end -->")
        block = content[start:end]
        lower = block.lower()
        assert "each" in lower or "per" in lower or "once per" in lower or "for each" in lower

    def test_says_before_closing_suggestion(self, content):
        start = content.find("<!-- attribution-capture:start -->")
        end = content.find("<!-- attribution-capture:end -->")
        block = content[start:end]
        lower = block.lower()
        assert "before" in lower


class TestNoMarkerDuplication:
    def test_skill_done_marker_appears_exactly_once(self, content):
        count = content.count("<!-- skill-done: pr-code-review -->")
        assert count == 1, f"skill-done: pr-code-review appears {count} times (expected 1)"

    def test_learning_eval_marker_appears_exactly_once(self, content):
        count = content.count("<!-- learning-eval: pr-code-review -->")
        assert count == 1, f"learning-eval: pr-code-review appears {count} times (expected 1)"


class TestBlockIsAdditive:
    def test_learning_capture_block_still_present(self, content):
        assert "<!-- learning-capture:start -->" in content
        assert "<!-- learning-capture:end -->" in content

    def test_attribution_block_distinct_from_learning_capture(self, content):
        attr_start = content.find("<!-- attribution-capture:start -->")
        lc_start = content.find("<!-- learning-capture:start -->")
        assert attr_start != lc_start
