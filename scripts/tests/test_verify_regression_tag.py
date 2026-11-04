"""Tests for load-change-diff.py and the regression_of verification in log-learning.py.

The judge is never a real model: log-learning.py runs the command in LOG_LEARNING_JUDGE_CMD,
and these tests point it at a stub script.
"""

import importlib.util
import io
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
HELPER = SHARED / "scripts" / "learning" / "load-change-diff.py"
LOG_LEARNING = SHARED / "scripts" / "learning" / "log-learning.py"
JUDGE_DOC = SHARED / "agents" / "learning" / "artifact-grounding-judge.md"

SKILL_PATH = "claude-code-shared/skills/debug/SKILL.md"

BASE_TEXT = "# Debug\n\n## Instrument\n\nold instrument text\n\n## Reproduce\n\nold repro text\n"
MERGED_TEXT = "# Debug\n\n## Instrument\n\nnew instrument text\n\n## Reproduce\n\nold repro text\n"

LEARNING = {
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

STUB = """#!/usr/bin/env python3
import json, os, sys
payload = sys.stdin.read()
with open(os.environ["STUB_CALLS"], "a") as fh:
    fh.write(payload.replace("\\n", " ") + "\\n")
mode = os.environ.get("STUB_MODE", "keep")
if mode == "keep":
    print(json.dumps({"verdict": "pass", "confidence": "confirmed", "reason": "section edited in diff"}))
elif mode == "ungrounded":
    print(json.dumps({"verdict": "rejected", "confidence": "candidate", "reason": "diff does not touch the tagged behavior"}))
elif mode == "garbage":
    print("not json at all")
else:
    sys.stderr.write("judge blew up\\n")
    sys.exit(3)
"""


def _git(repo, *args):
    res = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    )
    return res.stdout.strip()


def _make_repo(tmp_path, merged_text=MERGED_TEXT, record_sections=None):
    repo = tmp_path / "dotfiles"
    skill = repo / "claude-code-shared" / "skills" / "debug"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(BASE_TEXT)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    (skill / "SKILL.md").write_text(merged_text)
    _git(repo, "commit", "-q", "-am", "edit")
    merged_sha = _git(repo, "rev-parse", "HEAD")
    sections = record_sections if record_sections is not None else [
        {"path": SKILL_PATH, "section": "## Instrument"}]
    record = {
        "schema_version": "1",
        "change_id": f"chg-{merged_sha[:12]}",
        "touched_units": ["skill:debug"],
        "touched_paths": [SKILL_PATH],
        "sections": sections,
        "base_sha": base_sha,
        "merged_sha": merged_sha,
        "merged_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    }
    changes = repo / "attribution" / "change-records"
    changes.mkdir(parents=True)
    (changes / f"{record['change_id']}.json").write_text(json.dumps(record))
    return repo, record


@pytest.fixture
def stub(tmp_path):
    path = tmp_path / "judge-stub.py"
    path.write_text(STUB)
    return path


def _run_writer(tmp_path, repo, stub, mode, learning=LEARNING, judge_cmd=None):
    out = tmp_path / "out"
    calls = tmp_path / "calls.txt"
    env = {
        **os.environ,
        "LOG_LEARNING_DEST": str(out),
        "LOG_LEARNING_DOTFILES_DIR": str(repo),
        "LOG_LEARNING_JUDGE_CMD": judge_cmd or f"{sys.executable} {stub}",
        "STUB_CALLS": str(calls),
        "STUB_MODE": mode,
    }
    res = subprocess.run([sys.executable, str(LOG_LEARNING)], input=json.dumps(learning),
                         capture_output=True, text=True, env=env)
    rows = []
    rows_file = out / "unified-learnings.jsonl"
    if rows_file.exists():
        rows = [json.loads(l) for l in rows_file.read_text().splitlines()]
    n_calls = len(calls.read_text().splitlines()) if calls.exists() else 0
    return res, rows, n_calls


def _load_helper():
    spec = importlib.util.spec_from_file_location("load_change_diff", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLoadChangeDiff:
    def test_section_edited_in_diff(self, tmp_path):
        repo, record = _make_repo(tmp_path)
        got = _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "## Instrument", repo)
        assert got["section_edited"] is True
        assert got["change_id"] == record["change_id"]
        assert "+new instrument text" in got["section_diff"]
        assert "old repro text" not in got["section_diff"]
        assert "+new instrument text" in got["diff"]

    def test_section_absent_from_diff(self, tmp_path):
        repo, record = _make_repo(tmp_path)
        got = _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "Reproduce", repo)
        assert got["section_edited"] is False
        assert got["section_diff"] == ""
        assert "+new instrument text" in got["diff"]

    def test_pure_deletion_of_a_step_line_counts_as_section_edited(self, tmp_path):
        # "+N,0" hunk (count == 0 branch of _hunk_in_section): attributed to the section above the gap.
        repo, record = _make_repo(tmp_path, merged_text=BASE_TEXT.replace("old instrument text\n\n", ""))
        got = _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "## Instrument", repo)
        assert got["section_edited"] is True
        assert "-old instrument text" in got["section_diff"]
        other = _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "## Reproduce", repo)
        assert other["section_edited"] is False

    def test_pure_deletion_of_a_whole_heading_counts_for_the_section_above(self, tmp_path):
        merged = BASE_TEXT.replace("## Reproduce\n\nold repro text\n", "")
        repo, record = _make_repo(tmp_path, merged_text=merged)
        h = _load_helper()
        got = h.load_change_diff(record, "skills/debug/SKILL.md", "## Instrument", repo)
        assert got["section_edited"] is True
        assert "-## Reproduce" in got["section_diff"]
        # The deleted heading itself no longer exists on the new side.
        gone = h.load_change_diff(record, "skills/debug/SKILL.md", "## Reproduce", repo)
        assert gone["section_edited"] is False

    def test_unknown_commit_raises(self, tmp_path):
        repo, record = _make_repo(tmp_path)
        record["merged_sha"] = "0" * 40
        with pytest.raises(ValueError):
            _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "## Instrument", repo)

    @pytest.mark.parametrize("field", ["base_sha", "merged_sha"])
    @pytest.mark.parametrize("bad", ["--output=OUT", "HEAD", "abc123", "A" * 40, "0" * 39, "0" * 41])
    def test_non_hex_sha_rejected_and_nothing_written(self, tmp_path, field, bad):
        repo, record = _make_repo(tmp_path)
        out = tmp_path / "pwned.txt"
        record[field] = bad.replace("OUT", str(out))
        with pytest.raises(ValueError, match="40-hex"):
            _load_helper().load_change_diff(record, "skills/debug/SKILL.md", "## Instrument", repo)
        assert not out.exists()
        assert not (repo / "pwned.txt").exists()

    def test_path_not_in_change_raises(self, tmp_path):
        repo, record = _make_repo(tmp_path)
        with pytest.raises(ValueError):
            _load_helper().load_change_diff(record, "skills/other/SKILL.md", "## Instrument", repo)

    def test_cli_prints_json(self, tmp_path):
        repo, record = _make_repo(tmp_path)
        res = subprocess.run(
            [sys.executable, str(HELPER), "--repo", str(repo), "--changes-dir", str(repo / "attribution" / "change-records"),
             "--change-id", record["change_id"],
             "--path", "skills/debug/SKILL.md", "--section", "## Instrument"],
            capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["section_edited"] is True

    def test_cli_unknown_change_exits_1(self, tmp_path):
        repo, _ = _make_repo(tmp_path)
        res = subprocess.run(
            [sys.executable, str(HELPER), "--repo", str(repo), "--change-id", "chg-000000000000",
             "--path", "skills/debug/SKILL.md", "--section", "## Instrument"],
            capture_output=True, text=True)
        assert res.returncode == 1


class TestRegressionVerification:
    def test_edited_section_tag_is_kept(self, tmp_path, stub):
        repo, record = _make_repo(tmp_path)
        res, rows, n_calls = _run_writer(tmp_path, repo, stub, "keep")
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"] == {"kind": "regression_of", "ref": record["change_id"]}
        assert n_calls == 1

    def test_judge_is_given_the_real_diff(self, tmp_path, stub):
        repo, _ = _make_repo(tmp_path)
        _run_writer(tmp_path, repo, stub, "keep")
        sent = (tmp_path / "calls.txt").read_text()
        assert "+new instrument text" in sent
        assert "regression_of" in sent

    def test_judge_payload_drops_full_diff_and_truncates_section_diff(self):
        spec = importlib.util.spec_from_file_location("ll_bound", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        big = "x" * (mod.JUDGE_DIFF_BYTE_BUDGET * 2)
        out = mod._bounded_change_diff({"diff": big, "section_diff": big, "section_edited": True})
        assert "diff" not in out
        assert out["section_diff_truncated"] is True
        assert len(out["section_diff"].encode()) < mod.JUDGE_DIFF_BYTE_BUDGET + 200
        small = mod._bounded_change_diff({"diff": "d", "section_diff": "s"})
        assert small == {"section_diff": "s"}

    def test_section_absent_from_diff_downgrades(self, tmp_path, stub):
        # The record claims "## Instrument" was edited, but the real diff only touched Reproduce.
        repo, _ = _make_repo(
            tmp_path,
            merged_text=BASE_TEXT.replace("old repro text", "new repro text"),
            record_sections=[{"path": SKILL_PATH, "section": "## Instrument"}],
        )
        res, rows, _ = _run_writer(tmp_path, repo, stub, "keep")
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}

    def test_ungrounded_verdict_downgrades_and_keeps_other_fields(self, tmp_path, stub):
        repo, _ = _make_repo(tmp_path)
        res, rows, _ = _run_writer(tmp_path, repo, stub, "ungrounded")
        assert res.returncode == 0, res.stderr
        row = rows[0]
        assert row["attribution_tag"] == {"kind": "new_defect", "ref": None}
        for key in ("problem", "lesson", "fix", "cause", "improves", "evidence", "confidence"):
            assert row[key] == LEARNING[key]
        assert row["escape_point"] == {"path": "skills/debug/SKILL.md", "section": "## Instrument"}

    @pytest.mark.parametrize("mode", ["error", "garbage"])
    def test_errored_judge_downgrades(self, tmp_path, stub, mode):
        repo, _ = _make_repo(tmp_path)
        res, rows, n_calls = _run_writer(tmp_path, repo, stub, mode)
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"] == {
            "kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}
        assert n_calls == 1
        assert "WARNING" in res.stderr

    def test_missing_judge_command_downgrades(self, tmp_path, stub):
        repo, _ = _make_repo(tmp_path)
        res, rows, _ = _run_writer(tmp_path, repo, stub, "keep",
                                   judge_cmd=str(tmp_path / "no-such-judge"))
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"] == {
            "kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}

    def test_unloadable_diff_downgrades(self, tmp_path, stub):
        repo, record = _make_repo(tmp_path)
        rec_file = repo / "attribution" / "change-records" / f"{record['change_id']}.json"
        record["merged_sha"] = "0" * 40
        rec_file.write_text(json.dumps(record))
        res, rows, n_calls = _run_writer(tmp_path, repo, stub, "keep")
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}
        assert n_calls == 0

    def test_new_defect_never_calls_judge(self, tmp_path, stub):
        repo, _ = _make_repo(tmp_path)
        learning = {**LEARNING, "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Reproduce"}}
        res, rows, n_calls = _run_writer(tmp_path, repo, stub, "keep", learning=learning)
        assert res.returncode == 0, res.stderr
        assert rows[0]["attribution_tag"]["kind"] == "new_defect"
        assert n_calls == 0


SLOW_STUB = """#!/usr/bin/env python3
import sys, time
sys.stdin.read()
time.sleep(30)
print('{"verdict": "pass", "confidence": "confirmed", "reason": "too late"}')
"""


class TestJudgeTimeout:
    """A judge that outlives JUDGE_TIMEOUT_SECONDS downgrades the tag to new_defect with a WARNING."""

    @pytest.fixture
    def log_learning(self):
        spec = importlib.util.spec_from_file_location("log_learning_timeout_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_slow_judge_downgrades_with_warning(self, tmp_path, monkeypatch, capsys, log_learning):
        repo, record = _make_repo(tmp_path)
        slow = tmp_path / "slow-judge.py"
        slow.write_text(SLOW_STUB)
        monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(repo))
        monkeypatch.setenv("LOG_LEARNING_JUDGE_CMD", f"{sys.executable} {slow}")
        monkeypatch.setattr(log_learning, "JUDGE_TIMEOUT_SECONDS", 0.5)

        tag = {"kind": "regression_of", "ref": record["change_id"]}
        got = log_learning._verify_regression_tag(dict(LEARNING), tag)

        assert got == {"kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}
        err = capsys.readouterr().err
        assert f"WARNING: regression_of {record['change_id']} downgraded to new_defect" in err
        assert "timed out" in err


FAKE_GIT = """#!/bin/sh
# Wraps the real binary: the `diff` subcommand misbehaves per FAKE_GIT_MODE, the rest passes through.
for a in "$@"; do
  if [ "$a" = "diff" ]; then
    if [ "$FAKE_GIT_MODE" = "hang" ]; then sleep 30; fi
    echo "fake failure" >&2
    exit 1
  fi
done
exec "$REAL_GIT" "$@"
"""


class TestTimeoutConstants:
    def test_judge_timeout_is_20_seconds(self):
        spec = importlib.util.spec_from_file_location("log_learning_const_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.JUDGE_TIMEOUT_SECONDS == 20

    def test_diff_call_timeout_is_a_named_10_second_constant(self):
        assert _load_helper().GIT_TIMEOUT_SECONDS == 10


class TestLearningStillSaves:
    """A slow judge or a failing/hanging diff call never loses the learning."""

    @pytest.fixture
    def log_learning(self):
        spec = importlib.util.spec_from_file_location("log_learning_saves_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run_main(self, log_learning, monkeypatch, tmp_path):
        out = tmp_path / "out"
        monkeypatch.setattr(log_learning, "LEARNINGS_DIR", out)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(LEARNING)))
        log_learning.main()
        rows_file = out / "unified-learnings.jsonl"
        return [json.loads(l) for l in rows_file.read_text().splitlines()]

    def test_slow_judge_downgrades_and_learning_is_appended(self, tmp_path, monkeypatch, capsys, log_learning):
        repo, record = _make_repo(tmp_path)
        slow = tmp_path / "slow-judge.py"
        slow.write_text(SLOW_STUB)
        monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(repo))
        monkeypatch.setenv("LOG_LEARNING_JUDGE_CMD", f"{sys.executable} {slow}")
        monkeypatch.setattr(log_learning, "JUDGE_TIMEOUT_SECONDS", 0.5)

        rows = self._run_main(log_learning, monkeypatch, tmp_path)

        assert len(rows) == 1
        assert rows[0]["attribution_tag"] == {"kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}
        assert rows[0]["problem"] == LEARNING["problem"]
        err = capsys.readouterr().err
        assert "WARNING: regression_of" in err and "timed out" in err

    @pytest.mark.parametrize("mode", ["fail", "hang"])
    def test_diff_failure_or_timeout_downgrades_and_learning_is_appended(
            self, tmp_path, monkeypatch, capsys, log_learning, mode):
        repo, record = _make_repo(tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake = fake_bin / "git"
        fake.write_text(FAKE_GIT)
        fake.chmod(0o755)
        monkeypatch.setenv("REAL_GIT", shutil.which("git"))
        monkeypatch.setenv("FAKE_GIT_MODE", mode)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(repo))
        judge_calls = tmp_path / "judge-calls.txt"
        stub = tmp_path / "judge-stub.py"
        stub.write_text(STUB)
        monkeypatch.setenv("LOG_LEARNING_JUDGE_CMD", f"{sys.executable} {stub}")
        monkeypatch.setenv("STUB_CALLS", str(judge_calls))

        real_load = log_learning._load_script

        def load(filename, modname):
            mod = real_load(filename, modname)
            if filename == "load-change-diff.py":
                mod.GIT_TIMEOUT_SECONDS = 0.5
            return mod

        monkeypatch.setattr(log_learning, "_load_script", load)

        rows = self._run_main(log_learning, monkeypatch, tmp_path)

        assert len(rows) == 1
        assert rows[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}
        assert rows[0]["problem"] == LEARNING["problem"]
        assert not judge_calls.exists()
        err = capsys.readouterr().err
        assert "WARNING: regression_of" in err and "could not load diff" in err
        if mode == "hang":
            assert "timed out" in err


class TestJudgeDocUnreadable:
    """An unreadable JUDGE_DOC must downgrade to new_defect, not crash the write."""

    @pytest.fixture
    def log_learning(self):
        spec = importlib.util.spec_from_file_location("log_learning_doc_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize("kind", ["missing", "invalid_utf8", "directory"])
    def test_unreadable_judge_doc_downgrades(self, tmp_path, monkeypatch, capsys, log_learning, kind):
        repo, record = _make_repo(tmp_path)
        doc = tmp_path / "judge-doc.md"
        if kind == "invalid_utf8":
            doc.write_bytes(b"\xff\xfe\x80 not utf-8")
        elif kind == "directory":
            doc.mkdir()
        monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(repo))
        monkeypatch.setenv("LOG_LEARNING_JUDGE_CMD", f"{sys.executable} -c pass")
        monkeypatch.setattr(log_learning, "JUDGE_DOC", doc)

        tag = {"kind": "regression_of", "ref": record["change_id"]}
        got = log_learning._verify_regression_tag(dict(LEARNING), tag)

        assert got == {"kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}
        assert f"WARNING: regression_of {record['change_id']} downgraded to new_defect" in capsys.readouterr().err


class TestJudgeDoc:
    @pytest.fixture
    def doc(self):
        return JUDGE_DOC.read_text()

    def test_documents_regression_of_check(self, doc):
        assert "regression_of" in doc
        assert "## Regression check" in doc

    def test_regression_check_keeps_output_contract(self, doc):
        section = doc.split("## Regression check", 1)[1]
        assert '"verdict"' in section and "reason" in section and "confidence" in section

    def test_regression_check_does_not_write(self, doc):
        section = doc.split("## Regression check", 1)[1]
        assert "log-learning.py" in section
        assert "do not call" in section.lower()

    def test_names_the_diff_helper(self, doc):
        assert "load-change-diff.py" in doc
