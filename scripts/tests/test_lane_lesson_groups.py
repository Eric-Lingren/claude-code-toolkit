import copy
import importlib.util
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def load(name):
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


lg = load("lesson_groups")
sel = load("lane_select")


def row(i, lesson="Always verify X", improves=None, confidence="confirmed"):
    return {"id": i, "improves": improves, "confidence": confidence, "lesson": lesson,
            "problem": f"p{i}", "timestamp": "2026-01-01T00:00:00Z",
            "evidence": [{"source": "artifact", "ref": f"f{i}", "quote": "q"}]}


def run(rows, filed=None):
    calls = []
    filed = set() if filed is None else filed
    res = lg.route(rows, filed, calls.append)
    return res, calls, filed


def test_three_null_target_make_one_issue():
    res, calls, _ = run([row("a"), row("b"), row("c", lesson="  always VERIFY x ")])
    assert len(calls) == 1 and len(res["filed"]) == 1
    p = calls[0]
    assert p["system_generated"] and "system-generated" in p["labels"] and "idea" in p["labels"]
    assert p["learning_ids"] == ["a", "b", "c"] and len(p["evidence"]) == 3


def test_two_make_none_and_count_in_digest():
    res, calls, _ = run([row("a"), row("b"), row("c", lesson="other")])
    assert calls == [] and res["filed"] == []
    assert res["pending"] == 3 and res["pending_groups"] == 2


def test_candidates_routed_here_not_to_lane_and_improves_unchanged():
    rows = [row(x, improves="to-seed", confidence="candidate") for x in "abc"]
    before = copy.deepcopy(rows)
    assert sel.select(rows, open_prs=lambda: []) == []
    res, calls, _ = run(rows)
    assert len(calls) == 1
    assert rows == before and all(r["improves"] == "to-seed" for r in rows)


def test_confirmed_targeted_not_routed_here():
    res, calls, _ = run([row(x, improves="to-seed") for x in "abc"])
    assert calls == [] and res["pending"] == 0


def test_second_kick_and_fourth_learning_do_not_refile():
    rows = [row("a"), row("b"), row("c")]
    filed = set()
    _, calls, filed = run(rows, filed)
    assert len(calls) == 1
    _, calls2, filed = run(rows, filed)
    assert calls2 == []
    _, calls3, _ = run(rows + [row("d")], filed)
    assert calls3 == []


def test_no_improves_set_and_rows_not_mutated():
    rows = [row(x) for x in "abc"] + [row("z", lesson="solo")]
    before = copy.deepcopy(rows)
    run(rows)
    assert rows == before and all(not r["improves"] for r in rows)


def test_no_skill_file_touched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run([row(x) for x in "abc"])
    assert list(tmp_path.iterdir()) == []


def test_state_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    assert lg.load_state(p) == set()
    lg.save_state(p, {"lg-1"})
    assert lg.load_state(p) == {"lg-1"}


def test_failed_create_does_not_abort_other_groups_or_mark_filed():
    rows = [row(x, lesson="bad") for x in "abc"] + [row(x, lesson="good") for x in "def"]
    filed = set()
    seen = []

    def create(p):
        seen.append(p["group_id"])
        if "bad" in p["body"]:
            raise RuntimeError("gh exploded")

    res = lg.route(rows, filed, create)
    assert len(seen) == 2
    assert len(res["filed"]) == 1 and len(filed) == 1
    assert len(res["errors"]) == 1 and "gh exploded" in res["errors"][0]
    bad_gid = lg.group_id("bad")
    assert bad_gid not in filed and bad_gid in res["errors"][0]


def test_errors_empty_on_success():
    res, _, _ = run([row("a"), row("b"), row("c")])
    assert res["errors"] == []
