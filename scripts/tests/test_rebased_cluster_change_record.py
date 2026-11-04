"""A rebase-merged lane PR leaves a single-parent commit on main: no merge commit, no `(#N)`.

Covers the whole chain for that shape: write-change-records.py must still write a learnings-lane
record (ids from the Learning-Ids trailer, no pr: ref), and drift-alert.py must link the commit
for that record instead of a PR.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

SHARED = Path(__file__).resolve().parents[2]
WRITER = SHARED / "scripts" / "attribution" / "write-change-records.py"
DRIFT = SHARED / "scripts" / "usage" / "drift-alert.py"
SCHEMA = json.loads((SHARED / "contracts" / "change-record-schema.json").read_text())

TARGET_REPO = "Org/Repo"
REBASED_AT = "2026-11-02T12:00:00+00:00"  # ISO week 2026-W45
WEEK = "2026-W45"
W44_DAY = "2026-10-27T10:00:00Z"
W45_DAY = "2026-11-03T10:00:00Z"
SKILL_FILE = "claude-code-shared/skills/debug/SKILL.md"
TRAILER_MSG = "Apply learnings cluster\n\nLearning-Ids: L-101, L-102\nLearning-Ids: L-103"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wcr = _load(WRITER, "write_change_records_rebased")
drift = _load(DRIFT, "drift_alert_rebased")


def git(repo, *args, date=None):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
    )
    if date:
        env["GIT_COMMITTER_DATE"] = date
        env["GIT_AUTHOR_DATE"] = date
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def rebased(tmp_path):
    """Repo whose main holds a rebase-merged cluster commit. Returns (repo, original, rebased)."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, SKILL_FILE, "---\nname: debug\n---\n\n## Fix\n\nv1\n")
    write(r, "README.md", "readme\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "initial", date="2026-10-01T00:00:00+00:00")

    git(r, "checkout", "-q", "-b", "learnings/cluster-1", "main")
    write(r, SKILL_FILE, "---\nname: debug\n---\n\n## Fix\n\nv2 from learnings\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", TRAILER_MSG, date="2026-10-20T12:00:00+00:00")
    original = git(r, "rev-parse", "HEAD")

    # main moves on, so the rebase really rewrites the branch commit (new sha).
    git(r, "checkout", "-q", "main")
    write(r, "README.md", "readme, edited\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "docs only", date="2026-10-25T00:00:00+00:00")

    git(r, "checkout", "-q", "learnings/cluster-1")
    git(r, "rebase", "-q", "main", date=REBASED_AT)
    git(r, "checkout", "-q", "main")
    git(r, "merge", "--ff-only", "-q", "learnings/cluster-1")
    return r, original, git(r, "rev-parse", "HEAD")


def write_records(repo, tmp_path):
    out = tmp_path / "changes"
    result = wcr.write_change_records(repo, out, since="2000-01-01", ref="main",
                                      target_repo=TARGET_REPO)
    return out, result


def attribution_rows():
    """One W44 learning and four W45 ones for skill debug: a rate rise that trips drift."""
    row = {"type": "attribution", "improves": "debug", "improves_type": "skill"}
    return [dict(row, timestamp=W44_DAY, id="w44")] + [
        dict(row, timestamp=W45_DAY, id=f"n{i}") for i in range(4)]


def test_fixture_is_a_real_rebase_merge(rebased):
    repo, original, rebased_sha = rebased
    assert rebased_sha != original  # rebase rewrote the commit
    assert len(git(repo, "rev-list", "--parents", "-n", "1", rebased_sha).split()) == 2  # one parent
    assert git(repo, "log", "-1", "--format=%s", "main") == "Apply learnings cluster"


def test_rebased_cluster_commit_gets_learnings_record_without_pr_ref(rebased, tmp_path):
    repo, _, rebased_sha = rebased
    out, result = write_records(repo, tmp_path)
    assert result["errors"] == []
    assert result["written"] == [f"chg-{rebased_sha[:12]}"]
    rec = json.loads((out / f"chg-{rebased_sha[:12]}.json").read_text())
    jsonschema.validate(rec, SCHEMA)
    assert rec["lane"] == "learnings"
    assert rec["source_refs"] == ["L-101", "L-102", "L-103"]
    assert not any(ref.startswith("pr:") for ref in rec["source_refs"])
    assert rec["merged_sha"] == rebased_sha
    assert rec["touched_units"] == ["skill:debug"]
    assert rec["target_repo"] == TARGET_REPO


def test_plain_commit_on_main_beside_rebased_one_gets_no_record(rebased, tmp_path):
    _, _, rebased_sha = rebased
    repo = rebased[0]
    _, result = write_records(repo, tmp_path)
    assert len(result["written"]) == 1
    assert len(result["ignored"]) == 1  # the "docs only" commit on main
    assert rebased_sha not in result["ignored"]


def test_drift_alert_links_commit_not_pr_for_rebased_record(rebased, tmp_path):
    repo, _, rebased_sha = rebased
    out, _ = write_records(repo, tmp_path)
    changes = drift.load_changes(str(out))
    assert len(changes) == 1
    commit_url = f"https://github.com/{TARGET_REPO}/commit/{rebased_sha}"
    assert drift.change_link(changes[0]) == ("commit", commit_url)

    runs = {"debug": {"2026-W45": 10, "2026-W44": 10}}
    (alert,) = drift.detect(attribution_rows(), runs, changes, WEEK)
    (suspect,) = alert["suspects"]
    assert suspect["change_id"] == f"chg-{rebased_sha[:12]}"
    assert suspect["link_kind"] == "commit"
    assert suspect["url"] == commit_url
    assert commit_url in alert["text"]
    assert "/pull/" not in alert["text"]


def test_drift_alert_cli_output_links_commit_not_pr(rebased, tmp_path):
    repo, _, rebased_sha = rebased
    out, _ = write_records(repo, tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "snapshots-alpha.json").write_text(json.dumps({
        wk: {"adherence": {"skills": {"debug": {"exp": "sonnet", "mix": {"sonnet": 10}}}}}
        for wk in ("2026-W45", "2026-W44")}))
    lpath = tmp_path / "learnings.jsonl"
    lpath.write_text("".join(json.dumps(r) + "\n" for r in attribution_rows()))
    proc = subprocess.run(
        [sys.executable, str(DRIFT), "--week", WEEK, "--report-dir", str(reports),
         "--learnings", str(lpath), "--changes-dir", str(out), "--no-notify"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert f"https://github.com/{TARGET_REPO}/commit/{rebased_sha}" in proc.stdout
    assert "/pull/" not in proc.stdout
