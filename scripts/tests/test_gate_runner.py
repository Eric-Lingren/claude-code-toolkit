import importlib.util
import json
import os
import tempfile
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "learnings-lane" / "gate_runner.py"
spec = importlib.util.spec_from_file_location("gate_runner", p)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

PASS = lambda: {"ok": True, "log": "fine"}


def test_all_pass_ready():
    v = g.run_gate([("det", PASS), ("tests", PASS), ("smoke", PASS), ("review", PASS)])
    assert v["verdict"] == "ready" and v["draft"] is False and v["failing_layer"] is None


def test_smoke_fail_blocked_draft_names_layer():
    v = g.run_gate([("det", PASS), ("smoke", lambda: {"ok": False, "log": "FAIL x.md"})])
    assert v["verdict"] == "blocked" and v["draft"] is True and v["labels"] == ["blocked"]
    assert v["failing_layer"] == "smoke"
    assert "smoke" in v["pr_body"] and "FAIL x.md" in v["pr_body"]


def test_gate_failure_not_retried():
    calls = []

    def fn():
        calls.append(1)
        return {"ok": False, "infra": False, "log": "bad"}

    v = g.run_gate([("smoke", fn)])
    assert len(calls) == 1 and v["attempts"]["smoke"] == 1


def test_infra_failure_retried_exactly_once():
    calls = []

    def fn():
        calls.append(1)
        return {"ok": False, "infra": True, "log": "timeout"}

    v = g.run_gate([("smoke", fn)])
    assert len(calls) == 2 and v["verdict"] == "blocked"


def test_infra_then_success_ready():
    seq = iter([{"ok": False, "infra": True, "log": "t"}, {"ok": True, "log": "ok"}])
    v = g.run_gate([("smoke", lambda: next(seq))])
    assert v["verdict"] == "ready" and v["attempts"]["smoke"] == 2


def test_blocking_review_finding_blocks():
    layer = g.review_layer(lambda effort: [{"severity": "blocking", "message": "sql injection"}], "high")
    v = g.run_gate([("review", layer)])
    assert v["verdict"] == "blocked" and v["failing_layer"] == "review" and "sql injection" in v["pr_body"]


def test_nonblocking_review_passes_and_effort_forwarded():
    seen = []
    layer = g.review_layer(lambda e: seen.append(e) or [{"severity": "nit"}], "high")
    assert g.run_gate([("review", layer)])["verdict"] == "ready" and seen == ["high"]


def test_snapshot_matches_ci_snapshot_format():
    layer = g.tests_layer(lambda: {"a.sh": "pass", "b.py": "pass"}, lambda: {"a.sh": "pass", "b.py": "fail"})
    res = layer()
    head = res["snapshot"]["head"]
    assert head == {"results": {"a.sh": "pass", "b.py": "fail"}, "totals": {"passed": 1, "failed": 1, "total": 2}}
    assert res["ok"] is False and res["diff"]["newly_failing"] == ["b.py"]
    mod = g._load("csd", g.SHARED / "attribution" / "ci-snapshot-diff.py")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(head, f)
    try:
        assert mod.load_results(f.name) == head["results"]
    finally:
        os.unlink(f.name)


def test_suite_crash_is_infra():
    def boom():
        raise RuntimeError("x")

    assert g.tests_layer(boom, boom)()["infra"] is True


def test_smoke_layer_passes_root_as_argv1_and_cwd(tmp_path):
    seen = {}

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return P()

    orig = g.procgroup.run_killpg
    g.procgroup.run_killpg = fake_run
    try:
        assert g.smoke_layer(root=tmp_path)()["ok"] is True
    finally:
        g.procgroup.run_killpg = orig
    assert seen["cmd"][-1] == str(tmp_path)
    assert seen["cmd"][-2].endswith("smoke-test.py")
    assert seen["kw"]["cwd"] == tmp_path
