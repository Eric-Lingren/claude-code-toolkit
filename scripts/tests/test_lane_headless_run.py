"""End-to-end headless run with a stub claude binary (no live model)."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def load(name):
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hr = load("headless_run")
vrf = load("validate_run_file")
wb = load("lane_writeback")

STUB = """#!/usr/bin/env python3
import json, os, re, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
if os.environ.get("STUB_ARGV_LOG"):
    open(os.environ["STUB_ARGV_LOG"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
fx = json.load(open(os.environ["STUB_FIXTURE"]))
if prompt.startswith("ROLE: clusterer"):
    sys.stdin.read()
    print(json.dumps(fx["clusters"]))
elif "You are a learning validation agent" in prompt:
    lid = re.search(r"^  id: (\\S+)", prompt, re.M).group(1)
    print(json.dumps(fx["validator"][lid]))
elif "You are drafting a unified diff" in prompt:
    lid = re.search(r"^  id: (\\S+)", prompt, re.M).group(1)
    print(fx["drafter"][lid])
else:
    sys.exit("unknown prompt")
"""


def diff(path, anchor_before, added):
    return f"--- {path}\n+++ {path}\n@@ -1,2 +1,3 @@\n {anchor_before[0]}\n {anchor_before[1]}\n+{added}\n"


def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "contracts").mkdir(parents=True)
    a, b = repo / "a.md", repo / "b.md"
    a.write_text("# A\nline one\nline two\n")
    b.write_text("# B\nbee one\nbee two\n")
    (repo / "contracts" / "c.md").write_text("# C\n`widget` is a string\n")
    (repo / "contracts" / "c.json").write_text('{"widget": {"type": "string"}}\n')
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", *c], cwd=repo, check=True)

    rows = [{"id": i, "improves": "demo", "improves_type": "skill", "problem": f"p{i}",
             "lesson": f"l{i}", "fix": f"fix {i}", "status": "captured",
             "confidence": "confirmed", "timestamp": "2026-01-01T00:00:00Z"} for i in
            ("L1", "L2", "L3", "L4", "L5", "L6", "L7")]
    jl = tmp_path / "unified-learnings.jsonl"
    jl.write_text("".join(json.dumps(r) + "\n" for r in rows))
    cdiff = "--- c.md\n+++ c.md\n@@ -1,2 +1,2 @@\n # C\n-`widget` is a string\n+`widget` is a number\n"
    fx = {
        "clusters": {"clusters": [
            {"id": "c1", "target": "demo", "learning_ids": ["L1", "L2", "L3", "L4"],
             "root_cause": "Alpha gap", "fix": "add alpha"},
            {"id": "c2", "target": "demo", "learning_ids": ["L5", "L6"],
             "root_cause": "Bee gap", "fix": "add bee"},
            {"id": "c3", "target": "demo", "learning_ids": ["L7"],
             "root_cause": "Contract gap", "fix": "change widget"}]},
        "validator": {
            "L1": {"id": "L1", "verdict": "valid", "reason": "ok", "file_path": str(a)},
            "L2": {"id": "L2", "verdict": "valid", "reason": "ok", "file_path": str(a)},
            "L3": {"id": "L3", "verdict": "stale", "reason": "already_applied", "file_path": str(a)},
            "L4": {"id": "L4", "verdict": "invalid", "reason": "contradicts_shared_resource", "file_path": str(a)},
            "L5": {"id": "L5", "verdict": "misrouted", "reason": "type_mismatch", "file_path": str(b),
                   "suggested_improves": "other", "suggested_improves_type": "agent"},
            "L6": {"id": "L6", "verdict": "valid", "reason": "ok", "file_path": str(b)},
            "L7": {"id": "L7", "verdict": "valid", "reason": "ok", "file_path": str(repo / "contracts" / "c.md")},
        },
        "drafter": {
            "L1": diff("a.md", ("line one", "line two"), "added L1").replace("# A", "# A"),
            "L2": "```diff\n--- a.md\n+++ a.md\n@@ -1,2 +1,3 @@\n line two\n+added L2\n```",
            "L6": diff("b.md", ("bee one", "bee two"), "added L6"),
            "L7": cdiff,
        },
    }
    fxp = tmp_path / "fixture.json"
    fxp.write_text(json.dumps(fx))
    stub = tmp_path / "claude"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLAUDE_BIN", str(stub))
    monkeypatch.setenv("STUB_FIXTURE", str(fxp))
    monkeypatch.setattr(hr, "type_paths", lambda slug, t: [str(a), str(b), str(repo / "contracts" / "c.md")])
    return repo, jl


def head_count(repo):
    return int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True,
                              text=True, check=True).stdout)


def go(tmp_path, repo, jl):
    run_file = tmp_path / "out" / "run.json"
    doc = hr.run("demo", jl, repo, run_file, tmp_path / "out" / "ev.jsonl")
    return run_file, doc


def test_one_commit_per_cluster_with_learning_ids_trailer(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    before = head_count(repo)
    go(tmp_path, repo, jl)
    assert head_count(repo) == before + 2
    msgs = subprocess.run(["git", "log", "--format=%B%x00", "-2"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.split("\x00")
    ids = [wb.trailer_ids([m]) for m in msgs if m.strip()]
    assert ids == [["L6"], ["L1", "L2"]]
    assert all("Learning-Ids: " in m for m in msgs if m.strip())
    assert "added L1" in (repo / "a.md").read_text() and "added L2" in (repo / "a.md").read_text()


def test_jsonl_byte_identical_and_stale_invalid_in_run_file(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    raw = jl.read_bytes()
    run_file, doc = go(tmp_path, repo, jl)
    assert jl.read_bytes() == raw
    by = {v["learning_id"]: v for v in json.loads(run_file.read_text())["verdicts"]}
    assert by["L3"]["verdict"] == "stale" and by["L3"]["reason"] == "already_applied"
    assert by["L4"]["verdict"] == "invalid"
    assert by["L5"]["verdict"] == "retarget" and by["L5"]["suggested_improves"] == "other"
    assert by["L1"]["verdict"] == "applied"
    assert vrf.errors(json.loads(run_file.read_text())) == []


def test_contract_drift_needs_grill_no_commit(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    before_text = (repo / "contracts" / "c.md").read_text()
    _, doc = go(tmp_path, repo, jl)
    c3 = next(c for c in doc["clusters"] if c["id"] == "c3")
    assert c3["status"] == "needs-grill" and "commit" not in c3 and c3["reasons"] == ["contract-drift"]
    assert (repo / "contracts" / "c.md").read_text() == before_text
    log = subprocess.run(["git", "log", "--format=%B"], cwd=repo, capture_output=True, text=True).stdout
    assert "Contract gap" not in log


def test_flagged_cluster_is_needs_grill_without_model_calls_for_it(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    fx = json.loads(Path(os.environ["STUB_FIXTURE"]).read_text())
    fx["clusters"]["clusters"][1]["contradiction"] = True
    Path(os.environ["STUB_FIXTURE"]).write_text(json.dumps(fx))
    _, doc = go(tmp_path, repo, jl)
    c2 = next(c for c in doc["clusters"] if c["id"] == "c2")
    assert c2["status"] == "needs-grill" and c2["reasons"] == ["contradiction"]
    assert "added L6" not in (repo / "b.md").read_text()


def test_never_calls_update_learning():
    code = [l for l in (d / "headless_run.py").read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not any("update-learning" in l or "update_learning" in l for l in code)


def test_prompts_come_from_skill_md():
    assert "You are a learning validation agent" in hr.prompt_from_skill("Pass this prompt to the Haiku agent")
    assert "You are drafting a unified diff" in hr.prompt_from_skill("Read the target file (resolved")


def test_sample_validates_and_bad_run_file_rejected():
    assert vrf.main([sys.argv[0], "--sample"]) == 0
    assert vrf.errors({"version": "1.0", "target": "t", "clusters": [], "verdicts": [
        {"learning_id": "a", "cluster_id": "zz", "verdict": "retarget", "reason": "r"}]})


def test_call_claude_raises_on_nonzero_exit(tmp_path, monkeypatch):
    bad = tmp_path / "claude"
    bad.write_text("#!/bin/sh\necho partial\necho 'auth failed' >&2\nexit 3\n")
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLAUDE_BIN", str(bad))
    with pytest.raises(hr.ClaudeCallError, match="exited 3"):
        hr.call_claude("hi")


def test_call_claude_raises_on_timeout(tmp_path, monkeypatch):
    slow = tmp_path / "claude"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLAUDE_BIN", str(slow))
    monkeypatch.setenv("CLAUDE_CALL_TIMEOUT_S", "0.2")
    with pytest.raises(hr.ClaudeCallError, match="timed out"):
        hr.call_claude("hi")


def test_shell_agent_raises_on_nonzero_exit_and_timeout(monkeypatch):
    ct = hr.cluster_target
    with pytest.raises(ct.ClaudeCallError, match="exited 2"):
        ct.shell_agent("echo '{}'; exit 2")({})
    monkeypatch.setenv("CLAUDE_CALL_TIMEOUT_S", "0.2")
    with pytest.raises(ct.ClaudeCallError, match="timed out"):
        ct.shell_agent("sleep 5")({})


def test_malformed_validator_output_retries_then_raises_never_stale(monkeypatch):
    calls = []
    monkeypatch.setattr(hr, "call_claude", lambda *a, **k: calls.append(1) or "not json")
    with pytest.raises(hr.ClaudeCallError, match="malformed validator output"):
        hr.validate({"id": "L1", "improves": "demo", "improves_type": "skill"})
    assert len(calls) == hr.VALIDATE_ATTEMPTS


def test_malformed_validator_output_recovers_on_retry(monkeypatch):
    outs = iter(["garbage", json.dumps({"id": "L1", "verdict": "valid", "reason": "ok", "file_path": "x"})])
    monkeypatch.setattr(hr, "call_claude", lambda *a, **k: next(outs))
    assert hr.validate({"id": "L1", "improves": "demo", "improves_type": "skill"})["verdict"] == "valid"


def test_failed_validate_call_aborts_run_without_run_file(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    stub = Path(os.environ["CLAUDE_BIN"])
    stub.write_text(STUB.replace('    print(json.dumps(fx["validator"][lid]))', '    sys.exit(1)'))
    run_file = tmp_path / "out" / "run.json"
    with pytest.raises(hr.ClaudeCallError):
        hr.run("demo", jl, repo, run_file, tmp_path / "out" / "ev.jsonl")
    assert not run_file.exists()


def test_pmap_preserves_order_and_propagates_errors():
    assert hr.pmap(lambda x: x * 2, [1, 2, 3, 4, 5]) == [2, 4, 6, 8, 10]

    def boom(x):
        if x == 3:
            raise hr.ClaudeCallError("x")
        return x
    with pytest.raises(hr.ClaudeCallError):
        hr.pmap(boom, [1, 2, 3, 4])


def test_partial_run_file_written_per_cluster_and_removed_on_success(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    run_file = tmp_path / "out" / "run.json"
    seen = []
    orig = hr.write_json
    monkeypatch.setattr(hr, "write_json", lambda p, d: (seen.append(str(p)), orig(p, d)))
    hr.run("demo", jl, repo, run_file, tmp_path / "out" / "ev.jsonl")
    assert any(p.endswith(".partial") for p in seen)
    assert run_file.exists() and not Path(str(run_file) + ".partial").exists()


def test_load_rows_only_confirmed_captured(tmp_path):
    base = {"improves": "t", "timestamp": "2026-01-01T00:00:00Z"}
    rows = [
        dict(base, id="a", confidence="confirmed", status="captured"),
        dict(base, id="b", confidence="candidate", status="captured"),
        dict(base, id="c", confidence="confirmed", status="applied"),
    ]
    p = tmp_path / "l.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    assert [r["id"] for r in hr.load_rows(p, "t")] == ["a"]


def test_file_path_outside_worktree_or_candidates_is_draft_failed(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("# O\nx\ny\n")
    fx = json.loads(Path(os.environ["STUB_FIXTURE"]).read_text())
    for lid, bad in (("L1", str(outside)), ("L2", "~/evil.md"), ("L6", str(repo / "contracts" / "c.json"))):
        fx["validator"][lid]["file_path"] = bad
    Path(os.environ["STUB_FIXTURE"]).write_text(json.dumps(fx))
    _, doc = go(tmp_path, repo, jl)
    bad = {x["learning_id"]: x for x in doc["verdicts"] if x["verdict"] == "draft-failed"}
    assert {"L1", "L2", "L6"} <= set(bad)
    assert outside.read_text() == "# O\nx\ny\n"
    assert not (Path.home() / "evil.md").exists()


def test_every_claude_call_is_read_only(tmp_path, monkeypatch):
    repo, jl = setup(tmp_path, monkeypatch) if "setup" in globals() else (None, None)


def test_apply_diff_raw_entities_preserved():
    out = hr.apply_diff("a &amp; b\nx\n", "@@\n a &amp; b\n-x\n+y &lt; z\n")
    assert out == "a &amp; b\ny &lt; z\n"


def test_apply_diff_unescape_fallback():
    out = hr.apply_diff("a < b\nx\n", "@@\n a &lt; b\n-x\n+y\n")
    assert out == "a < b\ny\n"
