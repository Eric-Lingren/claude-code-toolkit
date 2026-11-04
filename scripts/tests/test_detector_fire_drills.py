"""Fire drills for the attribution and alert pipeline (fixtures only).

The detector chain is: write-change-records.py (change records) -> log-learning.py
(tag-attribution.py tags the learning, the judge verifies a regression_of tag) ->
drift-alert.py (alerts on a verified regression or a correction-rate rise, over
correction-rate.py). These drills run the real scripts end to end against a
throwaway git repo and throwaway learnings/report directories:

  1. regression in the changed section   -> regression_of tag AND an alert
  2. a learning in a different path      -> new_defect, no regression alert
  3. recurrence after a fix              -> recurrence_of
  4. a rare skill with one learning      -> a lone verified regression_of alerts;
                                            a lone learning that is not one does not
  5. a busy week with a normal rate      -> no alarm

Nothing live is touched: the skills are fixture files in a temp git repo, the learnings
file, change records and usage snapshots are temp files, the judge is a stub
(LOG_LEARNING_JUDGE_CMD), the notifier is a stub (DRIFT_ALERT_NOTIFY_CMD), and `claude`
and `gh` are poison shims on PATH that fail the test if anything calls them. The real
unified-learnings.jsonl and the real seed-bank are never read or written.

THE RULE: every real false alarm or missed regression becomes a new fixture here.
When the detectors cry wolf or stay silent on a real regression, add a drill that
reproduces it (a new test using the `lab` fixture) BEFORE changing the detector, watch
it fail, then fix. See claude-code-shared/resources/detector-fire-drills.md.
"""

import hashlib
import json
import shutil
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

SHARED = pathlib.Path(__file__).resolve().parents[2]
DOTFILES = SHARED.parent
SCRIPTS = SHARED / "scripts"
WRITE_RECORDS = SCRIPTS / "attribution" / "write-change-records.py"
LOG_LEARNING = SCRIPTS / "learning" / "log-learning.py"
DRIFT_ALERT = SCRIPTS / "usage" / "drift-alert.py"
CORRECTION_RATE = SCRIPTS / "usage" / "correction-rate.py"
RUN_ALL = DOTFILES / "run-all-tests.sh"
DOC = SHARED / "resources" / "detector-fire-drills.md"
THIS_FILE = pathlib.Path(__file__).resolve()

SKILL_MD = "# Debug\n\n## Reproduce\n\nstep one\n\n## Instrument\n\nold logging advice\n"
REFERENCES_MD = "# References\n\n## Notes\n\nold note\n"
NICHE_MD = "# Niche\n\n## Steps\n\nold step\n"

PR_OLD_FIX = 31  # touched debug "## Reproduce", merged 90 days ago (outside the 60-day tag window)
PR_RECENT = 42   # touched debug "## Instrument", merged 3 days ago
PR_NICHE = 43    # touched niche "## Steps", merged 3 days ago

BASE_LEARNING = {
    "type": "attribution",
    "reported_by": "debug",
    "improves": "debug",
    "improves_type": "skill",
    "cause": "general_best_practice",
    "cause_label": None,
    "problem": "Skipped the repro step.",
    "why_missed": "Step was optional.",
    "lesson": "Always repro first.",
    "fix": "Make repro mandatory.",
    "evidence": [{"source": "artifact", "ref": "docs/x.json", "quote": "q"}],
    "confidence": "candidate",
}


def at(path, section):
    return {"escape_point": {"path": path, "section": section}}


IN_CHANGED_SECTION = at("skills/debug/SKILL.md", "## Instrument")
DIFFERENT_PATH = at("skills/debug/references.md", "## Notes")
REPRODUCE_SECTION = at("skills/debug/SKILL.md", "## Reproduce")
NICHE_STEPS = {"improves": "niche", **at("skills/niche/SKILL.md", "## Steps")}

JUDGE_STUB = """#!/usr/bin/env python3
import json, os, sys
sys.stdin.read()
with open(os.environ["DRILL_JUDGE_CALLS"], "a") as fh:
    fh.write("call\\n")
print(json.dumps({"verdict": "pass", "confidence": "confirmed", "reason": "stub: section edited"}))
"""

NOTIFY_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$DRILL_NOTIFY_LOG"
"""

POISON_SHIM = """#!/bin/bash
echo "$(basename "$0") $*" >> "$DRILL_POISON_LOG"
exit 99
"""


# Benign gh for the fixture build only: logs the call, returns an empty result list.
BUILD_GH_STUB = """#!/bin/bash
echo "gh $*" >> "${DRILL_BUILD_GH_LOG:-/dev/null}"
echo "[]"
"""


def _clean_env():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("LOG_LEARNING_", "DRIFT_ALERT_", "GIT_")):
            del env[key]
    return env


def _git_env(when=None):
    env = _clean_env()
    env.update(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    return env


def _git(repo, *args, when=None):
    res = subprocess.run(["git", *args], cwd=repo, env=_git_env(when), check=True,
                         capture_output=True, text=True)
    return res.stdout.strip()


def _iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


class Lab:
    """A throwaway toolkit: git repo with skills, change records, learnings, usage snapshots."""

    def __init__(self, root, template=None):
        self.root = root
        self.repo = root / "dotfiles"
        self.toolkit = self.repo / "claude-code-shared"
        self.changes_dir = self.toolkit.parent / "attribution" / "change-records"
        self.dest = root / "learnings-out"
        self.reports = root / "usage-reports"
        self.learnings_file = self.dest / "unified-learnings.jsonl"
        self.judge_calls = root / "judge-calls.log"
        self.notify_log = root / "notify.log"
        self.poison_log = root / "poison.log"
        self.now = datetime.now(timezone.utc)
        self._build_bin()
        if template is None:
            self._build_repo()
        else:  # same fixture repo, copied: building it costs seconds of git work
            shutil.copytree(template.repo, self.repo, symlinks=True)
            self.now = template.now
            self.records = template.records

    # -- setup -------------------------------------------------------------
    def _build_bin(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("claude", "gh"):
            (bin_dir / name).write_text(POISON_SHIM)
            (bin_dir / name).chmod(0o755)
        self.judge = self.root / "judge-stub.py"
        self.judge.write_text(JUDGE_STUB)
        self.notifier = self.root / "notify-stub.sh"
        self.notifier.write_text(NOTIFY_STUB)
        self.notifier.chmod(0o755)
        self.bin_dir = bin_dir

    def _write(self, rel, text):
        p = self.toolkit / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _merge_pr(self, branch, pr, files, days_ago):
        when = (self.now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        _git(self.repo, "checkout", "-q", "-b", branch, "main")
        for rel, text in files.items():
            self._write(rel, text)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", f"work on {branch}", when=when)
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "--no-ff", "-q", branch, "-m",
             f"Merge pull request #{pr} from someone/{branch}", when=when)

    def _build_repo(self):
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        self._write("skills/debug/SKILL.md", SKILL_MD)
        self._write("skills/debug/references.md", REFERENCES_MD)
        self._write("skills/niche/SKILL.md", NICHE_MD)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "base",
             when=(self.now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S+00:00"))
        self._merge_pr("fix/old-repro", PR_OLD_FIX,
                       {"skills/debug/SKILL.md": SKILL_MD.replace("step one", "step one, fixed")}, 90)
        self._merge_pr("feat/instrument", PR_RECENT,
                       {"skills/debug/SKILL.md": SKILL_MD.replace("step one", "step one, fixed")
                        .replace("old logging advice", "new logging advice")}, 3)
        self._merge_pr("feat/niche", PR_NICHE,
                       {"skills/niche/SKILL.md": NICHE_MD.replace("old step", "new step")}, 3)
        build_bin = self.root / "build-bin"
        build_bin.mkdir()
        (build_bin / "gh").write_text(BUILD_GH_STUB)
        (build_bin / "gh").chmod(0o755)
        build_env = _git_env()
        build_env["PATH"] = f"{build_bin}:{build_env['PATH']}"
        build_env["DRILL_BUILD_GH_LOG"] = str(self.root / "build-gh.log")
        proc = subprocess.run(
            [sys.executable, str(WRITE_RECORDS), "--repo", str(self.repo), "--ref", "main",
             "--out-dir", str(self.changes_dir), "--since", "120 days ago",
             "--target-repo", "Eric-Lingren/dotfiles"],
            env=build_env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        self.records = {}
        for f in sorted(self.changes_dir.glob("chg-*.json")):
            rec = json.loads(f.read_text())
            pr = next(r[3:] for r in rec["source_refs"] if r.startswith("pr:"))
            self.records[int(pr)] = rec
        assert set(self.records) == {PR_OLD_FIX, PR_RECENT, PR_NICHE}

    # -- running the real scripts -----------------------------------------
    def _env(self):
        env = _clean_env()
        env.update(
            PATH=f"{self.bin_dir}:{env['PATH']}",
            LOG_LEARNING_DEST=str(self.dest),
            LOG_LEARNING_DOTFILES_DIR=str(self.repo),
            LOG_LEARNING_JUDGE_CMD=f"{sys.executable} {self.judge}",
            DRILL_JUDGE_CALLS=str(self.judge_calls),
            DRIFT_ALERT_NOTIFY_CMD=str(self.notifier),
            DRILL_NOTIFY_LOG=str(self.notify_log),
            DRILL_POISON_LOG=str(self.poison_log),
        )
        return env

    def log(self, **overrides):
        """Run log-learning.py on BASE_LEARNING + overrides; return the row it wrote."""
        payload = {**BASE_LEARNING, **overrides}
        proc = subprocess.run([sys.executable, str(LOG_LEARNING)], input=json.dumps(payload),
                              env=self._env(), capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return json.loads(self.learnings_file.read_text().splitlines()[-1])

    def seed_rows(self, rows):
        """Append already-written rows (an applied fix, last week's history) to the learnings file."""
        self.dest.mkdir(exist_ok=True)
        with open(self.learnings_file, "a") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def history_row(self, idx, days_ago=7, **extra):
        ts = (self.now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"type": "attribution", "improves": "debug", "improves_type": "skill",
                "id": f"hist-{idx}", "timestamp": ts, **extra}

    def applied_row(self, row_id, **escape):
        return {**BASE_LEARNING, **escape, "schema_version": "2", "id": row_id,
                "timestamp": (self.now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "applied"}

    @property
    def week(self):
        return _iso_week(self.now)

    @property
    def prev_week(self):
        return _iso_week(self.now - timedelta(days=7))

    def snapshots(self, runs):
        """runs: {skill: (runs_this_week, runs_prev_week)} -> usage snapshot file."""
        self.reports.mkdir(exist_ok=True)

        def week(i):
            return {"adherence": {"skills": {
                s: {"exp": "sonnet", "mix": {"sonnet": n[i]}} for s, n in runs.items()}}}
        (self.reports / "snapshots-drill.json").write_text(
            json.dumps({self.week: week(0), self.prev_week: week(1)}))

    def alerts(self):
        """Run drift-alert.py for the current week; return (alerts, stdout)."""
        proc = subprocess.run(
            [sys.executable, str(DRIFT_ALERT), "--week", self.week, "--report-dir", str(self.reports),
             "--learnings", str(self.learnings_file), "--changes-dir", str(self.changes_dir),
             "--json"],
            env=self._env(), capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout), proc.stdout

    def correction_rate(self):
        proc = subprocess.run(
            [sys.executable, str(CORRECTION_RATE), "--week", self.week, "--report-dir",
             str(self.reports), "--learnings", str(self.learnings_file)],
            env=self._env(), capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)["skills"]

    @property
    def judge_call_count(self):
        return len(self.judge_calls.read_text().splitlines()) if self.judge_calls.exists() else 0

    @property
    def notifications(self):
        return self.notify_log.read_text() if self.notify_log.exists() else ""


@pytest.fixture(scope="module")
def lab_template(tmp_path_factory):
    return Lab(tmp_path_factory.mktemp("lab-template"))


@pytest.fixture
def lab(tmp_path, lab_template):
    lab = Lab(tmp_path, lab_template)
    yield lab
    # Poison shims: nothing in the pipeline may reach a real model or GitHub.
    assert not lab.poison_log.exists(), lab.poison_log.read_text()


# -- Drill 1: regression in the changed section -----------------------------------------------

class TestDrillRegressionInChangedSection:
    def test_yields_regression_of_and_an_alert(self, lab):
        lab.snapshots({"debug": (12, 12)})
        lab.seed_rows([lab.history_row(1)])  # last week: 1 learning / 12 runs
        row = lab.log(**IN_CHANGED_SECTION)  # this week: 1 learning / 12 runs, flat rate

        recent = lab.records[PR_RECENT]
        assert row["attribution_tag"] == {"kind": "regression_of", "ref": recent["change_id"]}
        assert lab.judge_call_count == 1  # the tag was verified against the real diff

        alerts, _ = lab.alerts()
        assert [a["kind"] for a in alerts] == ["verified_regression"]  # flat rate: tag alone alarms
        alert = alerts[0]
        assert alert["skill"] == "debug"
        assert alert["ref"] == recent["change_id"]
        assert alert["learning_id"] == row["id"]
        top = alert["suspects"][0]
        assert top["change_id"] == recent["change_id"] and top["tagged"] is True
        assert top["url"] == "https://github.com/Eric-Lingren/dotfiles/pull/42"
        assert recent["change_id"] in lab.notifications  # delivered through the notifier stub

    def test_judge_rejection_removes_the_alarm(self, lab):
        # Control: the same learning with an ungrounded tag must not alarm (log-learning
        # downgrades it to new_defect), so the alert above really is the verified-tag path.
        lab.judge.write_text(JUDGE_STUB.replace('"pass"', '"rejected"')
                             .replace('"confirmed"', '"candidate"'))
        lab.snapshots({"debug": (12, 12)})
        lab.seed_rows([lab.history_row(1)])
        row = lab.log(**IN_CHANGED_SECTION)
        assert row["attribution_tag"] == {"kind": "new_defect", "ref": None}
        assert lab.alerts()[0] == []


# -- Drill 2: a learning in a different path --------------------------------------------------

class TestDrillDifferentPath:
    def test_yields_new_defect_and_no_regression_alert(self, lab):
        lab.snapshots({"debug": (12, 12)})
        lab.seed_rows([lab.history_row(1)])
        row = lab.log(**DIFFERENT_PATH)  # the recent change touched SKILL.md, not references.md
        assert row["attribution_tag"] == {"kind": "new_defect", "ref": None}
        assert lab.judge_call_count == 0  # no regression claim, nothing to verify
        alerts, stdout = lab.alerts()
        assert alerts == [] and json.loads(stdout) == []
        assert lab.notifications == ""


# -- Drill 3: recurrence after a fix ----------------------------------------------------------

class TestDrillRecurrenceAfterFix:
    def test_yields_recurrence_of(self, lab):
        # The defect was fixed (PR 31, 90 days ago, so outside the regression window) and its
        # learning applied. It now happens again in the same place.
        applied = lab.applied_row("11111111-1111-4111-8111-111111111111",
                                  **REPRODUCE_SECTION)
        lab.seed_rows([applied])
        assert lab.records[PR_OLD_FIX]["sections"][0]["section"] == "## Reproduce"
        row = lab.log(**REPRODUCE_SECTION)
        assert row["attribution_tag"] == {"kind": "recurrence_of", "ref": applied["id"]}
        assert lab.judge_call_count == 0

    def test_a_recurrence_is_not_a_verified_regression_alert(self, lab):
        lab.snapshots({"debug": (12, 12)})
        lab.seed_rows([lab.applied_row("11111111-1111-4111-8111-111111111111",
                                       **REPRODUCE_SECTION), lab.history_row(1)])
        lab.log(**REPRODUCE_SECTION)
        assert lab.alerts()[0] == []


# -- Drill 4: a rare skill with one learning --------------------------------------------------

class TestDrillRareSkill:
    def test_a_lone_verified_regression_on_a_rare_skill_alerts(self, lab):
        lab.snapshots({"niche": (2, 1), "debug": (12, 12)})  # 3 runs in 2 weeks: far under the gate
        lab.seed_rows([lab.history_row(1)])
        row = lab.log(**NICHE_STEPS)
        # The single learning is a fully verified regression_of (checked against the real
        # diff), so it alerts however few runs the skill has.
        assert row["attribution_tag"] == {
            "kind": "regression_of", "ref": lab.records[PR_NICHE]["change_id"]}
        niche = lab.correction_rate()["niche"]
        assert niche["alarmable"] is False  # the rate-drift gate stays closed
        alerts, _ = lab.alerts()
        assert [(a["kind"], a["skill"]) for a in alerts] == [("verified_regression", "niche")]
        assert alerts[0]["suspects"][0]["change_id"] == lab.records[PR_NICHE]["change_id"]
        assert alerts[0]["suspects"][0]["tagged"] is True
        assert lab.records[PR_NICHE]["change_id"] in lab.notifications

    def test_a_lone_learning_that_is_not_a_verified_regression_does_not_alarm(self, lab):
        # Same rare skill, same single learning, but the tag is not a verified regression_of
        # (the judge rejects it, so log-learning downgrades it to new_defect).
        lab.judge.write_text(JUDGE_STUB.replace('"pass"', '"rejected"')
                             .replace('"confirmed"', '"candidate"'))
        lab.snapshots({"niche": (2, 1), "debug": (12, 12)})
        lab.seed_rows([lab.history_row(1)])
        row = lab.log(**NICHE_STEPS)
        assert row["attribution_tag"] == {"kind": "new_defect", "ref": None}
        assert lab.correction_rate()["niche"]["alarmable"] is False
        alerts, _ = lab.alerts()
        assert alerts == []
        assert lab.notifications == ""

    def test_one_untagged_learning_on_a_rare_skill_does_not_alarm(self, lab):
        lab.snapshots({"niche": (2, 1)})
        lab.seed_rows([{"type": "attribution", "improves": "niche", "improves_type": "skill",
                        "id": "plain-1", "timestamp": lab.now.strftime("%Y-%m-%dT%H:%M:%SZ")}])
        assert lab.correction_rate()["niche"]["alarmable"] is False
        assert lab.alerts()[0] == []
        assert lab.notifications == ""


# -- Drill 5: a busy week with a normal rate --------------------------------------------------

class TestDrillBusyWeekNormalRate:
    def test_many_runs_and_learnings_at_the_usual_rate_do_not_alarm(self, lab):
        lab.snapshots({"debug": (50, 50)})
        lab.seed_rows([lab.history_row(i) for i in range(5)])  # last week: 5 / 50 = 0.10
        lab.seed_rows([lab.applied_row("22222222-2222-4222-8222-222222222222",
                                       **REPRODUCE_SECTION)])
        for _ in range(4):
            assert lab.log(**DIFFERENT_PATH)["attribution_tag"]["kind"] == "new_defect"
        assert lab.log(**REPRODUCE_SECTION)["attribution_tag"]["kind"] == "recurrence_of"
        debug = lab.correction_rate()["debug"]  # this week: 5 / 50 = 0.10
        assert debug["alarmable"] is True
        assert debug["rate"] == pytest.approx(0.10)
        alerts, _ = lab.alerts()
        assert alerts == []
        assert lab.notifications == ""

    def test_control_a_real_spike_on_the_same_fixtures_does_alarm(self, lab):
        # Without this, a detector that never alarms would pass the drill above.
        lab.snapshots({"debug": (50, 50)})
        lab.seed_rows([lab.history_row(i) for i in range(5)])
        for _ in range(12):  # 12 / 50 = 0.24, up 0.14
            lab.log(**DIFFERENT_PATH)
        alerts, _ = lab.alerts()
        assert [a["kind"] for a in alerts] == ["drift"]
        assert alerts[0]["skill"] == "debug"
        assert alerts[0]["before"]["rate"] == pytest.approx(0.10)
        assert alerts[0]["after"]["rate"] == pytest.approx(0.24)
        assert alerts[0]["suspects"][0]["change_id"] == lab.records[PR_RECENT]["change_id"]


# -- Drill 6: fixtures only, wired into run-all-tests.sh --------------------------------------

def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class TestDrillsAreHermetic:
    def test_a_full_drill_touches_no_live_skill_or_real_learning_row(self, lab):
        real_learnings = SHARED / "learnings" / "unified-learnings.jsonl"
        real_changes = DOTFILES / "attribution" / "change-records"
        before = (_sha(real_learnings),
                  sorted(p.name for p in real_changes.glob("*")) if real_changes.is_dir() else None)
        lab.snapshots({"debug": (12, 12)})
        lab.seed_rows([lab.history_row(1)])
        row = lab.log(**IN_CHANGED_SECTION)
        lab.alerts()
        after = (_sha(real_learnings),
                 sorted(p.name for p in real_changes.glob("*")) if real_changes.is_dir() else None)
        assert before == after
        # Everything the drill read or wrote lives under the temp root.
        for p in (lab.repo, lab.changes_dir, lab.learnings_file, lab.reports):
            assert str(lab.root) in str(p.resolve())
        assert lab.learnings_file.read_text().count(row["id"]) == 1
        assert os.path.realpath(lab.repo) != os.path.realpath(DOTFILES)
        # The judge and notifier are the stubs, never `claude` or a real banner.
        env = lab._env()
        assert env["LOG_LEARNING_JUDGE_CMD"].endswith("judge-stub.py")
        assert env["DRIFT_ALERT_NOTIFY_CMD"].endswith("notify-stub.sh")

    def test_poison_shims_catch_a_real_model_or_gh_call(self, lab):
        subprocess.run(["claude", "-p", "x"], env=lab._env(), capture_output=True)
        subprocess.run(["gh", "pr", "list"], env=lab._env(), capture_output=True)
        calls = lab.poison_log.read_text()
        assert "claude -p x" in calls and "gh pr list" in calls
        lab.poison_log.unlink()  # expected here; the fixture teardown must see a clean slate

    @pytest.mark.skipif(os.environ.get("FIRE_DRILL_NESTED") == "1",
                        reason="already running inside run-all-tests.sh")
    def test_drill_file_is_picked_up_by_run_all_tests(self, tmp_path):
        tree = tmp_path / "tree"
        tests_dir = tree / "claude-code-shared" / "scripts" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / THIS_FILE.name).symlink_to(THIS_FILE)  # path-resolving imports still work
        env = _clean_env()
        env.update(FIRE_DRILL_NESTED="1", RUN_ALL_TESTS_ROOT=str(tree))
        proc = subprocess.run(["bash", str(RUN_ALL)], env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert f"PASS: claude-code-shared/scripts/tests/{THIS_FILE.name}" in proc.stdout

    def test_the_rule_is_documented(self):
        text = DOC.read_text()
        assert "false alarm" in text and "missed regression" in text
        assert "new fixture" in text
        assert THIS_FILE.name in text
        assert "false alarm" in (THIS_FILE.read_text())
