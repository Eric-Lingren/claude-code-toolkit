"""kick.py end to end with stubbed claude, gh, branch push and learnings push."""
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

d = Path(__file__).resolve().parent.parent / "learnings-lane"
spec = importlib.util.spec_from_file_location("kick_under_test", d / "kick.py")
kick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kick)

REL = "claude-code-shared/learnings/unified-learnings.jsonl"


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def row(i, target, **kw):
    r = {"schema_version": "2", "id": i, "timestamp": "2026-10-01T00:00:00Z", "type": "self", "reported_by": "t",
         "improves": target, "improves_type": "skill" if target else None, "cause": "general_best_practice",
         "cause_label": None, "problem": f"p{i}", "why_missed": "w", "lesson": f"l{i}", "fix": f"f{i}",
         "evidence": [{"source": "transcript", "ref": "r", "quote": "q"}], "confidence": "confirmed",
         "status": "captured"}
    r.update(kw)
    return r


def setup(tmp_path, targets=("alpha", "beta"), extra=()):
    repo = tmp_path / "repo"
    ldir = repo / "claude-code-shared" / "learnings"
    ldir.mkdir(parents=True)
    rows = []
    for t in targets:
        (repo / "claude-code-shared" / "skills" / t).mkdir(parents=True)
        (repo / "claude-code-shared" / "skills" / t / "SKILL.md").write_text("# s\n")
        rows += [row(f"{t}{n}", t) for n in range(3)]
    rows += list(extra)
    (ldir / "unified-learnings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo, ldir


def stub_claude(path, code):
    path.write_text(f"#!/bin/sh\n{'echo boom >&2; ' if code else ''}exit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture(autouse=True)
def _claude_stub(tmp_path, monkeypatch):
    """Never call the real claude: the boot check honours CLAUDE_BIN."""
    monkeypatch.setenv("CLAUDE_BIN", stub_claude(tmp_path / "claude_ok", 0))


class Stubs:
    def __init__(self, timeout_on=()):
        self.timeout_on = set(timeout_on)
        self.gh_calls, self.pushes, self.branch_pushes, self.wt_unified = [], 0, [], {}
        self.cross_issues, self.lesson_issues = [], []
        self.gh_at_push = None

    def run_target(self, target, worktree, run_file, timeout_s):
        if target in self.timeout_on:
            raise subprocess.TimeoutExpired("headless", timeout_s)
        f = Path(worktree) / "claude-code-shared" / "skills" / target / "SKILL.md"
        f.write_text("# s\nfixed\n")
        git(worktree, "add", ".")
        ids = [f"{target}{n}" for n in range(3)]
        git(worktree, "commit", "-qm", f"improve({target}): fix", "-m", f"Learning-Ids: {','.join(ids)}")
        sha = git(worktree, "rev-parse", "HEAD")
        doc = {"version": "1.0", "target": target,
               "clusters": [{"id": "c1", "status": "committed", "learning_ids": ids, "applied_ids": ids, "commit": sha,
                             "target": target, "root_cause": f"root {target}", "fix": "do it"}],
               "verdicts": [{"learning_id": ids[2], "cluster_id": "c1", "verdict": "stale", "reason": "already there"}]}
        Path(run_file).write_text(json.dumps(doc))
        self.wt_unified[target] = git(worktree, "status", "--porcelain", "--", REL)

    def gh(self, args):
        self.gh_calls.append(args)
        if args[:2] == ["pr", "list"]:
            return "[]"
        if args[:2] == ["repo", "view"]:
            return json.dumps({"rebaseMergeAllowed": True})
        if args[:2] == ["pr", "create"]:
            return f"https://example/pr/{args[args.index('--head') + 1]}\n"
        raise AssertionError(args)

    def push(self, repo):
        self.pushes += 1
        self.gh_at_push = len(self.gh_calls)
        return {"status": "nothing", "message": "ok"}

    def deps(self, **kw):
        base = dict(pr_state=lambda: [], open_prs=lambda: [], run_target=self.run_target,
                    gate_layers=lambda t, w: [("ok", lambda: {"ok": True, "log": ""})], gh=self.gh,
                    push_branch=lambda w, b: self.branch_pushes.append(b),
                    create_lesson_issue=lambda p: self.lesson_issues.append(p),
                    create_cross_issue=lambda p: self.cross_issues.append(p) or 1,
                    push_learnings=self.push)
        base.update(kw)
        return kick.Deps(**base)


def test_e2e_one_pr_per_target_and_unified_untouched(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs()
    out = kick.kick(repo, ldir, s.deps(), base="main", run_dir=tmp_path / "run")
    created = [c for c in s.gh_calls if c[:2] == ["pr", "create"]]
    assert sorted(c[c.index("--head") + 1] for c in created) == ["learnings-lane/alpha", "learnings-lane/beta"]
    assert len(out["opened"]) == 2
    assert s.wt_unified == {"alpha": "", "beta": ""}
    for t in ("alpha", "beta"):
        assert REL not in git(repo, "diff", "--name-only", f"main..learnings-lane/{t}").splitlines()
    assert sorted(s.branch_pushes) == ["learnings-lane/alpha", "learnings-lane/beta"]
    assert {x["id"] for x in out["dropped"]} == {"alpha2", "beta2"}


def test_dry_run_no_gh_no_commits(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs()
    before = git(repo, "rev-parse", "HEAD")
    raw = (ldir / "unified-learnings.jsonl").read_text()
    out = kick.kick(repo, ldir, s.deps(), dry_run=True, run_dir=tmp_path / "run")
    assert s.gh_calls == [] and s.pushes == 0 and s.branch_pushes == []
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "worktree", "list").count("\n") == 0
    assert git(repo, "branch", "--list", "learnings-lane/*") == ""
    assert (ldir / "unified-learnings.jsonl").read_text() == raw
    assert out["targets"] == ["alpha", "beta"]
    assert any("would run" in line for line in out["lines"])


def test_timeout_logged_others_continue(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha", "beta", "gamma"))
    s = Stubs(timeout_on={"beta"})
    ev = tmp_path / "ev.jsonl"
    out = kick.kick(repo, ldir, s.deps(), run_dir=tmp_path / "run", events=ev)
    assert out["timeouts"] == ["beta"]
    assert sorted(p["target"] for p in out["opened"]) == ["alpha", "gamma"]
    events = [json.loads(line) for line in ev.read_text().splitlines()]
    assert any(e["event"] == "target_timeout" and e["target"] == "beta" for e in events)


def test_digest_counts(tmp_path):
    lessons = [row(f"n{n}", None, lesson="same lesson", confidence="candidate") for n in range(2)]
    lessons += [row("q1", None, lesson="lonely", confidence="candidate")]
    repo, ldir = setup(tmp_path, extra=lessons)

    def layers(t, w):
        if t == "beta":
            return [("tests", lambda: {"ok": False, "infra": False, "log": "boom"})]
        return [("ok", lambda: {"ok": True, "log": ""})]

    s = Stubs()
    out = kick.kick(repo, ldir, s.deps(gate_layers=layers), run_dir=tmp_path / "run")
    text = "\n".join(out["lines"])
    assert "PRs opened: 2" in text
    assert "blocked: 1" in text and "blocked: beta at tests" in text
    assert "needs-grill: 0" in text
    assert "dropped learnings: 2" in text
    assert "null-target groups: 0 filed, 2 pending (3 learnings)" in text
    assert "lane health" in text


def test_push_once_at_end(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs()
    out = kick.kick(repo, ldir, s.deps(), run_dir=tmp_path / "run")
    assert s.pushes == 1
    assert s.gh_at_push == len(s.gh_calls)  # no gh call after the push
    assert out["push"]["status"] == "nothing"


def _events(p):
    return [json.loads(line) for line in Path(p).read_text().splitlines()]


def test_boot_failed_logged_with_main_sha_and_in_digest(tmp_path, monkeypatch):
    repo, ldir = setup(tmp_path)
    monkeypatch.setenv("CLAUDE_BIN", stub_claude(tmp_path / "claude_bad", 3))
    ev = tmp_path / "ev.jsonl"
    out = kick.kick(repo, ldir, Stubs().deps(), run_dir=tmp_path / "run", events=ev)
    sha = git(repo, "rev-parse", "main")
    bf = [e for e in _events(ev) if e["event"] == "boot_failed"]
    assert len(bf) == 1 and bf[0]["main_sha"] == sha and "boom" in bf[0]["error"]
    text = "\n".join(out["lines"])
    assert f"BOOT FAILED on main {sha}" in text and "change-revert.sh" in text
    assert len(out["opened"]) == 2  # pass carries on
    assert git(repo, "worktree", "list").count("\n") == 0  # boot worktree cleaned


def test_boot_failed_distinct_from_run_failure(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs(timeout_on={"beta"})
    ev = tmp_path / "ev.jsonl"
    out = kick.kick(repo, ldir, s.deps(boot=lambda r, sha, rd: (_ for _ in ()).throw(RuntimeError("x"))),
                    run_dir=tmp_path / "run", events=ev)
    kinds = {e["event"] for e in _events(ev)}
    assert {"boot_failed", "target_timeout"} <= kinds
    assert "target_failed" not in kinds
    assert out["failed"] == [] and out["boot_failed"]["error"] == "x"


def test_passing_boot_logs_no_failure(tmp_path):
    repo, ldir = setup(tmp_path)
    ev = tmp_path / "ev.jsonl"
    out = kick.kick(repo, ldir, Stubs().deps(), run_dir=tmp_path / "run", events=ev)
    assert out["boot_failed"] is None
    assert not any(e["event"] == "boot_failed" for e in _events(ev))
    assert "BOOT FAILED" not in "\n".join(out["lines"])


def test_failed_lesson_issue_does_not_abort_pass(tmp_path):
    lessons = [row(f"g{n}", None, lesson="good lesson", confidence="candidate") for n in range(3)]
    lessons += [row(f"b{n}", None, lesson="bad lesson", confidence="candidate") for n in range(3)]
    repo, ldir = setup(tmp_path, extra=lessons)
    s = Stubs()

    def create(p):
        if "bad lesson" in p["body"]:
            raise subprocess.CalledProcessError(1, "gh")
        s.lesson_issues.append(p)

    out = kick.kick(repo, ldir, s.deps(create_lesson_issue=create), run_dir=tmp_path / "run")
    state = json.loads((ldir / "lesson-groups-filed.json").read_text())
    assert len(state) == 1 and len(s.lesson_issues) == 1  # filed group persisted, failed one not
    assert s.pushes == 1 and out["push"] is not None  # pass carried on to the push
    assert any("lesson issue" in e for e in out["errors"])
    assert "warning:" in "\n".join(out["lines"])


def test_bind_gh_cwd_binds_defaults_only(tmp_path):
    custom = lambda *a, **k: None  # noqa: E731
    d = kick._bind_gh_cwd(kick.Deps(gh=custom), str(tmp_path))
    assert d.gh is custom
    for name in ("pr_state", "open_prs", "create_lesson_issue", "create_cross_issue"):
        assert d.__dict__[name].keywords == {"cwd": str(tmp_path)}


def test_push_status_in_digest_and_warns_on_refused(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs()
    out = kick.kick(repo, ldir, s.deps(), run_dir=tmp_path / "run")
    assert "learnings push: nothing (ok)" in out["lines"]
    assert not any(l.startswith("warning: learnings push") for l in out["lines"])
    for status in ("refused", "failed"):
        repo, ldir = setup(tmp_path / status)
        s = Stubs()
        s.push = lambda r, st=status: {"status": st, "message": "boom"}
        out = kick.kick(repo, ldir, s.deps(), run_dir=tmp_path / status / "run")
        assert f"warning: learnings push {status}: boom" in out["lines"]


def test_default_push_branch_force_pushes_rebuilt_lane_branch(tmp_path):
    """Remote branch left by a merged lane PR must not make the next push non-fast-forward."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-b", "main", str(wt)], check=True, capture_output=True)
    git(wt, "config", "user.email", "t@t")
    git(wt, "config", "user.name", "t")
    git(wt, "remote", "add", "origin", str(remote))
    (wt / "f").write_text("1")
    git(wt, "add", "f")
    git(wt, "commit", "-m", "base")
    git(wt, "checkout", "-b", "learnings-lane/alpha")
    (wt / "f").write_text("old")
    git(wt, "commit", "-am", "old run")
    kick.default_push_branch(wt, "learnings-lane/alpha")
    # next run: rebuilt from base, diverges from the remote branch
    git(wt, "reset", "--hard", "main")
    (wt / "f").write_text("new")
    git(wt, "commit", "-am", "new run")
    kick.default_push_branch(wt, "learnings-lane/alpha")
    assert git(remote, "rev-parse", "learnings-lane/alpha") == git(wt, "rev-parse", "HEAD")


def test_refused_pr_leaves_learnings_untouched_and_notes_pending_branch(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha",))
    s = Stubs()
    raw = (ldir / "unified-learnings.jsonl").read_text()

    def gh(args):
        if args[:2] == ["repo", "view"]:
            return json.dumps({"rebaseMergeAllowed": False})
        return s.gh(args)

    out = kick.kick(repo, ldir, s.deps(gh=gh), base="main", run_dir=tmp_path / "run")
    assert out["opened"] == [] and out["dropped"] == []
    assert (ldir / "unified-learnings.jsonl").read_text() == raw
    assert any("learnings-lane/alpha pushed without a PR" in e for e in out["errors"])


def test_existing_pr_not_recreated_and_verdicts_applied(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha",))
    s = Stubs()

    def gh(args):
        if args[:2] == ["pr", "list"] and "--head" in args:
            return json.dumps([{"url": "https://example/pr/existing"}])
        return s.gh(args)

    out = kick.kick(repo, ldir, s.deps(gh=gh), base="main", run_dir=tmp_path / "run")
    assert [c for c in s.gh_calls if c[:2] == ["pr", "create"]] == []
    assert out["opened"][0]["url"] == "https://example/pr/existing"
    assert {x["id"] for x in out["dropped"]} == {"alpha2"}


def test_no_commit_target_cools_down_and_is_not_rerun(tmp_path):
    import time
    repo, ldir = setup(tmp_path, targets=("alpha",))
    calls = []

    def run_target(target, worktree, run_file, timeout_s):
        calls.append(target)
        doc = {"version": "1.0", "target": target, "verdicts": [],
               "clusters": [{"id": "c1", "status": "needs-grill", "reasons": ["unclear"], "learning_ids": ["alpha0"],
                             "target": target, "root_cause": "r"}]}
        Path(run_file).write_text(json.dumps(doc))

    s = Stubs()
    kick.kick(repo, ldir, s.deps(run_target=run_target), run_dir=tmp_path / "run1")
    assert calls == ["alpha"]
    assert "alpha" in json.loads((ldir / kick.ATTEMPTS_FILE).read_text())
    kick.kick(repo, ldir, s.deps(run_target=run_target), run_dir=tmp_path / "run2")
    assert calls == ["alpha"]
    assert kick.cooling_targets(ldir / kick.ATTEMPTS_FILE, now=time.time() + 8 * 86400) == set()


def test_gh_failure_in_writeback_and_select_does_not_abort_pass(tmp_path):
    repo, ldir = setup(tmp_path)
    s = Stubs()

    def boom():
        raise subprocess.CalledProcessError(1, "gh")

    out = kick.kick(repo, ldir, s.deps(pr_state=boom, open_prs=boom), run_dir=tmp_path / "run")
    assert any(e.startswith("writeback:") for e in out["errors"])
    assert any(e.startswith("select:") for e in out["errors"])
    assert out["targets"] == [] and s.pushes == 1  # pass carried on to the push


def _extra_commit_run_target(s, rel, body="x\n"):
    """Wrap Stubs.run_target: after its trailer commit, add a second trailer commit touching `rel`."""
    def run_target(target, worktree, run_file, timeout_s):
        s.run_target(target, worktree, run_file, timeout_s)
        f = Path(worktree) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        git(worktree, "add", ".")
        git(worktree, "commit", "-qm", "touch", "-m", f"Learning-Ids: {target}0")
    return run_target


def _created_pr(s):
    (c,) = [c for c in s.gh_calls if c[:2] == ["pr", "create"]]
    return c


def test_gate_for_blocked_when_commit_touches_unified_learnings(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha",))
    s = Stubs()
    rt = _extra_commit_run_target(s, REL, "{}\n")
    out = kick.kick(repo, ldir, s.deps(run_target=rt), base="main", run_dir=tmp_path / "run")
    c = _created_pr(s)
    labels = [c[i + 1] for i, a in enumerate(c) if a == "--label"]
    assert "blocked" in labels and "needs-grill" not in labels
    assert "--draft" in c
    assert out["opened"][0]["verdict"] == "blocked"
    assert out["blocked"] == ["blocked: alpha at no-unified-learnings"]


def test_gate_for_needs_grill_when_commit_touches_protected_path(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha",))
    s = Stubs()
    rt = _extra_commit_run_target(s, "claude-code-shared/scripts/learnings-lane/zz_new.py")
    out = kick.kick(repo, ldir, s.deps(run_target=rt), base="main", run_dir=tmp_path / "run")
    c = _created_pr(s)
    labels = [c[i + 1] for i, a in enumerate(c) if a == "--label"]
    assert "needs-grill" in labels and "blocked" not in labels
    assert "--draft" in c
    assert out["opened"][0]["verdict"] == "needs-grill"
    assert out["needs_grill"] == ["needs-grill: alpha PR (protected-paths)"]


def test_gate_for_needs_grill_when_commit_touches_contract_path(tmp_path):
    repo, ldir = setup(tmp_path, targets=("alpha",))
    s = Stubs()
    rt = _extra_commit_run_target(s, "claude-code-shared/contracts/x.json")
    out = kick.kick(repo, ldir, s.deps(run_target=rt), base="main", run_dir=tmp_path / "run")
    c = _created_pr(s)
    labels = [c[i + 1] for i, a in enumerate(c) if a == "--label"]
    assert "needs-grill" in labels and "--draft" in c
    assert out["opened"][0]["verdict"] == "needs-grill"
