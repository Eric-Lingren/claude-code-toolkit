"""headless_run.py branch coverage: draft-failed, no-change, apply_diff None, parse_json fallback, malformed validator."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def load(name):
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hr = load("headless_run")


# parse_json
def test_parse_json_clean():
    assert hr.parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_extracts_embedded_object():
    assert hr.parse_json('Sure! here:\n{"id": "L1", "verdict": "valid"}\nbye') == {"id": "L1", "verdict": "valid"}


def test_parse_json_non_json_returns_empty():
    assert hr.parse_json("no json here") == {}


def test_parse_json_braces_but_invalid_returns_empty():
    assert hr.parse_json("{not: json}") == {}


# apply_diff
def test_apply_diff_applies():
    assert hr.apply_diff("a\nb\n", "@@ -1 +1,2 @@\n a\n+x\n") == "a\nx\nb\n"


def test_apply_diff_unanchored_returns_none():
    assert hr.apply_diff("a\nb\n", "@@ -1 +1,2 @@\n zzz\n+x\n") is None


def test_apply_diff_no_hunks_is_identity():
    assert hr.apply_diff("a\n", "garbage") == "a\n"


def test_apply_diff_unescape_fallback():
    assert hr.apply_diff("a < b\nc\n", "@@ -1 +1,2 @@\n a &lt; b\n+x\n") == "a < b\nx\nc\n"


# validate: malformed output retried then raises (malformed_verdict fallback no longer exists)
ROW = {"id": "L1", "improves": "demo", "improves_type": "skill", "problem": "p", "lesson": "l", "fix": "f"}


def test_validate_retries_then_succeeds(monkeypatch):
    outs = iter(["not json", '{"id": "L1", "verdict": "valid"}'])
    monkeypatch.setattr(hr, "call_claude", lambda *a, **k: next(outs))
    assert hr.validate(ROW)["verdict"] == "valid"


def test_validate_persistent_malformed_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(hr, "call_claude", lambda *a, **k: calls.append(1) or "nope")
    with pytest.raises(hr.ClaudeCallError):
        hr.validate(ROW)
    assert len(calls) == hr.VALIDATE_ATTEMPTS


def test_validate_wrong_id_counts_as_malformed(monkeypatch):
    monkeypatch.setattr(hr, "call_claude", lambda *a, **k: '{"id": "OTHER", "verdict": "valid"}')
    with pytest.raises(hr.ClaudeCallError):
        hr.validate(ROW)


# run_cluster: draft-failed / no-change
def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    a = repo / "a.md"
    a.write_text("# A\nline one\n")
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", *c], cwd=repo, check=True)
    return repo, a


def cluster(ids):
    return {"id": "c1", "target": "demo", "learning_ids": ids, "root_cause": "rc", "fix": "fx"}


def test_unanchored_diff_yields_draft_failed_and_no_change(tmp_path, monkeypatch):
    repo, a = make_repo(tmp_path)
    monkeypatch.setattr(hr, "type_paths", lambda s, t: [str(a)])
    monkeypatch.setattr(hr, "validate", lambda row: {"id": row["id"], "verdict": "valid", "file_path": str(a)})
    monkeypatch.setattr(hr, "draft", lambda row, res: "@@ -1 +1,2 @@\n nonexistent\n+x\n")
    entry, vs = hr.run_cluster(cluster(["L1"]), {"L1": ROW}, repo)
    assert entry["status"] == "no-change"
    assert [(v["verdict"], v["reason"]) for v in vs] == [("draft-failed", "diff_did_not_apply")]
    assert a.read_text() == "# A\nline one\n"


def test_noop_diff_yields_draft_failed(tmp_path, monkeypatch):
    repo, a = make_repo(tmp_path)
    monkeypatch.setattr(hr, "type_paths", lambda s, t: [str(a)])
    monkeypatch.setattr(hr, "validate", lambda row: {"id": row["id"], "verdict": "valid", "file_path": str(a)})
    monkeypatch.setattr(hr, "draft", lambda row, res: "@@ -1 +1 @@\n # A\n")
    entry, vs = hr.run_cluster(cluster(["L1"]), {"L1": ROW}, repo)
    assert entry["status"] == "no-change"
    assert vs[0]["verdict"] == "draft-failed"


def test_path_outside_candidates_yields_draft_failed(tmp_path, monkeypatch):
    repo, a = make_repo(tmp_path)
    other = tmp_path / "other.md"
    other.write_text("x\n")
    monkeypatch.setattr(hr, "type_paths", lambda s, t: [str(a)])
    monkeypatch.setattr(hr, "validate", lambda row: {"id": row["id"], "verdict": "valid", "file_path": str(other)})
    entry, vs = hr.run_cluster(cluster(["L1"]), {"L1": ROW}, repo)
    assert entry["status"] == "no-change"
    assert vs[0]["verdict"] == "draft-failed"
    assert vs[0]["reason"] == "file_path_outside_worktree_or_candidates"


def test_mixed_anchored_and_unanchored_commits_only_anchored(tmp_path, monkeypatch):
    repo, a = make_repo(tmp_path)
    r2 = dict(ROW, id="L2")
    monkeypatch.setattr(hr, "type_paths", lambda s, t: [str(a)])
    monkeypatch.setattr(hr, "validate", lambda row: {"id": row["id"], "verdict": "valid", "file_path": str(a)})
    diffs = {"L1": "@@ -1 +1,2 @@\n line one\n+added\n", "L2": "@@ -1 +1,2 @@\n zzz\n+y\n"}
    monkeypatch.setattr(hr, "draft", lambda row, res: diffs[row["id"]])
    entry, vs = hr.run_cluster(cluster(["L1", "L2"]), {"L1": ROW, "L2": r2}, repo)
    assert entry["status"] == "committed" and entry["applied_ids"] == ["L1"]
    assert {v["learning_id"]: v["verdict"] for v in vs} == {"L1": "applied", "L2": "draft-failed"}
