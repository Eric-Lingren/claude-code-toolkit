import importlib.util
import json
import subprocess
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "learnings-lane" / "lane_gate.py"
spec = importlib.util.spec_from_file_location("lane_gate", p)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

OK = ["fix: x\n\nLearning-Ids: L-1"]


def test_unified_learnings_fails():
    v = g.evaluate(["claude-code-shared/learnings/unified-learnings.jsonl"], OK)
    assert v["verdict"] == "fail" and v["failing_check"] == "no-unified-learnings"


def test_missing_trailer_fails():
    v = g.evaluate(["a.md"], OK + ["fix: y"])
    assert v["verdict"] == "fail" and v["failing_check"] == "learning-ids-trailer"


def test_protected_path_draft_needs_grill():
    v = g.evaluate(["run-all-tests.sh"], OK)
    assert v["verdict"] == "needs-grill" and v["draft"] is True
    assert "needs-grill" in v["labels"] and v["failing_check"] == "protected-paths"


def test_four_files_needs_grill_with_issue():
    seen = []
    v = g.evaluate(["a", "b", "c", "d"], OK, promote=lambda p: seen.append(p) or p)
    assert v["verdict"] == "needs-grill" and v["issue_payload"] and len(seen) == 1
    assert v["draft"] is False


def test_contract_needs_grill_with_issue():
    v = g.evaluate(["claude-code-shared/contracts/x-schema.json"], OK)
    assert v["verdict"] == "needs-grill" and v["failing_check"] == "contract-change"
    assert v["issue_payload"]


def test_three_plain_files_pass():
    v = g.evaluate(["a", "b", "c"], OK)
    assert v["verdict"] == "pass" and v["failing_check"] is None


def _fake_git(files, shas, msgs):
    def fake(*args):
        if args[0] == "diff":
            return "\n".join(files)
        if args[0] == "rev-list":
            return " ".join(shas)
        return msgs[args[-1]]
    return fake


def test_main_pass_exit_0_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(g, "_git", _fake_git(["a"], ["s1"], {"s1": OK[0]}))
    assert g.main(["--base", "B"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "pass" and out["files"] == ["a"]


def test_main_fail_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(g, "_git", _fake_git(["a"], ["s1"], {"s1": "fix: no trailer"}))
    assert g.main(["--base", "B", "--head", "H"]) == 1
    assert json.loads(capsys.readouterr().out)["failing_check"] == "learning-ids-trailer"


def test_main_needs_grill_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(g, "_git", _fake_git(["a", "b", "c", "d"], ["s1"], {"s1": OK[0]}))
    assert g.main(["--base", "B"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "needs-grill"


def test_main_git_failure_exit_2_error_verdict(monkeypatch, capsys):
    def boom(*args):
        raise subprocess.CalledProcessError(128, ["git", *args], stderr="fatal: bad revision\n")
    monkeypatch.setattr(g, "_git", boom)
    assert g.main(["--base", "nope"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out == {"verdict": "error", "failing_check": None, "error": "fatal: bad revision"}
