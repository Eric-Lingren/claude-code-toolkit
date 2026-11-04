import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def _load(n):
    spec = importlib.util.spec_from_file_location(n, d / f"{n}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cc = _load("cross_cluster")
ls = _load("lane_select")
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
ROOT = "Runner artifacts are not verified."


def setup(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("LOG_LEARNING_DEST", str(tmp_path))
    rows, clusters = [], []
    for n, t in enumerate(targets):
        lid = f"L{n}"
        rows.append({"schema_version": "2", "id": lid, "timestamp": "2026-10-01T00:00:00Z", "type": "self",
                     "reported_by": "t", "improves": t, "improves_type": "skill",
                     "cause": "general_best_practice", "cause_label": None, "problem": "p",
                     "why_missed": "w", "lesson": "l", "fix": "f",
                     "evidence": [{"source": "transcript", "ref": "r", "quote": "q"}],
                     "confidence": "confirmed", "status": "captured"})
        clusters.append({"id": f"c{n}", "target": t, "learning_ids": [lid],
                         "root_cause": ROOT if n % 2 == 0 else ROOT.lower().rstrip(".") + "  ",
                         "fix": "f"})
    (tmp_path / "unified-learnings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows, clusters


def status(tmp_path):
    return {r["id"]: r["status"] for r in ls.load_rows(tmp_path / "unified-learnings.jsonl")}


def test_three_targets_one_issue_and_marks(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    payloads = []
    res = cc.promote(clusters, rows, lambda p: payloads.append(p) or 42, tmp_path)
    assert len(payloads) == 1
    assert payloads[0]["targets"] == ["a", "b", "c"]
    assert payloads[0]["learning_ids"] == ["L0", "L1", "L2"]
    assert "idea" in payloads[0]["labels"]
    assert res["errors"] == [] and res["marked"] == ["L0", "L1", "L2"]
    assert status(tmp_path) == {i: "clustered:42" for i in ("L0", "L1", "L2")}


def test_two_targets_no_issue_no_marks(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b"])
    calls = []
    res = cc.promote(clusters, rows, lambda p: calls.append(p) or 1, tmp_path)
    assert calls == [] and res["issues"] == [] and res["marked"] == []
    assert set(status(tmp_path).values()) == {"captured"}


def test_same_target_repeated_is_not_cross_target(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "a", "b"])
    calls = []
    cc.promote(clusters, rows, lambda p: calls.append(p) or 1, tmp_path)
    assert calls == []


def test_selector_excludes_clustered_after_marking(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    path = tmp_path / "unified-learnings.jsonl"
    # Each target has 1 row aged > MIN_AGE_DAYS, so eligible before marking.
    assert {t for t, _ in ls.select(ls.load_rows(path), NOW, lambda: [])} == {"a", "b", "c"}
    cc.promote(clusters, rows, lambda p: 9, tmp_path)
    assert ls.select(ls.load_rows(path), NOW, lambda: []) == []


def test_issue_failure_marks_nothing(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])

    def boom(p):
        raise RuntimeError("gh down")
    res = cc.promote(clusters, rows, boom, tmp_path)
    assert res["marked"] == [] and res["errors"]
    assert set(status(tmp_path).values()) == {"captured"}


def test_payload_carries_group_key(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    payloads = []
    cc.promote(clusters, rows, lambda p: payloads.append(p) or 5, tmp_path)
    assert f"Group-Key: {payloads[0]['group_key']}" in payloads[0]["body"]


def test_rerun_after_update_failure_reuses_issue(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    issues = {}

    def create(p):
        issues[p["group_key"]] = 77
        return 77

    def find(p):
        return issues.get(p["group_key"])
    real_run = cc.subprocess.run

    def flaky(cmd, *a, **kw):
        if "L1" in cmd or "L2" in cmd:
            return cc.subprocess.CompletedProcess(cmd, 1, "", "boom")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(cc.subprocess, "run", flaky)
    res = cc.promote(clusters, rows, create, tmp_path, find_issue=find)
    assert res["marked"] == ["L0"] and len(res["errors"]) == 2
    assert status(tmp_path)["L1"] == "captured"
    monkeypatch.setattr(cc.subprocess, "run", real_run)
    rows2 = ls.load_rows(tmp_path / "unified-learnings.jsonl")
    created = []
    res2 = cc.promote(clusters, rows2, lambda p: created.append(p) or 99, tmp_path, find_issue=find)
    assert created == [] and res2["errors"] == []
    assert res2["issues"][0]["number"] == 77
    assert status(tmp_path) == {i: "clustered:77" for i in ("L0", "L1", "L2")}


def test_find_issue_failure_creates_nothing(tmp_path, monkeypatch):
    rows, clusters = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    calls = []

    def find(p):
        raise RuntimeError("gh down")
    res = cc.promote(clusters, rows, lambda p: calls.append(p) or 1, tmp_path, find_issue=find)
    assert calls == [] and res["errors"] and res["marked"] == []
