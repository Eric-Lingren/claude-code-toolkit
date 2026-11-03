"""Tests for attribution capture tail block in debug SKILL.md."""

import pathlib
import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
SKILL_FILE = SHARED / "skills" / "debug" / "SKILL.md"


@pytest.fixture(scope="module")
def content():
    return SKILL_FILE.read_text()


class TestAttributionBlockPresent:
    def test_attribution_block_exists(self, content):
        assert "<!-- attribution-capture:start -->" in content

    def test_attribution_block_end_marker(self, content):
        assert "<!-- attribution-capture:end -->" in content


RESOURCE_FILE = SHARED / "resources" / "attribution-capture.md"


@pytest.fixture(scope="module")
def resource():
    return RESOURCE_FILE.read_text()


class TestAttributionBlockContent:
    """The skill block only points at the shared resource; the instructions live there."""

    def test_block_references_shared_resource(self, content):
        start = content.find("<!-- attribution-capture:start -->")
        end = content.find("<!-- attribution-capture:end -->")
        assert "resources/attribution-capture.md" in content[start:end]

    def test_references_attribution_tracer(self, resource):
        assert "attribution-tracer" in resource

    def test_references_artifact_grounding_judge(self, resource):
        assert "artifact-grounding-judge" in resource

    def test_fires_per_confirmed_root_cause(self, content):
        lower = content.lower()
        assert "root cause" in lower or "confirmed" in lower

    def test_says_before_closing_suggestion(self, resource):
        assert "before" in resource.lower()

    def test_collects_repo_pointers(self, resource):
        assert "transcript" in resource.lower()


class TestNoMarkerDuplication:
    def test_skill_done_marker_appears_exactly_once(self, content):
        count = content.count("<!-- skill-done: debug -->")
        assert count == 1, f"skill-done: debug appears {count} times (expected 1)"

    def test_learning_eval_marker_defined_once_in_shared_resource(self):
        # The marker is printed at run time per the shared resource, not baked into the skill.
        text = (SHARED / "resources" / "learning-capture.md").read_text()
        assert text.count("<!-- learning-eval: <slug> -->") == 1


class TestBlockIsAdditive:
    def test_learning_capture_block_still_present(self, content):
        assert "<!-- learning-capture:start -->" in content
        assert "<!-- learning-capture:end -->" in content

    def test_attribution_block_distinct_from_learning_capture(self, content):
        # Different sentinel markers
        assert "<!-- attribution-capture:start -->" != "<!-- learning-capture:start -->"
        attr_start = content.find("<!-- attribution-capture:start -->")
        lc_start = content.find("<!-- learning-capture:start -->")
        assert attr_start != lc_start, "Attribution block and learning-capture block must be separate"
