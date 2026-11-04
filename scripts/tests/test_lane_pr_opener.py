import importlib.util
import json
from pathlib import Path

import pytest

p = Path(__file__).resolve().parent.parent / "learnings-lane" / "pr_opener.py"
spec = importlib.util.spec_from_file_location("pr_opener", p)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)

RUN = {"target": "demo", "clusters": [
    {"id": "c1", "status": "committed", "learning_ids": ["L1", "L2"], "commit": "abc"}],
    "verdicts": [{"learning_id": "L2", "cluster_id": "c1", "verdict": "stale", "reason": "already_applied"},
                 {"learning_id": "L3", "cluster_id": "c1", "verdict": "invalid", "reason": "no anchor"},
                 {"learning_id": "L1", "cluster_id": "c1", "verdict": "applied", "reason": "ok"}]}
DET = {"c1": {"problem": "PROB-X", "anchor_quotes": ["quote-one", "quote-two"],
              "fix_rationale": "WHY-Y", "diff_summary": "DIFF-Z"}}


def fake_gh(open_n=0, rebase=True):
    calls = []

    def gh(args):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": i} for i in range(open_n)])
        if args[:2] == ["repo", "view"]:
            return json.dumps({"rebaseMergeAllowed": rebase})
        return "https://example/pr/1\n"
    gh.calls = calls
    return gh


def create(gh):
    return [c for c in gh.calls if c[:2] == ["pr", "create"]][0]


def test_body_has_cluster_sections_and_dropped():
    b = o.build_body("demo", RUN, DET, {"pr_body": ""})
    for s in ("PROB-X", "quote-one", "quote-two", "WHY-Y", "DIFF-Z", "L2", "stale", "L3", "invalid"):
        assert s in b
    assert "rebase merge" in b
    assert "`L1`" not in b.split("Dropped learnings")[1]


def test_ready_normal_pr():
    gh = fake_gh()
    o.open_pr("demo", RUN, DET, {"verdict": "ready"}, gh=gh)
    c = create(gh)
    assert "--draft" not in c and "blocked" not in c and "needs-grill" not in c
    assert "learnings-lane" in c and "learnings-lane/demo" in c


def test_blocked_draft_label_and_log():
    gh = fake_gh()
    o.open_pr("demo", RUN, DET, {"verdict": "blocked", "pr_body": "LOG-HERE"}, gh=gh)
    c = create(gh)
    assert "--draft" in c and "blocked" in c
    assert "LOG-HERE" in c[c.index("--body") + 1]


def test_needs_grill_draft_label():
    gh = fake_gh()
    o.open_pr("demo", RUN, DET, {"verdict": "needs-grill"}, gh=gh)
    c = create(gh)
    assert "--draft" in c and "needs-grill" in c and "blocked" not in c


def test_cap_refuses():
    gh = fake_gh(open_n=5)
    with pytest.raises(o.PrRefused):
        o.open_pr("demo", RUN, DET, {"verdict": "ready"}, gh=gh)
    assert not [c for c in gh.calls if c[:2] == ["pr", "create"]]


def test_rebase_disallowed_refuses():
    gh = fake_gh(rebase=False)
    with pytest.raises(o.PrRefused):
        o.open_pr("demo", RUN, DET, {"verdict": "ready"}, gh=gh)
    assert not [c for c in gh.calls if c[:2] == ["pr", "create"]]
