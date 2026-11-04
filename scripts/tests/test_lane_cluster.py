import importlib.util
import json
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def load(name):
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pg = load("pregroup")
vc = load("validate_clusters")


def row(i, cause="c1", improves="t", refs=(), path=None):
    r = {"id": i, "improves": improves, "cause": cause,
         "evidence": [{"source": "artifact", "ref": x, "quote": "q"} for x in refs]}
    if path:
        r["escape_point"] = {"path": path}
    return r


def ids(groups):
    return sorted(sorted(r["id"] for r in g) for g in groups)


def cl(cid, lids):
    return {"id": cid, "target": "t", "learning_ids": lids, "root_cause": "rc", "fix": "fx"}


INP = [{"id": "a", "improves": "t"}, {"id": "b", "improves": "t"}, {"id": "c", "improves": "t"}]


def test_validator_accepts_exact_partition():
    assert vc.errors(INP, {"clusters": [cl("1", ["a", "b"]), cl("2", ["c"])]}) == []


def test_validator_rejects_duplicate():
    errs = vc.errors(INP, {"clusters": [cl("1", ["a", "b"]), cl("2", ["b", "c"])]})
    assert any("two clusters" in e for e in errs)


def test_validator_rejects_dropped():
    errs = vc.errors(INP, {"clusters": [cl("1", ["a", "b"])]})
    assert any("learning c in no cluster" in e for e in errs)


def test_validator_rejects_unknown_id():
    errs = vc.errors(INP, {"clusters": [cl("1", ["a", "b", "c", "zz"])]})
    assert any("not in input: zz" in e for e in errs)


def test_pregroup_groups_same_improves_cause_files():
    rows = [row("a", refs=["x.md"]), row("b", refs=["x.md:3", "y.md"]), row("c", path="y.md"),
            row("d", refs=["z.md"]), row("e", cause="c2", refs=["x.md"]),
            row("f", improves="u", refs=["x.md"])]
    assert ids(pg.pregroup(rows)) == [["a", "b", "c"], ["d"], ["e"], ["f"]]


def test_pregroup_many_shared_cause_no_files_stay_singletons():
    rows = [row(f"g{i}", cause="general_best_practice") for i in range(50)]
    rows += [row("p", cause="general_best_practice", refs=["s.md"]),
             row("q", cause="general_best_practice", refs=["s.md"])]
    gs = ids(pg.pregroup(rows))
    assert len(gs) == 51 and ["p", "q"] in gs


def test_contract_validates_sample():
    assert vc.main(["--sample"]) == 0


def test_rules_in_contract_equal_rules_implemented():
    c = json.loads((d / "cluster-contract.json").read_text())
    assert set(c["rules"]) == set(vc.RULES)


def test_validator_reads_structure_from_contract():
    c = vc.CONTRACT
    assert vc.schema_errors({"clusters": []}, c["response"], "r") == ["r.clusters needs at least 1 item(s)"]
    bad = dict(cl("1", ["a"]), extra=1)
    assert any("extra not allowed" in e for e in vc.errors(INP, {"clusters": [bad]}))


def test_validator_rejects_target_mismatch_only_when_target_given():
    out = {"clusters": [cl("1", ["a", "b", "c"])]}
    assert vc.errors(INP, out) == []
    assert vc.errors(INP, out, "t") == []
    assert any("must equal request target" in e for e in vc.errors(INP, out, "other"))


def test_validator_rejects_duplicate_cluster_ids_and_escape_point():
    errs = vc.errors(INP, {"clusters": [cl("1", ["a"]), cl("1", ["b", "c"])]})
    assert any("duplicated" in e for e in errs)
    errs = vc.errors(INP, {"clusters": [dict(cl("1", ["a", "b", "c"]), escape_point={"path": "x"})]})
    assert any("escape_point" in e for e in errs)


def test_validator_rejects_non_boolean_contradiction_and_blank_fields():
    assert vc.errors(INP, {"clusters": [dict(cl("1", ["a", "b", "c"]), contradiction="yes")]})
    assert vc.errors(INP, {"clusters": [dict(cl("1", ["a", "b", "c"]), fix="  ")]})


def test_request_errors_enforced():
    ok = {"target": "t", "learnings": INP, "pregroups": [["a", "b"]]}
    assert vc.request_errors(ok) == []
    assert vc.request_errors(dict(ok, extra=1))
    assert vc.request_errors(dict(ok, target=""))
    assert vc.request_errors(dict(ok, learnings=[]))
    assert vc.request_errors(dict(ok, learnings=[{"id": "a"}]))
    assert any("duplicated" in e for e in vc.request_errors(dict(ok, learnings=[INP[0], INP[0]])))
    assert any("pregroup id not in request" in e for e in vc.request_errors(dict(ok, pregroups=[["zz"]])))
