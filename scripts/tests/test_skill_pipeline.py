"""Tests for skill-pipeline.json workflow DAG."""

import json
import pathlib

import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
PIPELINE_FILE = SHARED / "skill-pipeline.json"
SKILLS_DIR = SHARED / "skills"

REQUIRED_SKILLS = {
    "to-seed", "to-tasks", "build-code", "debug", "pr-code-review",
    "grill-me", "grill-with-docs", "improve-skill-benchmarks", "prototype",
    "to-spec", "to-e2e-tasks", "dispatch-tasks", "pr-revise",
}


@pytest.fixture(scope="module")
def pipeline():
    return json.loads(PIPELINE_FILE.read_text())


@pytest.fixture(scope="module")
def existing_skills():
    return {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}


class TestPipelineFile:
    def test_file_exists(self):
        assert PIPELINE_FILE.exists(), f"Not found: {PIPELINE_FILE}"

    def test_valid_json(self):
        data = json.loads(PIPELINE_FILE.read_text())
        assert isinstance(data, dict)

    def test_has_skills_key(self, pipeline):
        assert "skills" in pipeline, "Top-level 'skills' key missing"

    def test_skills_is_dict(self, pipeline):
        assert isinstance(pipeline["skills"], dict)


class TestRequiredSkillsPresent:
    def test_all_required_skills_in_dag(self, pipeline):
        dag = pipeline["skills"]
        missing = REQUIRED_SKILLS - dag.keys()
        assert not missing, f"Missing from DAG: {sorted(missing)}"


class TestEdgeStructure:
    def test_each_entry_has_next_array(self, pipeline):
        for slug, entry in pipeline["skills"].items():
            assert "next" in entry, f"{slug}: missing 'next'"
            assert isinstance(entry["next"], list), f"{slug}: 'next' must be a list"

    def test_each_next_edge_has_skill_and_when(self, pipeline):
        for slug, entry in pipeline["skills"].items():
            for edge in entry["next"]:
                assert "skill" in edge, f"{slug}: edge missing 'skill'"
                assert "when" in edge, f"{slug}: edge missing 'when'"
                assert isinstance(edge["skill"], str) and edge["skill"], f"{slug}: 'skill' must be non-empty string"
                assert isinstance(edge["when"], str) and edge["when"], f"{slug}: 'when' must be non-empty string"


class TestSlugIntegrity:
    def test_all_source_slugs_exist_in_skills_dir(self, pipeline, existing_skills):
        dag = pipeline["skills"]
        bad = [s for s in dag.keys() if s not in existing_skills]
        assert not bad, f"Source slugs not in skills/: {bad}"

    def test_all_target_slugs_exist_in_skills_dir(self, pipeline, existing_skills):
        dag = pipeline["skills"]
        bad = []
        for slug, entry in dag.items():
            for edge in entry["next"]:
                target = edge["skill"]
                if target not in existing_skills:
                    bad.append(f"{slug} -> {target}")
        assert not bad, f"Target slugs not in skills/: {bad}"


class TestKnownEdges:
    def test_grill_me_leads_to_to_seed(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["grill-me"]["next"]]
        assert "to-seed" in targets

    def test_grill_with_docs_leads_to_to_seed(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["grill-with-docs"]["next"]]
        assert "to-seed" in targets

    def test_to_seed_leads_to_to_tasks(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["to-seed"]["next"]]
        assert "to-tasks" in targets

    def test_to_seed_leads_to_to_spec(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["to-seed"]["next"]]
        assert "to-spec" in targets

    def test_to_spec_leads_to_to_tasks(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["to-spec"]["next"]]
        assert "to-tasks" in targets

    def test_to_tasks_leads_to_dispatch_tasks(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["to-tasks"]["next"]]
        assert "dispatch-tasks" in targets

    def test_build_code_leads_to_to_e2e_tasks(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["build-code"]["next"]]
        assert "to-e2e-tasks" in targets

    def test_pr_revise_leads_to_to_tasks(self, pipeline):
        targets = [e["skill"] for e in pipeline["skills"]["pr-revise"]["next"]]
        assert "to-tasks" in targets

    def test_terminal_skills_have_empty_next(self, pipeline):
        terminal = ["pr-code-review", "improve-skill-benchmarks", "tdd", "handoff"]
        for slug in terminal:
            entry = pipeline["skills"][slug]
            assert entry["next"] == [], f"{slug}: expected empty next, got {entry['next']}"
