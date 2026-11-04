"""Tests for the weekly drift / verified-regression detector (usage/drift-alert.py).

The script filename has a hyphen, so it is loaded through importlib (and run via
subprocess for the CLI tests). Notification delivery is always stubbed through
DRIFT_ALERT_NOTIFY_CMD: no real banner is ever raised.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "usage" / "drift-alert.py")
WEEKLY = (pathlib.Path(__file__).resolve().parents[1]
          / "usage" / "weekly-usage-report.sh")


def load_module(name="drift_alert"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return load_module()


# 2026-11-03 is in ISO week 2026-W45; 2026-10-27 in W44.
W45_DAY = "2026-11-03T10:00:00Z"
W44_DAY = "2026-10-27T10:00:00Z"
WEEK = "2026-W45"


def learning(ts, improves, lid=None, tag=None, **extra):
    row = {"type": "attribution", "timestamp": ts, "improves": improves,
           "improves_type": "skill", "id": lid or f"L-{ts}-{improves}"}
    if tag is not None:
        row["attribution_tag"] = tag
    row.update(extra)
    return row


def change(cid, merged_at, units, pr=None, sha=None, repo="Eric-Lingren/dotfiles"):
    refs = [f"pr:{pr}"] if pr else []
    return {"schema_version": "1", "change_id": cid, "lane": "manual",
            "source_refs": refs, "target_repo": repo,
            "touched_units": units, "merged_sha": sha or ("f" * 40),
            "merged_at": merged_at}


def write_changes(d, changes):
    d.mkdir(parents=True, exist_ok=True)
    for c in changes:
        (d / f"{c['change_id']}.json").write_text(json.dumps(c))
    return d


def write_learnings(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def write_snaps(d, weeks):
    """weeks: {"2026-W45": {"tdd": 10}} -> one host snapshot file."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshots-alpha.json").write_text(json.dumps({
        wk: {"adherence": {"skills": {s: {"exp": "sonnet", "mix": {"sonnet": n}}
                                      for s, n in skills.items()}}}
        for wk, skills in weeks.items()}))
    return d


def detect(mod, learnings, runs, changes, week=WEEK):
    return mod.detect(learnings, runs, changes, week)


def test_rate_rise_over_threshold_alerts_with_before_after(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    alerts = detect(mod, learnings, runs, [])
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "drift"
    assert a["skill"] == "tdd"
    assert a["before"]["rate"] == pytest.approx(0.1)
    assert a["after"]["rate"] == pytest.approx(0.4)
    assert a["before"]["runs"] == 10 and a["after"]["runs"] == 10
    assert "tdd" in a["text"]
    assert "0.100" in a["text"] and "0.400" in a["text"]


def test_small_rise_under_threshold_does_not_alert(mod):
    runs = {"tdd": {"2026-W45": 20, "2026-W44": 20}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(2)]
    # 0.05 -> 0.10: +0.05, under the 0.10 threshold
    assert detect(mod, learnings, runs, []) == []


def test_drop_in_rate_does_not_alert(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd", lid=f"o{i}") for i in range(5)]
    assert detect(mod, learnings, runs, []) == []


def test_skill_below_ten_runs_never_alarms(mod):
    # 9 runs in W45, W44 has 100 but the window only widens when runs < 10 --
    # here it widens to 2 weeks (109 runs) so make it truly short instead.
    runs = {"tdd": {"2026-W45": 2, "2026-W44": 2, "2026-W43": 2, "2026-W42": 2}}
    learnings = [learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    assert detect(mod, learnings, runs, []) == []


def test_prior_window_under_min_runs_does_not_drift_alert(mod):
    # Target week has 10 runs (alarmable, window stays 1 week); the prior week
    # has a real rate (0/9) but only 9 runs, so the baseline is noise. A large
    # rise (0.0 -> 0.5) must not alert.
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 9}}
    learnings = [learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(5)]
    assert detect(mod, learnings, runs, []) == []


def test_verified_regression_alerts_for_skill_under_ten_runs(mod):
    runs = {"tdd": {"2026-W45": 3}}
    tag = {"kind": "regression_of", "ref": "chg-aaa"}
    learnings = [learning(W45_DAY, "tdd", lid="n1", tag=tag)]
    alerts = detect(mod, learnings, runs, [])
    assert [a["kind"] for a in alerts] == ["verified_regression"]
    assert alerts[0]["skill"] == "tdd"
    assert alerts[0]["learning_id"] == "n1"
    assert alerts[0]["ref"] == "chg-aaa"


def test_rate_drift_alarm_still_gated_for_skill_under_ten_runs(mod):
    # 4 learnings over 3 runs is a huge raw rate, but under 10 runs: no drift.
    runs = {"tdd": {"2026-W45": 3, "2026-W44": 3}}
    learnings = [learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    assert detect(mod, learnings, runs, []) == []


def test_lone_untagged_learning_on_rare_skill_does_not_alert(mod):
    runs = {"tdd": {"2026-W45": 3}}
    assert detect(mod, [learning(W45_DAY, "tdd")], runs, []) == []
    tag = {"kind": "new_defect", "ref": None}
    assert detect(mod, [learning(W45_DAY, "tdd", tag=tag)], runs, []) == []


def test_tagged_change_survives_max_suspects_cap(mod):
    runs = {"tdd": {"2026-W45": 3}}
    tag = {"kind": "regression_of", "ref": "chg-tagged"}
    learnings = [learning(W45_DAY, "tdd", lid="n1", tag=tag)]
    # 12 newer changes touching the skill push the (older) tagged one past the cap.
    changes = [change(f"chg-{i:02d}", f"2026-11-02T{i:02d}:00:00Z", ["skill:tdd"],
                      pr=100 + i) for i in range(12)]
    changes.append(change("chg-tagged", "2026-10-28T12:00:00Z", ["skill:tdd"], pr=5))
    a = detect(mod, learnings, runs, changes)[0]
    ids = [s["change_id"] for s in a["suspects"]]
    assert len(ids) == mod.MAX_SUSPECTS
    assert "chg-tagged" in ids
    assert [s["tagged"] for s in a["suspects"]].count(True) == 1
    assert ids[-1] == "chg-tagged"  # still newest first: it is the oldest
    assert "chg-tagged" in a["text"] and "[tagged by the learning]" in a["text"]


def test_cap_without_tag_still_keeps_newest_ten(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    changes = [change(f"chg-{i:02d}", f"2026-11-02T{i:02d}:00:00Z", ["skill:tdd"])
               for i in range(12)]
    ids = [s["change_id"] for s in detect(mod, learnings, runs, changes)[0]["suspects"]]
    assert ids == [f"chg-{i:02d}" for i in range(11, 1, -1)]


def test_alert_text_renders_when_before_and_after_rates_are_none(mod):
    # No snapshot runs at all for the skill: both windows have rate None.
    tag = {"kind": "regression_of", "ref": "chg-aaa"}
    learnings = [learning(W45_DAY, "tdd", lid="n1", tag=tag)]
    a = detect(mod, learnings, {}, [])[0]
    assert a["before"]["rate"] is None and a["after"]["rate"] is None
    assert a["kind"] == "verified_regression"
    t = a["text"]
    assert "VERIFIED REGRESSION: tdd learning n1" in t
    assert "before: n/a (0 learnings / 0 runs)" in t
    assert "after:  n/a (1 learnings / 0 runs)" in t


def test_suspects_newest_first_with_pr_links(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    changes = [
        change("chg-old", "2026-10-27T09:00:00-07:00", ["skill:tdd"], pr=70),
        change("chg-new", "2026-11-02T16:15:17-08:00", ["skill:tdd"], pr=71),
        change("chg-mid", "2026-10-28T12:00:00Z", ["skill:tdd", "script:x.py"],
               pr=72),
        change("chg-other", "2026-11-01T12:00:00Z", ["skill:debug"], pr=73),
    ]
    a = detect(mod, learnings, runs, changes)[0]
    ids = [s["change_id"] for s in a["suspects"]]
    assert ids == ["chg-new", "chg-mid", "chg-old"]
    assert a["suspects"][0]["url"] == \
        "https://github.com/Eric-Lingren/dotfiles/pull/71"
    assert a["suspects"][1]["url"].endswith("/pull/72")
    # text lists them in the same order, with links
    t = a["text"]
    assert t.index("chg-new") < t.index("chg-mid") < t.index("chg-old")
    assert "https://github.com/Eric-Lingren/dotfiles/pull/71" in t
    assert "chg-other" not in t


def test_suspect_without_pr_falls_back_to_commit_url(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    changes = [change("chg-nopr", "2026-11-02T12:00:00Z", ["skill:tdd"],
                      sha="ab" * 20)]
    s = detect(mod, learnings, runs, changes)[0]["suspects"][0]
    assert s["url"] == f"https://github.com/Eric-Lingren/dotfiles/commit/{'ab' * 20}"


def test_suspect_outside_windows_is_not_listed(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    learnings = [learning(W44_DAY, "tdd")] + [
        learning(W45_DAY, "tdd", lid=f"n{i}") for i in range(4)]
    changes = [change("chg-ancient", "2026-08-01T12:00:00Z", ["skill:tdd"], pr=1),
               change("chg-future", "2026-11-20T12:00:00Z", ["skill:tdd"], pr=2)]
    assert detect(mod, learnings, runs, changes)[0]["suspects"] == []


def test_verified_regression_alerts_even_when_rate_flat(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    tag = {"kind": "regression_of", "ref": "chg-bad"}
    learnings = [learning(W44_DAY, "tdd", lid="o1"),
                 learning(W45_DAY, "tdd", lid="n1", tag=tag)]
    changes = [change("chg-bad", "2026-10-30T12:00:00Z", ["skill:tdd"], pr=88),
               change("chg-newer", "2026-11-02T12:00:00Z", ["skill:tdd"], pr=89)]
    alerts = detect(mod, learnings, runs, changes)
    assert [a["kind"] for a in alerts] == ["verified_regression"]
    a = alerts[0]
    assert a["skill"] == "tdd"
    assert a["before"]["rate"] == pytest.approx(0.1)
    assert a["after"]["rate"] == pytest.approx(0.1)
    assert a["learning_id"] == "n1"
    assert a["ref"] == "chg-bad"
    ids = [s["change_id"] for s in a["suspects"]]
    assert ids == ["chg-newer", "chg-bad"]  # newest first
    assert [s["tagged"] for s in a["suspects"]] == [False, True]
    assert a["suspects"][1]["url"].endswith("/pull/88")


def test_tagged_change_listed_even_if_not_touching_skill_unit(mod):
    runs = {"tdd": {"2026-W45": 10}}
    tag = {"kind": "regression_of", "ref": "chg-bad"}
    learnings = [learning(W45_DAY, "tdd", tag=tag)]
    changes = [change("chg-bad", "2026-10-30T12:00:00Z", ["script:y.py"], pr=5)]
    a = detect(mod, learnings, runs, changes)[0]
    assert [s["change_id"] for s in a["suspects"]] == ["chg-bad"]


def test_ungrounded_downgraded_tag_does_not_alert(mod):
    # log-learning downgrades an ungrounded regression_of tag to new_defect/null.
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    tag = {"kind": "new_defect", "ref": None}
    learnings = [learning(W44_DAY, "tdd", lid="o1"),
                 learning(W45_DAY, "tdd", lid="n1", tag=tag)]
    changes = [change("chg-bad", "2026-10-30T12:00:00Z", ["skill:tdd"], pr=88)]
    assert detect(mod, learnings, runs, changes) == []


def test_regression_tag_with_null_ref_does_not_alert(mod):
    runs = {"tdd": {"2026-W45": 10}}
    learnings = [learning(W45_DAY, "tdd",
                          tag={"kind": "regression_of", "ref": None})]
    assert detect(mod, learnings, runs, []) == []


def test_regression_from_other_week_does_not_alert(mod):
    runs = {"tdd": {"2026-W45": 10, "2026-W44": 10}}
    tag = {"kind": "regression_of", "ref": "chg-bad"}
    learnings = [learning(W44_DAY, "tdd", tag=tag)]
    assert detect(mod, learnings, runs, []) == []


# --- CLI --------------------------------------------------------------------

def stub_notifier(tmp_path):
    log = tmp_path / "notify.log"
    stub = tmp_path / "notify-stub.sh"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{log}"\n')
    stub.chmod(0o755)
    return stub, log


def drift_fixture(tmp_path):
    reports = write_snaps(tmp_path / "reports",
                          {"2026-W45": {"tdd": 10}, "2026-W44": {"tdd": 10}})
    lpath = write_learnings(tmp_path / "learnings.jsonl",
                            [learning(W44_DAY, "tdd")] + [
                                learning(W45_DAY, "tdd", lid=f"n{i}")
                                for i in range(4)])
    changes = write_changes(tmp_path / "changes", [
        change("chg-new", "2026-11-02T12:00:00Z", ["skill:tdd"], pr=71)])
    return reports, lpath, changes


def run_cli(tmp_path, *extra, env_extra=None):
    reports, lpath, changes = drift_fixture(tmp_path)
    stub, log = stub_notifier(tmp_path)
    env = dict(os.environ, DRIFT_ALERT_NOTIFY_CMD=str(stub))
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--week", WEEK,
         "--report-dir", str(reports), "--learnings", str(lpath),
         "--changes-dir", str(changes), *extra],
        env=env, capture_output=True, text=True)
    return proc, log


def test_cli_prints_alert_and_delivers_via_notify(tmp_path):
    proc, log = run_cli(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "tdd" in proc.stdout and "chg-new" in proc.stdout
    assert "/pull/71" in proc.stdout
    sent = log.read_text()
    assert "--title" in sent and "--message" in sent
    assert "tdd" in sent


def test_cli_no_alerts_prints_nothing_and_sends_nothing(tmp_path):
    reports = write_snaps(tmp_path / "reports", {"2026-W45": {"tdd": 10}})
    lpath = write_learnings(tmp_path / "l.jsonl", [])
    changes = tmp_path / "changes"
    stub, log = stub_notifier(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--week", WEEK, "--report-dir",
         str(reports), "--learnings", str(lpath), "--changes-dir", str(changes)],
        env=dict(os.environ, DRIFT_ALERT_NOTIFY_CMD=str(stub)),
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
    assert not log.exists()


def test_cli_no_notify_flag_skips_delivery(tmp_path):
    proc, log = run_cli(tmp_path, "--no-notify")
    assert proc.returncode == 0
    assert "tdd" in proc.stdout
    assert not log.exists()


def test_cli_notify_failure_does_not_fail_detector(tmp_path):
    proc, _ = run_cli(tmp_path, env_extra={
        "DRIFT_ALERT_NOTIFY_CMD": str(tmp_path / "missing-notifier")})
    assert proc.returncode == 0
    assert "tdd" in proc.stdout


def test_cli_json_output(tmp_path):
    proc, _ = run_cli(tmp_path, "--json", "--no-notify")
    data = json.loads(proc.stdout)
    assert data[0]["skill"] == "tdd"
    assert data[0]["suspects"][0]["change_id"] == "chg-new"


# --- default --week (last completed ISO week) -------------------------------

class FrozenDatetime:
    """Stand-in for the datetime module with date.today() frozen."""

    def __init__(self, today):
        import datetime as real
        self._real = real

        class _Date(real.date):
            @classmethod
            def today(cls):
                return today

        self.date = _Date

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.parametrize("today,expected", [
    ("2026-11-09", "2026-W45"),  # Monday: W45 just completed
    ("2026-11-08", "2026-W44"),  # Sunday: W45 still in progress
    ("2026-11-03", "2026-W44"),  # mid-week
    ("2027-01-04", "2026-W53"),  # year boundary (2026 has 53 ISO weeks)
])
def test_last_completed_week_across_monday_sunday_boundary(
        mod, monkeypatch, today, expected):
    import datetime
    monkeypatch.setattr(mod, "datetime",
                        FrozenDatetime(datetime.date.fromisoformat(today)))
    assert mod.last_completed_week() == expected


def test_cli_without_week_uses_last_completed_week(
        mod, monkeypatch, tmp_path, capsys):
    import datetime
    # Monday 2026-11-09 -> default week is 2026-W45, matching drift_fixture.
    monkeypatch.setattr(mod, "datetime",
                        FrozenDatetime(datetime.date(2026, 11, 9)))
    reports, lpath, changes = drift_fixture(tmp_path)
    mod.main(["--report-dir", str(reports), "--learnings", str(lpath),
              "--changes-dir", str(changes), "--json", "--no-notify"])
    data = json.loads(capsys.readouterr().out)
    assert data and data[0]["skill"] == "tdd"
    assert data[0]["suspects"][0]["change_id"] == "chg-new"


def test_cli_without_week_on_sunday_targets_prior_week(
        mod, monkeypatch, tmp_path, capsys):
    import datetime
    # Sunday 2026-11-08 -> default week is 2026-W44, which has no drift.
    monkeypatch.setattr(mod, "datetime",
                        FrozenDatetime(datetime.date(2026, 11, 8)))
    reports, lpath, changes = drift_fixture(tmp_path)
    mod.main(["--report-dir", str(reports), "--learnings", str(lpath),
              "--changes-dir", str(changes), "--json", "--no-notify"])
    assert json.loads(capsys.readouterr().out) == []


# --- no git writes / no PRs -------------------------------------------------

def git_env():
    return dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
                GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
                GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=git_env(), check=True,
                          capture_output=True, text=True).stdout


def test_detector_does_no_git_writes_and_opens_no_prs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    # Put every input inside the repo so a careless commit/push would hit it.
    reports, lpath, changes = None, None, None
    reports = write_snaps(repo / "reports",
                          {"2026-W45": {"tdd": 10}, "2026-W44": {"tdd": 10}})
    lpath = write_learnings(repo / "learnings.jsonl",
                            [learning(W44_DAY, "tdd")] + [
                                learning(W45_DAY, "tdd", lid=f"n{i}")
                                for i in range(4)])
    changes = write_changes(repo / "changes", [
        change("chg-new", "2026-11-02T12:00:00Z", ["skill:tdd"], pr=71)])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")

    head = git(repo, "rev-parse", "HEAD")
    refs = git(repo, "for-each-ref")
    status = git(repo, "status", "--porcelain")
    reflog = git(repo, "reflog")

    # git and gh shims first on PATH: log every call, and fail loudly on writes.
    shims = tmp_path / "shims"
    shims.mkdir()
    calls = tmp_path / "calls.log"
    real_git = subprocess.run(["which", "git"], capture_output=True,
                              text=True).stdout.strip()
    (shims / "git").write_text(
        f'#!/bin/bash\necho "git $*" >> "{calls}"\nexec "{real_git}" "$@"\n')
    (shims / "gh").write_text(f'#!/bin/bash\necho "gh $*" >> "{calls}"\nexit 1\n')
    for n in ("git", "gh"):
        (shims / n).chmod(0o755)
    stub, nlog = stub_notifier(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--week", WEEK, "--report-dir",
         str(reports), "--learnings", str(lpath), "--changes-dir", str(changes)],
        cwd=repo, capture_output=True, text=True,
        env=dict(git_env(), PATH=f"{shims}:{os.environ['PATH']}",
                 DRIFT_ALERT_NOTIFY_CMD=str(stub)))
    assert proc.returncode == 0, proc.stderr
    assert "chg-new" in proc.stdout  # it really ran and alerted

    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "for-each-ref") == refs
    assert git(repo, "status", "--porcelain") == status
    assert git(repo, "reflog") == reflog
    logged = calls.read_text() if calls.exists() else ""
    assert "gh " not in logged                      # no gh at all, so no pr create
    for verb in ("commit", "push", "checkout", "branch", "tag", "reset",
                 "merge", "rebase", "add", "revert", "cherry-pick", "bisect"):
        assert f"git {verb}" not in logged


def test_detector_source_has_no_git_or_pr_commands():
    src = SCRIPT.read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert '"git"' not in code and "'git'" not in code
    assert '"gh"' not in code and "'gh'" not in code


# --- weekly job wiring ------------------------------------------------------

def run_weekly(tmp_path, drift_body, bench_body=None):
    out = tmp_path / "reports"
    stub_bench = tmp_path / "bench.py"
    stub_bench.write_text(
        bench_body
        or "print('bench ' + ' '.join(__import__('sys').argv[1:]))\n")
    writer = tmp_path / "writer.sh"
    writer.write_text("#!/bin/bash\necho 'committed 0 record(s)'\n")
    writer.chmod(0o755)
    drift = tmp_path / "drift.sh"
    drift.write_text("#!/bin/bash\n" + drift_body)
    drift.chmod(0o755)
    env = dict(os.environ, CC_USAGE_REPORT_DIR=str(out),
               CC_USAGE_BENCHMARK=str(stub_bench),
               CC_CHANGE_RECORDS_WRITER=str(writer), CC_DRIFT_ALERT=str(drift))
    proc = subprocess.run(["bash", str(WEEKLY)], env=env, capture_output=True,
                          text=True)
    return proc, out


def test_weekly_job_includes_alert_text_in_digest_and_report(tmp_path):
    proc, out = run_weekly(tmp_path, "echo 'DRIFT ALERT: tdd 0.100 -> 0.400'\n")
    assert proc.returncode == 0, proc.stderr
    digest = (out / "digest-latest.txt").read_text()
    assert "DRIFT ALERT: tdd 0.100 -> 0.400" in digest
    report = next(out.glob("usage-*.txt")).read_text()
    assert "DRIFT ALERTS" in report
    assert "DRIFT ALERT: tdd 0.100 -> 0.400" in report


def test_weekly_job_quiet_when_no_alerts(tmp_path):
    proc, out = run_weekly(tmp_path, "exit 0\n")
    assert proc.returncode == 0, proc.stderr
    digest = (out / "digest-latest.txt").read_text()
    assert "DRIFT ALERT" not in digest
    report = next(out.glob("usage-*.txt")).read_text()
    assert "DRIFT ALERTS" in report


def test_weekly_job_survives_detector_failure(tmp_path):
    proc, out = run_weekly(tmp_path, "echo boom >&2\nexit 9\n")
    assert proc.returncode == 0, proc.stderr
    report = next(out.glob("usage-*.txt")).read_text()
    assert "drift detector failed" in report
    assert "WEEKLY TREND" in report
    assert "drift detector failed" not in (out / "digest-latest.txt").read_text()


def test_weekly_job_runs_drift_after_digest_refreshes_snapshots(tmp_path):
    # --digest is the only writer of the snapshot the detector reads, so the
    # detector must see the marker the digest step leaves behind.
    marker = tmp_path / "digest-ran"
    bench = (
        "import sys, pathlib\n"
        f"if '--digest' in sys.argv: pathlib.Path({str(marker)!r}).write_text('x')\n"
        "print('bench ' + ' '.join(sys.argv[1:]))\n")
    drift = f'[ -e "{marker}" ] && echo "DRIFT ALERT: saw fresh snapshot" || echo "DRIFT ALERT: stale"\n'
    proc, out = run_weekly(tmp_path, drift, bench_body=bench)
    assert proc.returncode == 0, proc.stderr
    digest = (out / "digest-latest.txt").read_text()
    assert "saw fresh snapshot" in digest
    assert "stale" not in digest


def test_weekly_job_keeps_detector_stderr_out_of_digest(tmp_path):
    proc, out = run_weekly(
        tmp_path, "echo 'Warning: noisy python deprecation' >&2\nexit 0\n")
    assert proc.returncode == 0, proc.stderr
    digest = (out / "digest-latest.txt").read_text()
    assert "noisy python deprecation" not in digest
    report = next(out.glob("usage-*.txt")).read_text()
    assert "noisy python deprecation" not in report
    assert "DRIFT ALERTS" in report
