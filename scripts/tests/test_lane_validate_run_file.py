"""Isolated negative tests: each validate_run_file rule fails on its own."""
import importlib.util
import json
from pathlib import Path

import pytest

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def _load(n):
    spec = importlib.util.spec_from_file_location(n, d / f"{n}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


vrf = _load("validate_run_file")
va = _load("verdict_apply")


def good():
    return {
        "version": "1.0", "target": "t",
        "clusters": [
            {"id": "c1", "status": "committed", "learning_ids": ["L1"], "commit": "abc", "applied_ids": ["L1"]},
            {"id": "c2", "status": "needs-grill", "learning_ids": ["L2"], "reasons": ["contradiction"]},
            {"id": "c3", "status": "no-change", "learning_ids": ["L3"]},
        ],
        "verdicts": [
            {"learning_id": "L3", "cluster_id": "c3", "verdict": "retarget", "reason": "r",
             "suggested_improves": "other", "suggested_improves_type": "agent"},
        ],
    }


def test_baseline_good_is_valid():
    assert vrf.errors(good()) == []


CASES = {
    "version": (lambda x: x.update(version="2.0"), "version must be"),
    "target": (lambda x: x.update(target=" "), "target missing"),
    "committed-no-commit": (lambda x: x["clusters"][0].pop("commit"), "commit required when committed"),
    "committed-empty-commit": (lambda x: x["clusters"][0].update(commit=""), "commit required when committed"),
    "committed-no-applied": (lambda x: x["clusters"][0].pop("applied_ids"), "applied_ids required"),
    "committed-empty-applied": (lambda x: x["clusters"][0].update(applied_ids=[]), "applied_ids required"),
    "commit-on-noncommitted": (lambda x: x["clusters"][2].update(commit="abc"), "commit only allowed"),
    "needs-grill-no-reasons": (lambda x: x["clusters"][1].pop("reasons"), "reasons required when needs-grill"),
    "needs-grill-empty-reasons": (lambda x: x["clusters"][1].update(reasons=[]), "reasons required when needs-grill"),
    "cluster-status": (lambda x: x["clusters"][2].update(status="bogus"), "status must be one of"),
    "cluster-learning-ids": (lambda x: x["clusters"][2].update(learning_ids=[]), "learning_ids missing"),
    "verdict-enum": (lambda x: x["verdicts"][0].update(verdict="maybe"), "verdict must be one of"),
    "verdict-unknown-cluster": (lambda x: x["verdicts"][0].update(cluster_id="zz"), "cluster_id not in clusters"),
    "verdict-no-reason": (lambda x: x["verdicts"][0].pop("reason"), "reason missing"),
    "retarget-no-improves": (lambda x: x["verdicts"][0].pop("suggested_improves"), "required for retarget"),
    "retarget-bad-slug": (lambda x: x["verdicts"][0].update(suggested_improves="Bad/Slug"), "must match"),
    "retarget-dotdot": (lambda x: x["verdicts"][0].update(suggested_improves="a..b"), "must match"),
    "retarget-bad-type": (lambda x: x["verdicts"][0].update(suggested_improves_type="nope"), "suggested_improves_type"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_rule_fails_alone(name):
    fn, needle = CASES[name]
    doc = good()
    fn(doc)
    errs = vrf.errors(doc)
    assert any(needle in e for e in errs), errs


def test_verdict_apply_invalid_run_file_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEARNING_DEST", str(tmp_path))
    row = {"schema_version": "2", "id": "L1", "timestamp": "2026-10-01T00:00:00Z", "type": "self",
           "reported_by": "t", "improves": "skill-x", "improves_type": "skill",
           "cause": "general_best_practice", "cause_label": None, "problem": "p", "why_missed": "w",
           "lesson": "l", "fix": "f", "evidence": [{"source": "transcript", "ref": "r", "quote": "q"}],
           "confidence": "confirmed", "status": "captured"}
    f = tmp_path / "unified-learnings.jsonl"
    f.write_text(json.dumps(row) + "\n")
    before = f.read_bytes()
    doc = {"version": "1.0", "target": "skill-x",
           "clusters": [{"id": "c1", "status": "no-change", "learning_ids": ["L1"]}],
           "verdicts": [{"learning_id": "L1", "cluster_id": "c1", "verdict": "stale", "reason": "x"},
                        {"learning_id": "L1", "cluster_id": "missing", "verdict": "invalid", "reason": "x"}]}
    rf = tmp_path / "run.json"
    rf.write_text(json.dumps(doc))
    res = va.apply_verdicts(rf, tmp_path)
    assert res["errors"] and all(e.startswith("run file invalid") for e in res["errors"])
    assert f.read_bytes() == before
