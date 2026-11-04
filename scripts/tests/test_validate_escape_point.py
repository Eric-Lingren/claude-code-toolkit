"""Tests for validate-escape-point.py and its wiring into log-learning.py."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SHARED = HERE.parents[1]
VALIDATOR = SHARED / "scripts" / "learning" / "validate-escape-point.py"
LOG_LEARNING = SHARED / "scripts" / "learning" / "log-learning.py"

SKILL_MD = """# Debug

Intro.

## Instrument

Add logs.

### Nested Step

Deeper.

```
## Not A Heading
```

## Fix
"""

MINIMAL_ATTRIBUTION = {
    "type": "attribution",
    "reported_by": "debug",
    "improves": "debug",
    "improves_type": "skill",
    "cause": "requirement_lost_between_docs",
    "cause_label": None,
    "problem": "Criterion lost.",
    "why_missed": "Never seen.",
    "lesson": "Check it.",
    "fix": "Add step.",
    "evidence": [{"source": "artifact", "ref": "docs/seeds/foo.json", "quote": "q"}],
    "trace": {"seed": "docs/seeds/foo.json", "branch": "feat/bar"},
    "confidence": "candidate",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "skills" / "debug").mkdir(parents=True)
    (root / "skills" / "debug" / "SKILL.md").write_text(SKILL_MD)
    return root


def validate(repo, proposal):
    res = subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(repo)],
        input=json.dumps(proposal), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


class TestValidator:
    def test_valid_path_and_heading_unchanged(self, repo):
        p = {"path": "skills/debug/SKILL.md", "section": "## Instrument"}
        assert validate(repo, p) == p

    def test_missing_heading_nulls_section(self, repo):
        out = validate(repo, {"path": "skills/debug/SKILL.md", "section": "## Nope"})
        assert out == {"path": "skills/debug/SKILL.md", "section": None}

    def test_nonexistent_path_drops_escape_point(self, repo):
        assert validate(repo, {"path": "skills/ghost/SKILL.md", "section": "## Instrument"}) is None

    def test_match_ignores_leading_hash_marks(self, repo):
        for section in ("## Instrument", "Instrument", "# Instrument", "###   Instrument"):
            out = validate(repo, {"path": "skills/debug/SKILL.md", "section": section})
            assert out["section"] == section

    def test_match_is_exact_on_text(self, repo):
        for section in ("## instrument", "## Instrumen", "## Instrument Extra"):
            out = validate(repo, {"path": "skills/debug/SKILL.md", "section": section})
            assert out["section"] is None

    def test_nested_heading(self, repo):
        p = {"path": "skills/debug/SKILL.md", "section": "### Nested Step"}
        assert validate(repo, p) == p

    def test_heading_inside_code_fence_not_matched(self, repo):
        out = validate(repo, {"path": "skills/debug/SKILL.md", "section": "## Not A Heading"})
        assert out["section"] is None

    def test_null_section_stays_null(self, repo):
        p = {"path": "skills/debug/SKILL.md", "section": None}
        assert validate(repo, p) == p

    def test_path_traversal_dropped(self, repo):
        (repo.parent / "outside.md").write_text("# X\n")
        assert validate(repo, {"path": "../outside.md", "section": None}) is None

    def test_absolute_path_dropped(self, repo):
        assert validate(repo, {"path": str(repo / "skills/debug/SKILL.md"), "section": None}) is None

    def test_directory_path_dropped(self, repo):
        assert validate(repo, {"path": "skills/debug", "section": None}) is None

    def test_malformed_proposal_dropped(self, repo):
        assert validate(repo, {"section": "## x"}) is None
        assert validate(repo, "nope") is None

    def test_shared_prefix_fallback(self, tmp_path):
        root = tmp_path / "r"
        (root / "claude-code-shared" / "skills" / "debug").mkdir(parents=True)
        (root / "claude-code-shared" / "skills" / "debug" / "SKILL.md").write_text(SKILL_MD)
        p = {"path": "skills/debug/SKILL.md", "section": "## Fix"}
        assert validate(root, p) == p


def run_log(repo, out, entry):
    env = {**os.environ, "LOG_LEARNING_DEST": str(out), "LOG_LEARNING_DOTFILES_DIR": str(repo)}
    return subprocess.run(
        [sys.executable, str(LOG_LEARNING)],
        input=json.dumps(entry), capture_output=True, text=True, env=env,
    )


def written(out):
    lines = (out / "unified-learnings.jsonl").read_text().strip().split("\n")
    return [json.loads(line) for line in lines]


class TestLogLearningWiring:
    def test_valid_escape_point_written_unchanged(self, repo, tmp_path):
        out = tmp_path / "out"
        ep = {"path": "skills/debug/SKILL.md", "section": "## Instrument"}
        res = run_log(repo, out, {**MINIMAL_ATTRIBUTION, "escape_point": ep})
        assert res.returncode == 0, res.stderr
        assert written(out)[0]["escape_point"] == ep

    def test_missing_heading_nulls_section_and_still_appends(self, repo, tmp_path):
        out = tmp_path / "out"
        ep = {"path": "skills/debug/SKILL.md", "section": "## Nope"}
        res = run_log(repo, out, {**MINIMAL_ATTRIBUTION, "escape_point": ep})
        assert res.returncode == 0, res.stderr
        assert written(out)[0]["escape_point"] == {"path": "skills/debug/SKILL.md", "section": None}

    def test_nonexistent_path_drops_escape_point_and_still_appends(self, repo, tmp_path):
        out = tmp_path / "out"
        ep = {"path": "skills/ghost/SKILL.md", "section": "## Instrument"}
        res = run_log(repo, out, {**MINIMAL_ATTRIBUTION, "escape_point": ep})
        assert res.returncode == 0, res.stderr
        assert "escape_point" not in written(out)[0]

    def test_malformed_escape_point_dropped_not_rejected(self, repo, tmp_path):
        out = tmp_path / "out"
        res = run_log(repo, out, {**MINIMAL_ATTRIBUTION, "escape_point": "skills/debug/SKILL.md"})
        assert res.returncode == 0, res.stderr
        assert "escape_point" not in written(out)[0]

    def test_no_escape_point_unaffected(self, repo, tmp_path):
        out = tmp_path / "out"
        res = run_log(repo, out, MINIMAL_ATTRIBUTION)
        assert res.returncode == 0, res.stderr
        assert "escape_point" not in written(out)[0]


class TestDotfilesStampFallback:
    def test_non_git_dotfiles_dir_nulls_stamp_and_warns(self, repo, tmp_path):
        out = tmp_path / "out"
        res = run_log(repo, out, MINIMAL_ATTRIBUTION)
        assert res.returncode == 0, res.stderr
        row = written(out)[0]
        assert row["dotfiles_sha"] is None
        assert row["dotfiles_dirty"] is None
        assert "WARNING: could not read git state" in res.stderr
