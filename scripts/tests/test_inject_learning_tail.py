"""Tests for upgraded inject-learning-tail.py."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
INJECTOR = SHARED / "scripts" / "learning" / "inject-learning-tail.py"
PIPELINE_FILE = SHARED / "skill-pipeline.json"
SKILLS_DIR = SHARED / "skills"

# Import the module under test for unit-level tests (hyphen in filename requires importlib)
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("inject_learning_tail", str(INJECTOR))
injector_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(injector_mod)


@pytest.fixture(scope="module")
def pipeline():
    return json.loads(PIPELINE_FILE.read_text())


# ── Unit: build_tail_block ────────────────────────────────────────────────────

class TestBuildTailBlock:
    def test_block_contains_skill_done_marker_with_slug(self):
        block = injector_mod.build_tail_block("debug", [])
        assert "<!-- skill-done: debug -->" in block

    def test_block_contains_learning_eval_marker_with_slug(self):
        block = injector_mod.build_tail_block("debug", [])
        assert "<!-- learning-eval: debug -->" in block

    def test_markers_use_correct_slug(self):
        block = injector_mod.build_tail_block("run-tasks", [])
        assert "<!-- skill-done: run-tasks -->" in block
        assert "<!-- learning-eval: run-tasks -->" in block

    def test_block_says_before_closing_suggestion(self):
        block = injector_mod.build_tail_block("debug", [])
        lower = block.lower()
        assert "before" in lower
        assert "closing" in lower or "final" in lower or "suggestion" in lower

    def test_block_does_not_say_after_the_run(self):
        block = injector_mod.build_tail_block("debug", [])
        assert "after the run" not in block.lower()

    def test_block_includes_next_step_for_one_edge(self):
        edges = [{"skill": "to-seed", "when": "decisions are crisp"}]
        block = injector_mod.build_tail_block("grill-me", edges)
        assert "to-seed" in block
        assert "decisions are crisp" in block

    def test_block_includes_next_steps_for_multiple_edges(self):
        edges = [
            {"skill": "to-tasks", "when": "ready to implement"},
            {"skill": "to-spec", "when": "want richer PRD"},
        ]
        block = injector_mod.build_tail_block("to-seed", edges)
        assert "to-tasks" in block
        assert "to-spec" in block
        assert "ready to implement" in block
        assert "want richer PRD" in block

    def test_block_for_terminal_skill_has_no_next_steps(self):
        block = injector_mod.build_tail_block("debug", [])
        # Terminal skill: no next-step prose
        assert "Next:" not in block or "to-" not in block

    def test_block_wrapped_in_sentinels(self):
        block = injector_mod.build_tail_block("debug", [])
        assert block.startswith("<!-- learning-capture:start -->")
        assert block.rstrip().endswith("<!-- learning-capture:end -->")


# ── Unit: validate_pipeline_slugs ─────────────────────────────────────────────

class TestValidatePipelineSlugs:
    def test_valid_pipeline_returns_empty_list(self, pipeline):
        bad = injector_mod.validate_pipeline_slugs(pipeline, str(SKILLS_DIR))
        assert bad == [], f"Unexpected invalid slugs: {bad}"

    def test_invalid_target_slug_detected(self):
        bad_pipeline = {
            "skills": {
                "debug": {"next": [{"skill": "nonexistent-skill-xyz", "when": "always"}]}
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            (pathlib.Path(tmpdir) / "debug").mkdir()
            bad = injector_mod.validate_pipeline_slugs(bad_pipeline, tmpdir)
        assert "nonexistent-skill-xyz" in bad

    def test_invalid_source_slug_detected(self):
        bad_pipeline = {
            "skills": {
                "ghost-skill": {"next": []}
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = injector_mod.validate_pipeline_slugs(bad_pipeline, tmpdir)
        assert "ghost-skill" in bad


# ── Integration: injector CLI ─────────────────────────────────────────────────

def _run_injector(args, skills_dir=None, pipeline_file=None):
    cmd = [sys.executable, str(INJECTOR)] + args
    env = os.environ.copy()
    if skills_dir:
        env["INJECT_SKILLS_DIR"] = str(skills_dir)
    if pipeline_file:
        env["INJECT_PIPELINE_FILE"] = str(pipeline_file)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


class TestInjectorCliValidation:
    def test_fails_loudly_on_missing_slug_in_pipeline(self, tmp_path):
        bad_pipeline = {
            "skills": {
                "debug": {"next": [{"skill": "nonexistent-xyz", "when": "always"}]}
            }
        }
        pipeline_path = tmp_path / "pipeline.json"
        pipeline_path.write_text(json.dumps(bad_pipeline))
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "debug").mkdir()
        result = _run_injector(
            ["--check"],
            skills_dir=skills_dir,
            pipeline_file=pipeline_path,
        )
        assert result.returncode != 0
        assert "nonexistent-xyz" in result.stderr or "nonexistent-xyz" in result.stdout


class TestInjectedBlockContent:
    def test_all_skill_dirs_have_correct_skill_done_marker(self):
        """After --apply, each SKILL.md has its own slug in the skill-done marker."""
        for entry in sorted(SKILLS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in injector_mod.PLUGIN_SKILLS:
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text()
            expected = f"<!-- skill-done: {entry.name} -->"
            assert expected in content, (
                f"{skill_md}: missing marker '{expected}'"
            )

    def test_all_skill_dirs_have_correct_learning_eval_marker(self):
        for entry in sorted(SKILLS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in injector_mod.PLUGIN_SKILLS:
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text()
            expected = f"<!-- learning-eval: {entry.name} -->"
            assert expected in content, (
                f"{skill_md}: missing marker '{expected}'"
            )

    def test_block_says_before_not_after(self):
        for entry in sorted(SKILLS_DIR.iterdir()):
            if not entry.is_dir() or entry.name in injector_mod.PLUGIN_SKILLS:
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text()
            # Find the learning-capture block
            start = content.find("<!-- learning-capture:start -->")
            end = content.find("<!-- learning-capture:end -->")
            if start == -1:
                continue
            block = content[start:end]
            assert "after the run" not in block.lower(), (
                f"{skill_md}: block still says 'after the run'"
            )


class TestIdempotency:
    def test_rerunning_check_shows_no_changes(self):
        """After --apply, --check should report 0 changes."""
        result = _run_injector(["--check"])
        assert result.returncode == 0
        assert "would change: 0" in result.stdout or "would change: 0" in result.stdout or \
               "CHANGE" not in result.stdout
