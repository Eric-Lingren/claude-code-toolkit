"""Tests for the --consistency (SHAPE findings) scanner mode.

Covers claude_tooling_extractor.consistency_findings() directly and the
scanner.py --consistency CLI end-to-end. SHAPE findings are the filesystem-shape
companion to the edge-graph litmus set: organizational inconsistencies the
graph-based core cannot see.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "dependency-graph"))

import claude_tooling_extractor as extractor  # noqa: E402

SCANNER = SCRIPTS / "dependency-graph" / "scanner.py"


def build_repo(root, agents=(), scripts=()):
    """Materialize a minimal claude-code-shared-shaped tree.

    agents:  iterable of (name, relative_file_path). The file is created and
             the agent is registered in agents/registry.json.
    scripts: iterable of relative paths (under the repo root) to touch as
             script files.
    """
    (root / "agents").mkdir(parents=True, exist_ok=True)
    registry = {"agents": []}
    for name, rel in agents:
        fpath = root / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f"# {name}\n")
        registry["agents"].append({"name": name, "file": rel, "consumers": []})
    (root / "agents" / "registry.json").write_text(json.dumps(registry, indent=2))
    for rel in scripts:
        fpath = root / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text("#!/usr/bin/env python3\n")
    return root


def principles(findings):
    return [f["principle"] for f in findings]


def titles(findings):
    return " || ".join(f["title"] for f in findings)


class TestAgentWrapping:
    def test_mixed_wrapping_group_is_flagged(self, tmp_path):
        build_repo(tmp_path, agents=[
            ("export-tasks", "agents/egress/export-tasks.md"),
            ("export-tasks-github", "agents/egress/export-tasks-github/agent.md"),
            ("export-tasks-notion", "agents/egress/export-tasks-notion/agent.md"),
        ])
        findings = extractor.consistency_findings(str(tmp_path))
        assert principles(findings) == ["SHAPE"]
        f = findings[0]
        assert "agents/egress" in f["title"]
        assert set(f["nodes"]) == {
            "agent:export-tasks", "agent:export-tasks-github", "agent:export-tasks-notion",
        }
        # Evidence names each member and its shape.
        facts = " ".join(e["fact"] for e in f["evidence"])
        assert "bare" in facts and "wrapped" in facts

    def test_uniform_bare_group_is_not_flagged(self, tmp_path):
        build_repo(tmp_path, agents=[
            ("persona-accuracy", "agents/seed-review/persona-accuracy.md"),
            ("persona-judge", "agents/seed-review/persona-judge.md"),
        ])
        assert extractor.consistency_findings(str(tmp_path)) == []

    def test_uniform_wrapped_group_is_not_flagged(self, tmp_path):
        build_repo(tmp_path, agents=[
            ("alpha", "agents/adapters/alpha/agent.md"),
            ("beta", "agents/adapters/beta/agent.md"),
        ])
        assert extractor.consistency_findings(str(tmp_path)) == []

    def test_top_level_bucket_with_group_subdir_is_not_flagged(self, tmp_path):
        # Bare top-level agents plus a grouped subdirectory (personas) is the
        # normal shape -- the subdir contributes its own group, not agents at
        # the agents/ level, so agents/ stays uniformly bare.
        build_repo(tmp_path, agents=[
            ("context-loader", "agents/context-loader.md"),
            ("build-runner", "agents/build/build-runner.md"),
            ("persona-accuracy", "agents/seed-review/persona-accuracy.md"),
            ("persona-judge", "agents/seed-review/persona-judge.md"),
        ])
        assert extractor.consistency_findings(str(tmp_path)) == []


class TestStrayFixtures:
    def test_example_fixture_in_scripts_root_is_flagged(self, tmp_path):
        build_repo(tmp_path, scripts=[
            "scripts/scanner.py",
            "scripts/graph.example.json",
        ])
        findings = extractor.consistency_findings(str(tmp_path))
        assert principles(findings) == ["SHAPE"]
        assert findings[0]["nodes"] == ["script:scripts/graph.example.json"]

    def test_example_fixture_inside_tests_is_not_flagged(self, tmp_path):
        build_repo(tmp_path, scripts=[
            "scripts/scanner.py",
            "scripts/tests/graph.example.json",
        ])
        assert extractor.consistency_findings(str(tmp_path)) == []

    def test_plain_executables_are_not_flagged(self, tmp_path):
        build_repo(tmp_path, scripts=[
            "scripts/scanner.py",
            "scripts/helper.sh",
        ])
        assert extractor.consistency_findings(str(tmp_path)) == []


class TestCleanTree:
    def test_no_findings_on_clean_tree(self, tmp_path):
        build_repo(
            tmp_path,
            agents=[("context-loader", "agents/context-loader.md")],
            scripts=["scripts/scanner.py"],
        )
        assert extractor.consistency_findings(str(tmp_path)) == []


class TestScannerCLI:
    def _run(self, root, *extra):
        return subprocess.run(
            [sys.executable, str(SCANNER), "--consistency", str(root), *extra],
            capture_output=True, text=True,
        )

    def test_cli_emits_findings_envelope_and_exits_zero(self, tmp_path):
        build_repo(tmp_path, agents=[
            ("export-tasks", "agents/egress/export-tasks.md"),
            ("export-tasks-github", "agents/egress/export-tasks-github/agent.md"),
        ], scripts=["scripts/graph.example.json"])
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        env = json.loads(result.stdout)
        assert env["schema_version"] == "1"
        assert env["summary"]["findings"] == len(env["findings"])
        assert env["summary"]["findings"] == 2
        # F-ids are assigned in order and every finding is SHAPE.
        assert [f["id"] for f in env["findings"]] == ["F0001", "F0002"]
        assert set(principles(env["findings"])) == {"SHAPE"}

    def test_cli_clean_tree_yields_empty_findings(self, tmp_path):
        build_repo(
            tmp_path,
            agents=[("context-loader", "agents/context-loader.md")],
            scripts=["scripts/scanner.py"],
        )
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        env = json.loads(result.stdout)
        assert env["findings"] == []
        assert env["summary"]["findings"] == 0

    def test_cli_out_flag_writes_file(self, tmp_path):
        build_repo(tmp_path, agents=[("context-loader", "agents/context-loader.md")])
        out = tmp_path / "findings.json"
        result = self._run(tmp_path, "--out", str(out))
        assert result.returncode == 0, result.stderr
        assert out.exists()
        env = json.loads(out.read_text())
        assert "findings" in env


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
