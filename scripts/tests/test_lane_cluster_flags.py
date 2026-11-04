import importlib.util
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def load(name):
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cf = load("cluster_flags")
vc = load("validate_clusters")


def row(i, fix, improves="build-code", conf="confirmed"):
    return {"id": i, "improves": improves, "fix": fix, "confidence": conf}


def cl(cid, lids, target="build-code", **kw):
    return dict({"id": cid, "target": target, "learning_ids": lids, "root_cause": "rc", "fix": "fx"}, **kw)


def test_opposing_fixes_flag_needs_grill():
    rows = [row("a", "Add a `Step 3` check"), row("b", "Remove the `Step 3` check")]
    out = cf.flag_clusters([cl("c1", ["a"]), cl("c2", ["b"])], rows)
    assert all(c["needs_grill"] and "contradiction" in c["flag_reasons"] for c in out)


def test_opposing_fixes_in_one_cluster_flag():
    rows = [row("a", "Always run `lint`"), row("b", "Never run `lint`")]
    out = cf.flag_clusters([cl("c1", ["a", "b"])], rows)
    assert out[0]["needs_grill"]


def test_model_marker_flags():
    out = cf.flag_clusters([cl("c1", ["a"], contradiction=True)], [row("a", "x")])
    assert out[0]["flag_reasons"] == ["contradiction"]


def test_lane_improves_forced_regardless_of_confidence():
    for name in ("learnings-lane/lane_gate.py", "review-lane", "learnings-clusterer"):
        rows = [row("a", "tweak", improves=name, conf="confirmed")]
        out = cf.flag_clusters([cl("c1", ["a"], target=name)], rows)
        assert out[0]["needs_grill"] and "lane-target" in out[0]["flag_reasons"]
    out = cf.flag_clusters([cl("c1", ["a"])], [row("a", "tweak", improves="learnings_lane.py", conf="high")])
    assert out[0]["needs_grill"]


def test_unflagged_pass_through_unchanged():
    rows = [row("a", "Add a `Step 3` check"), row("b", "Add a `Step 4` check")]
    cs = [cl("c1", ["a"]), cl("c2", ["b"])]
    out = cf.flag_clusters(cs, rows)
    assert out == cs and cf.headless_ready(out) == cs and cf.digest_lines(out) == []


def test_flagged_excluded_from_headless_and_in_digest():
    rows = [row("a", "x", improves="review-lane")]
    out = cf.flag_clusters([cl("c1", ["a"], target="review-lane"), cl("c2", ["b"])], rows + [row("b", "y")])
    assert [c["id"] for c in cf.headless_ready(out)] == ["c2"]
    assert "c1" in cf.digest_lines(out)[0]


def test_validator_carries_contradiction_marker():
    inp = [{"id": "a", "improves": "t"}]
    assert vc.errors(inp, {"clusters": [cl("1", ["a"], contradiction=True)]}) == []
    assert vc.errors(inp, {"clusters": [cl("1", ["a"], contradiction="yes")]}) != []
