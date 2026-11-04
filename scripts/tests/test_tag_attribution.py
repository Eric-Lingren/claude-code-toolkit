"""Tests for tag-attribution.py and its server-side use in log-learning.py."""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
TAGGER = SHARED / "scripts" / "learning" / "tag-attribution.py"
LOG_LEARNING = SHARED / "scripts" / "learning" / "log-learning.py"
SCHEMA = SHARED / "contracts" / "learning-schema.json"

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
SKILL_PATH = "claude-code-shared/skills/debug/SKILL.md"

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
    "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Instrument"},
}


def _load():
    spec = importlib.util.spec_from_file_location("tag_attribution", TAGGER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _change(days_ago, sections, unit="skill:debug", sha="a1b2c3d4e5f6"):
    return {
        "schema_version": "1",
        "change_id": f"chg-{sha}",
        "touched_units": [unit],
        "touched_paths": sorted({s["path"] for s in sections}),
        "sections": sections,
        "merged_at": (NOW - timedelta(days=days_ago)).isoformat(),
    }


def _sec(path=SKILL_PATH, section="## Instrument"):
    return {"path": path, "section": section}


def tag(learning, changes=(), applied=()):
    return _load().tag_attribution(learning, list(changes), list(applied), NOW)


class TestRegressionOf:
    def test_escape_point_inside_changed_section(self):
        got = tag(BASE_LEARNING, [_change(5, [_sec()])])
        assert got == {"kind": "regression_of", "ref": "chg-a1b2c3d4e5f6"}

    def test_path_prefix_and_heading_marks_normalized(self):
        learning = {**BASE_LEARNING, "escape_point": {
            "path": "claude-code-shared/skills/debug/SKILL.md", "section": "Instrument"}}
        got = tag(learning, [_change(5, [_sec()])])
        assert got["kind"] == "regression_of"

    def test_newest_matching_change_wins(self):
        old = _change(20, [_sec()], sha="000000000001")
        new = _change(2, [_sec()], sha="000000000002")
        assert tag(BASE_LEARNING, [old, new])["ref"] == "chg-000000000002"

    def test_change_for_other_skill_ignored(self):
        other = _change(5, [_sec()], unit="skill:to-tasks")
        assert tag(BASE_LEARNING, [other])["kind"] == "new_defect"


class TestNewDefect:
    def test_no_change_records(self):
        assert tag(BASE_LEARNING) == {"kind": "new_defect", "ref": None}

    def test_different_path(self):
        ch = _change(5, [_sec(path="claude-code-shared/skills/debug/other.md")])
        assert tag(BASE_LEARNING, [ch])["kind"] == "new_defect"

    def test_same_path_untouched_section(self):
        ch = _change(5, [_sec(section="## Reproduce")])
        assert tag(BASE_LEARNING, [ch])["kind"] == "new_defect"

    def test_no_escape_point(self):
        learning = {k: v for k, v in BASE_LEARNING.items() if k != "escape_point"}
        assert tag(learning, [_change(5, [_sec()])])["kind"] == "new_defect"

    def test_null_section_never_regression(self):
        learning = {**BASE_LEARNING, "escape_point": {"path": "skills/debug/SKILL.md", "section": None}}
        ch = _change(5, [_sec(), _sec(section="(preamble)")])
        assert tag(learning, [ch]) == {"kind": "new_defect", "ref": None}

    def test_null_improves_never_regression(self):
        learning = {**BASE_LEARNING, "improves": None}
        assert tag(learning, [_change(5, [_sec()])])["kind"] == "new_defect"

    def test_change_older_than_window_ignored(self):
        assert tag(BASE_LEARNING, [_change(61, [_sec()])])["kind"] == "new_defect"

    def test_change_inside_window_edge_counts(self):
        assert tag(BASE_LEARNING, [_change(59, [_sec()])])["kind"] == "regression_of"

    def test_change_exactly_at_window_boundary_counts(self):
        assert tag(BASE_LEARNING, [_change(60, [_sec()])])["kind"] == "regression_of"

    def test_change_merged_after_now_ignored(self):
        assert tag(BASE_LEARNING, [_change(-1, [_sec()])])["kind"] == "new_defect"

    def test_unparseable_merged_at_ignored(self):
        ch = _change(5, [_sec()])
        ch["merged_at"] = "not a date"
        assert tag(BASE_LEARNING, [ch])["kind"] == "new_defect"


def _applied(id_="11111111-1111-4111-8111-111111111111", **over):
    row = {**BASE_LEARNING, "id": id_, "status": "applied"}
    row.update(over)
    return row


class TestRecurrenceOf:
    def test_same_cause_and_escape_point_as_applied(self):
        got = tag(BASE_LEARNING, applied=[_applied()])
        assert got == {"kind": "recurrence_of", "ref": "11111111-1111-4111-8111-111111111111"}

    def test_non_applied_status_ignored(self):
        assert tag(BASE_LEARNING, applied=[_applied(status="captured")])["kind"] == "new_defect"

    def test_different_cause_is_new_defect(self):
        row = _applied(cause="context_lost_in_handoff")
        assert tag(BASE_LEARNING, applied=[row])["kind"] == "new_defect"

    def test_other_cause_compares_label(self):
        learning = {**BASE_LEARNING, "cause": "other", "cause_label": "a"}
        same = _applied(cause="other", cause_label="a")
        diff = _applied(cause="other", cause_label="b")
        assert tag(learning, applied=[same])["kind"] == "recurrence_of"
        assert tag(learning, applied=[diff])["kind"] == "new_defect"

    def test_different_section_is_new_defect(self):
        row = _applied(escape_point={"path": "skills/debug/SKILL.md", "section": "## Reproduce"})
        assert tag(BASE_LEARNING, applied=[row])["kind"] == "new_defect"

    def test_no_escape_point_never_recurrence(self):
        learning = {k: v for k, v in BASE_LEARNING.items() if k != "escape_point"}
        row = {k: v for k, v in _applied().items() if k != "escape_point"}
        assert tag(learning, applied=[row])["kind"] == "new_defect"

    def test_regression_takes_precedence(self):
        got = tag(BASE_LEARNING, [_change(5, [_sec()])], [_applied()])
        assert got["kind"] == "regression_of"

    def test_in_window_fix_commit_routes_to_recurrence(self):
        fix = _change(5, [_sec()])
        fix["source_refs"] = ["11111111-1111-4111-8111-111111111111"]
        got = tag(BASE_LEARNING, [fix], [_applied()])
        assert got == {"kind": "recurrence_of", "ref": "11111111-1111-4111-8111-111111111111"}

    def test_fix_commit_skipped_but_other_change_still_regression(self):
        fix = _change(5, [_sec()], sha="aaaaaaaaaaaa")
        fix["source_refs"] = ["11111111-1111-4111-8111-111111111111"]
        other = _change(10, [_sec()], sha="bbbbbbbbbbbb")
        got = tag(BASE_LEARNING, [fix, other], [_applied()])
        assert got == {"kind": "regression_of", "ref": "chg-bbbbbbbbbbbb"}

    def test_null_section_recurrence_allowed(self):
        ep = {"path": "skills/debug/SKILL.md", "section": None}
        learning = {**BASE_LEARNING, "escape_point": ep}
        assert tag(learning, applied=[_applied(escape_point=ep)])["kind"] == "recurrence_of"


class TestCli:
    def test_cli_reads_changes_dir_and_learnings_file(self, tmp_path):
        changes = tmp_path / "changes"
        changes.mkdir()
        rec = _change(0, [_sec()])
        rec["merged_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        (changes / "chg-a1b2c3d4e5f6.json").write_text(json.dumps(rec))
        (changes / "broken.json").write_text("{not json")
        res = subprocess.run(
            [sys.executable, str(TAGGER), "--changes-dir", str(changes),
             "--learnings-file", str(tmp_path / "missing.jsonl")],
            input=json.dumps(BASE_LEARNING), capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout) == {"kind": "regression_of", "ref": "chg-a1b2c3d4e5f6"}

    def test_cli_bad_stdin_exits_1(self):
        res = subprocess.run([sys.executable, str(TAGGER)], input="nope",
                             capture_output=True, text=True)
        assert res.returncode == 1


# -- log-learning.py integration ------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def dotfiles_repo(tmp_path):
    repo = tmp_path / "dotfiles"
    skill = repo / "claude-code-shared" / "skills" / "debug"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Debug\n\n## Instrument\n\nold text\n\n## Reproduce\n\nmore\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base_sha = _rev(repo)
    (skill / "SKILL.md").write_text("# Debug\n\n## Instrument\n\nnew text\n\n## Reproduce\n\nmore\n")
    _git(repo, "commit", "-q", "-am", "edit Instrument")
    # The change record points at these real commits so the regression_of check can load the diff.
    changes = repo / "attribution" / "change-records"
    changes.mkdir(parents=True)
    rec = _change(0, [_sec()])
    rec["base_sha"], rec["merged_sha"] = base_sha, _rev(repo)
    rec["merged_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    (changes / "chg-a1b2c3d4e5f6.json").write_text(json.dumps(rec))
    return repo


def _rev(repo):
    res = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True)
    return res.stdout.strip()


def _run_writer(payload, out, repo):
    # Stub judge that keeps every regression_of tag; the real judge is never called from tests.
    judge = '/bin/echo \'{"verdict": "pass", "confidence": "confirmed", "reason": "stub"}\''
    env = {**os.environ, "LOG_LEARNING_DEST": str(out), "LOG_LEARNING_DOTFILES_DIR": str(repo),
           "LOG_LEARNING_JUDGE_CMD": judge}
    return subprocess.run([sys.executable, str(LOG_LEARNING)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def _rows(out):
    return [json.loads(l) for l in (out / "unified-learnings.jsonl").read_text().splitlines()]


class TestLogLearningTags:
    def test_writes_regression_tag(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        res = _run_writer(BASE_LEARNING, out, dotfiles_repo)
        assert res.returncode == 0, res.stderr
        assert _rows(out)[0]["attribution_tag"] == {"kind": "regression_of", "ref": "chg-a1b2c3d4e5f6"}

    def test_writes_new_defect_for_untouched_section(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        learning = {**BASE_LEARNING, "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Reproduce"}}
        assert _run_writer(learning, out, dotfiles_repo).returncode == 0
        assert _rows(out)[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}

    def test_missing_section_in_file_nulls_section_and_new_defect(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        learning = {**BASE_LEARNING, "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Nope"}}
        assert _run_writer(learning, out, dotfiles_repo).returncode == 0
        row = _rows(out)[0]
        assert row["escape_point"]["section"] is None
        assert row["attribution_tag"]["kind"] == "new_defect"

    def test_recurrence_of_applied_learning(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        out.mkdir()
        applied = {**BASE_LEARNING, "schema_version": "2", "id": "22222222-2222-4222-8222-222222222222",
                   "timestamp": "2026-10-01T00:00:00Z", "status": "applied",
                   "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Reproduce"}}
        (out / "unified-learnings.jsonl").write_text(json.dumps(applied) + "\n")
        learning = {**BASE_LEARNING, "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Reproduce"}}
        assert _run_writer(learning, out, dotfiles_repo).returncode == 0
        assert _rows(out)[-1]["attribution_tag"] == {
            "kind": "recurrence_of", "ref": "22222222-2222-4222-8222-222222222222"}

    def test_rejected_regression_falls_back_to_recurrence(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        out.mkdir()
        applied = {**BASE_LEARNING, "schema_version": "2", "id": "22222222-2222-4222-8222-222222222222",
                   "timestamp": "2026-10-01T00:00:00Z", "status": "applied"}
        (out / "unified-learnings.jsonl").write_text(json.dumps(applied) + "\n")
        judge = '/bin/echo \'{"verdict": "fail", "confidence": "confirmed", "reason": "stub"}\''
        env = {**os.environ, "LOG_LEARNING_DEST": str(out), "LOG_LEARNING_DOTFILES_DIR": str(dotfiles_repo),
               "LOG_LEARNING_JUDGE_CMD": judge}
        res = subprocess.run([sys.executable, str(LOG_LEARNING)], input=json.dumps(BASE_LEARNING),
                             capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stderr
        assert _rows(out)[-1]["attribution_tag"] == {
            "kind": "recurrence_of", "ref": "22222222-2222-4222-8222-222222222222"}

    def test_no_change_records_dir_gives_new_defect(self, tmp_path, dotfiles_repo):
        shutil.rmtree(dotfiles_repo / "attribution")
        out = tmp_path / "out"
        assert _run_writer(BASE_LEARNING, out, dotfiles_repo).returncode == 0
        assert _rows(out)[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}

    def test_rejects_caller_supplied_attribution_tag(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        res = _run_writer({**BASE_LEARNING, "attribution_tag": {"kind": "new_defect", "ref": None}},
                          out, dotfiles_repo)
        assert res.returncode != 0
        assert "attribution_tag" in res.stderr
        assert not (out / "unified-learnings.jsonl").exists()

    def test_written_row_validates_against_schema(self, tmp_path, dotfiles_repo):
        from jsonschema import Draft7Validator
        out = tmp_path / "out"
        assert _run_writer(BASE_LEARNING, out, dotfiles_repo).returncode == 0
        errs = list(Draft7Validator(json.loads(SCHEMA.read_text())).iter_errors(_rows(out)[0]))
        assert errs == [], [e.message for e in errs]


class TestSchemaAttributionTag:
    def _errors(self, tag_value):
        from jsonschema import Draft7Validator
        row = {**BASE_LEARNING, "schema_version": "2", "id": "a1b2c3d4-e5f6-4890-abcd-ef1234567890",
               "timestamp": "2026-06-05T12:00:00Z", "attribution_tag": tag_value}
        return list(Draft7Validator(json.loads(SCHEMA.read_text())).iter_errors(row))

    def test_accepts_each_kind(self):
        assert self._errors({"kind": "regression_of", "ref": "chg-a1b2c3d4e5f6"}) == []
        assert self._errors({"kind": "recurrence_of", "ref": "abc"}) == []
        assert self._errors({"kind": "new_defect", "ref": None}) == []

    def test_rejects_unknown_kind_and_extra_keys(self):
        assert self._errors({"kind": "bogus", "ref": None}) != []
        assert self._errors({"kind": "new_defect", "ref": None, "x": 1}) != []
        assert self._errors({"kind": "new_defect"}) != []
