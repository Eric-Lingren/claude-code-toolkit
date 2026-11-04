import importlib.util
import json
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def _load(n):
    spec = importlib.util.spec_from_file_location(n, d / f"{n}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


va = _load("verdict_apply")
ls = _load("lane_select")


def make(tmp_path, monkeypatch, ids=("L1", "L2", "L3", "L4")):
    monkeypatch.setenv("LOG_LEARNING_DEST", str(tmp_path))
    rows = [{"schema_version": "2", "id": i, "timestamp": "2026-10-01T00:00:00Z", "type": "self",
             "reported_by": "t", "improves": "skill-x", "improves_type": "skill",
             "cause": "general_best_practice", "cause_label": None, "problem": "p",
             "why_missed": "w", "lesson": "l", "fix": "f",
             "evidence": [{"source": "transcript", "ref": "r", "quote": "q"}],
             "confidence": "confirmed", "status": "captured"} for i in ids]
    (tmp_path / "unified-learnings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def run_file(tmp_path, extra=()):
    vs = [
        {"learning_id": "NOPE", "cluster_id": "c1", "verdict": "stale", "reason": "x"} if extra else
        {"learning_id": "L4", "cluster_id": "c1", "verdict": "applied", "reason": "ok"},
        {"learning_id": "L1", "cluster_id": "c1", "verdict": "stale", "reason": "already_applied"},
        {"learning_id": "L2", "cluster_id": "c1", "verdict": "invalid", "reason": "wrong claim"},
        {"learning_id": "L3", "cluster_id": "c1", "verdict": "retarget", "reason": "type_mismatch",
         "suggested_improves": "other-agent", "suggested_improves_type": "agent"},
    ]
    doc = {"version": "1.0", "target": "skill-x", "verdicts": vs,
           "clusters": [{"id": "c1", "status": "no-change", "learning_ids": [v["learning_id"] for v in vs]}]}
    p = tmp_path / "run.json"
    p.write_text(json.dumps(doc))
    return p


def rows(tmp_path):
    return {r["id"]: r for r in ls.load_rows(tmp_path / "unified-learnings.jsonl")}


def test_verdicts_update_rows(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    res = va.apply_verdicts(run_file(tmp_path), tmp_path)
    r = rows(tmp_path)
    assert not res["errors"], res
    assert r["L1"]["status"] == "stale" and r["L1"]["reason"] == "already_applied"
    assert r["L2"]["status"] == "invalid" and r["L2"]["reason"] == "wrong claim"
    assert (r["L3"]["improves"], r["L3"]["improves_type"], r["L3"]["status"]) == ("other-agent", "agent", "captured")
    assert r["L4"]["status"] == "captured"
    assert res["dropped"] == [{"id": "L1", "verdict": "stale", "reason": "already_applied"},
                              {"id": "L2", "verdict": "invalid", "reason": "wrong claim"}]
    assert [x["id"] for x in res["retargeted"]] == ["L3"]


def test_second_run_changes_nothing(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    rf = run_file(tmp_path)
    va.apply_verdicts(rf, tmp_path)
    f = tmp_path / "unified-learnings.jsonl"
    before = f.read_text()
    res = va.apply_verdicts(rf, tmp_path)
    assert f.read_text() == before
    assert res["dropped"] == [] and res["retargeted"] == []
    assert sorted(res["skipped"]) == ["L1", "L2", "L3"]


def test_unknown_id_reported_pass_continues(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    rf = run_file(tmp_path, extra=True)
    res = va.apply_verdicts(rf, tmp_path)
    assert res["unknown"] == ["NOPE"]
    assert not res["errors"]
    assert rows(tmp_path)["L1"]["status"] == "stale"
    assert va.main([str(rf), "--learnings-dir", str(tmp_path)]) == 0


def test_retarget_bad_slug_rejected():
    vrf = _load("validate_run_file")
    for bad in ("../evil", "a/b", "-x", "A", "a..b", "x y"):
        doc = {"version": "1.0", "target": "t", "clusters": [{"id": "c1", "status": "no-change"}],
               "verdicts": [{"learning_id": "L1", "cluster_id": "c1", "verdict": "retarget", "reason": "r",
                             "suggested_improves": bad, "suggested_improves_type": "agent"}]}
        assert any("suggested_improves must match" in e for e in vrf.errors(doc)), bad
