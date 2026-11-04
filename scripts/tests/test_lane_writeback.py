import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"


def _load(n):
    spec = importlib.util.spec_from_file_location(n, d / f"{n}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wb = _load("lane_writeback")
ls = _load("lane_select")

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
SHA = "abc123def456"


def make(tmp_path, monkeypatch, ids=("L1", "L2", "L3")):
    monkeypatch.setenv("LOG_LEARNING_DEST", str(tmp_path))
    rows = [{"schema_version": "2", "id": i, "timestamp": "2026-10-01T00:00:00Z", "type": "self",
             "reported_by": "t", "improves": "skill-x", "improves_type": "skill",
             "cause": "general_best_practice", "cause_label": None, "problem": "p",
             "why_missed": "w", "lesson": "l", "fix": "f", "evidence": [{"source": "transcript", "ref": "r", "quote": "q"}],
             "confidence": "confirmed", "status": "captured"} for i in ids]
    (tmp_path / "unified-learnings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def rows(tmp_path):
    return {r["id"]: r for r in ls.load_rows(tmp_path / "unified-learnings.jsonl")}


def pr(state, **kw):
    p = {"number": 7, "state": state, "merge_sha": None, "close_comment": "",
         "commit_messages": ["Fix skill\n\nBody\n\nLearning-Ids: L1, L2\n", "More\n\nLearning-Ids: L3"]}
    p.update(kw)
    return p


def test_merged_sets_applied_with_sha(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    res = wb.writeback(lambda: [pr("MERGED", merge_sha=SHA)], tmp_path)
    r = rows(tmp_path)
    assert not res["errors"], res
    assert res["applied"] == ["L1", "L2", "L3"]
    assert all(r[i]["status"] == "applied" and r[i]["merged_sha"] == SHA for i in r)


def test_closed_sets_rejected_with_comment(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    wb.writeback(lambda: [pr("CLOSED", close_comment="not wanted")], tmp_path)
    r = rows(tmp_path)
    assert all(r[i]["status"] == "rejected" and r[i]["reason"] == "not wanted" for i in r)


def test_open_changes_nothing_and_rerun_idempotent(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    f = tmp_path / "unified-learnings.jsonl"
    before = f.read_text()
    assert wb.writeback(lambda: [pr("OPEN")], tmp_path)["applied"] == []
    assert f.read_text() == before
    wb.writeback(lambda: [pr("MERGED", merge_sha=SHA)], tmp_path)
    after = f.read_text()
    res = wb.writeback(lambda: [pr("MERGED", merge_sha=SHA)], tmp_path)
    assert res == {"applied": [], "rejected": [], "errors": []}
    assert f.read_text() == after


def test_rejected_not_selected(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    assert ls.select(ls.load_rows(tmp_path / "unified-learnings.jsonl"), NOW, lambda: []) != []
    wb.writeback(lambda: [pr("CLOSED", close_comment="no")], tmp_path)
    assert ls.select(ls.load_rows(tmp_path / "unified-learnings.jsonl"), NOW, lambda: []) == []


def test_trailer_parsing():
    assert wb.trailer_ids(["a\n\nLearning-Ids: x, y z\nLearning-Ids: y,w", "nothing"]) == ["x", "y", "z", "w"]


# ---- escape_point from the merged commit diff ----
VEP = d.parent / "learning" / "validate-escape-point.py"
DOC = "# T\n\nintro\n\n## A\n\na1\n\n## B\n\nb1\n"
MSG = "change\n\nLearning-Ids: L1, L2"


def _git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def run_ep(tmp_path, monkeypatch, files, preset=None):
    ldir = tmp_path / "l"
    ldir.mkdir()
    make(ldir, monkeypatch)
    if preset:
        f = ldir / "unified-learnings.jsonl"
        recs = [json.loads(x) for x in f.read_text().splitlines()]
        recs[0]["escape_point"] = preset
        f.write_text("".join(json.dumps(x) + "\n" for x in recs))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    for name, text in {"skills/x/SKILL.md": DOC, "contracts/c.json": '{"a": 1}\n'}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", MSG)
    sha = _git(repo, "rev-parse", "HEAD")
    p = pr("MERGED", merge_sha=sha, commits=[{"sha": sha, "message": MSG}], commit_messages=[MSG])
    res = wb.writeback(lambda: [p], ldir, repo=repo)
    assert not res["errors"], res
    return rows(ldir), repo


def test_one_section_stamped_on_all_members(tmp_path, monkeypatch):
    r, repo = run_ep(tmp_path, monkeypatch, {"skills/x/SKILL.md": DOC.replace("a1", "a2")})
    for i in ("L1", "L2"):
        ep = r[i]["escape_point"]
        assert ep["path"] == "skills/x/SKILL.md" and ep["section"].lstrip("# ") == "A"
        out = subprocess.run([sys.executable, str(VEP), "--root", str(repo)], input=json.dumps(ep),
                             capture_output=True, text=True).stdout
        assert json.loads(out) == ep
    assert "escape_point" not in r["L3"] and r["L3"]["status"] == "captured"


def test_two_sections_leave_null(tmp_path, monkeypatch):
    r, _ = run_ep(tmp_path, monkeypatch, {"skills/x/SKILL.md": DOC.replace("a1", "a2").replace("b1", "b2")})
    assert "escape_point" not in r["L1"] and "escape_point" not in r["L2"]


def test_json_file_leaves_null(tmp_path, monkeypatch):
    r, _ = run_ep(tmp_path, monkeypatch, {"contracts/c.json": '{"a": 2}\n'})
    assert "escape_point" not in r["L1"] and r["L1"]["status"] == "applied"


def test_existing_escape_point_kept(tmp_path, monkeypatch):
    keep = {"path": "other.md", "section": None}
    r, _ = run_ep(tmp_path, monkeypatch, {"skills/x/SKILL.md": DOC.replace("a1", "a2")}, preset=keep)
    assert r["L1"]["escape_point"] == keep
    assert r["L2"]["escape_point"]["path"] == "skills/x/SKILL.md"


def test_escape_walk_skipped_when_nothing_pending(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch, ids=("L1",))
    msg = "x\n\nLearning-Ids: L1"
    wb.writeback(lambda: [pr("MERGED", merge_sha=SHA, commit_messages=[msg])], tmp_path)
    calls = []
    monkeypatch.setattr(wb, "commit_escape_point", lambda repo, sha: calls.append(sha))
    p = pr("MERGED", merge_sha=SHA, commit_messages=[msg], commits=[{"sha": "s1", "message": msg}])
    wb.writeback(lambda: [p], tmp_path, repo=tmp_path)
    assert calls == []


def test_dash_led_reason_recorded(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    res = wb.writeback(lambda: [pr("CLOSED", close_comment="--wontfix")], tmp_path)
    r = rows(tmp_path)
    assert not res["errors"], res
    assert all(r[i]["status"] == "rejected" and r[i]["reason"] == "--wontfix" for i in r)


def test_gh_pr_state_trusts_prefix_and_owner(monkeypatch):
    def c(body, assoc):
        return {"body": body, "authorAssociation": assoc}
    data = [
        {"number": 1, "state": "CLOSED", "headRefName": "learnings-lane/x", "commits": [],
         "comments": [c("owner says no", "OWNER"), c("stranger spam", "NONE")]},
        {"number": 2, "state": "CLOSED", "headRefName": "evil/branch", "commits": [],
         "comments": [c("x", "OWNER")]},
        {"number": 3, "state": "CLOSED", "headRefName": "learnings-lane/y", "commits": [],
         "comments": [c("stranger only", "NONE")]},
    ]

    class R:
        stdout = json.dumps(data)
    monkeypatch.setattr(wb.subprocess, "run", lambda *a, **k: R())
    out = wb.gh_pr_state()
    assert [p["number"] for p in out] == [1, 3]
    assert out[0]["close_comment"] == "owner says no"
    assert out[1]["close_comment"] == ""


def test_gh_pr_state_maps_merge_commit_and_commits(monkeypatch):
    data = [
        {"number": 4, "state": "MERGED", "headRefName": "learnings-lane/a", "comments": [],
         "mergeCommit": {"oid": "deadbeef"},
         "commits": [{"oid": "c1", "messageHeadline": "Head", "messageBody": "Learning-Ids: L1"}]},
        {"number": 5, "state": "MERGED", "headRefName": "learnings-lane/b", "comments": None,
         "mergeCommit": None, "commits": None},
    ]

    class R:
        stdout = json.dumps(data)
    monkeypatch.setattr(wb.subprocess, "run", lambda *a, **k: R())
    out = wb.gh_pr_state()
    assert out[0]["merge_sha"] == "deadbeef"
    assert out[0]["commits"] == [{"sha": "c1", "message": "Head\n\nLearning-Ids: L1"}]
    assert out[0]["commit_messages"] == ["Head\n\nLearning-Ids: L1"]
    assert out[1]["merge_sha"] is None
    assert out[1]["commits"] == [] and out[1]["close_comment"] == ""


def test_merged_without_sha_errors_and_changes_nothing(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch)
    f = tmp_path / "unified-learnings.jsonl"
    before = f.read_text()
    res = wb.writeback(lambda: [pr("MERGED", merge_sha=None)], tmp_path)
    assert res["errors"] == ["PR 7: merged but no merge sha"]
    assert res["applied"] == [] and res["rejected"] == []
    assert f.read_text() == before


def test_update_learning_failure_recorded_and_skipped(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch, ids=("L1",))

    class P:
        returncode = 2
        stderr = " boom \n"
    monkeypatch.setattr(wb, "_update", lambda args, env: P())
    p = pr("MERGED", merge_sha=SHA, commit_messages=["x\n\nLearning-Ids: L1"])
    res = wb.writeback(lambda: [p], tmp_path)
    assert res == {"applied": [], "rejected": [], "errors": ["L1: boom"]}
    assert rows(tmp_path)["L1"]["status"] == "captured"
