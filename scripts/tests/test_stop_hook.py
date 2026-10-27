"""Tests for stop-hook.py — learning-eval safety net."""

import importlib.util as _ilu
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
HOOK_FILE = SHARED / "hooks" / "stop-hook.py"
AUDIT_DIR = SHARED / "learnings" / "audit"
SETTINGS = SHARED / "settings.json"

# Load module for unit tests
_spec = _ilu.spec_from_file_location("stop_hook", str(HOOK_FILE))
hook_mod = _ilu.module_from_spec(_spec)


def _make_transcript(last_assistant_text, tmp_path):
    """Create a JSONL transcript file with one assistant turn."""
    transcript = tmp_path / "transcript.jsonl"
    turn = {"role": "assistant", "content": last_assistant_text}
    transcript.write_text(json.dumps(turn) + "\n")
    return str(transcript)


def _run_hook(stdin_data: dict, tmp_path=None, session_id=None, env_override=None):
    """Run the hook script with JSON on stdin. Returns CompletedProcess."""
    env = os.environ.copy()
    if session_id:
        env["CLAUDE_SESSION_ID"] = session_id
    if tmp_path:
        env["HOOK_AUDIT_DIR"] = str(tmp_path / "audit")
        env["HOOK_SESSION_DIR"] = str(tmp_path)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, str(HOOK_FILE)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
    )


class TestHookFileExists:
    def test_hook_file_exists(self):
        assert HOOK_FILE.exists(), f"Not found: {HOOK_FILE}"

    def test_hook_is_executable_python(self):
        content = HOOK_FILE.read_text()
        assert "#!/usr/bin/env python3" in content


class TestRegisteredInSettings:
    def test_stop_hook_registered_in_settings(self):
        settings = json.loads(SETTINGS.read_text())
        stop_hooks = settings.get("hooks", {}).get("Stop", [])
        all_commands = []
        for entry in stop_hooks:
            for h in entry.get("hooks", []):
                all_commands.append(h.get("command", ""))
        assert any("stop-hook" in cmd for cmd in all_commands), (
            f"stop-hook.py not registered in settings.json Stop hooks. Found: {all_commands}"
        )


class TestAuditDirectory:
    def test_audit_directory_exists(self):
        assert AUDIT_DIR.exists(), f"audit/ dir not found: {AUDIT_DIR}"

    def test_audit_log_not_in_unified_glob(self):
        # hook-audit.jsonl must be in audit/ subdir, not directly in learnings/
        # so that learnings/*.jsonl glob patterns don't pick it up
        audit_log = AUDIT_DIR / "hook-audit.jsonl"
        # Check the path is in a subdirectory of learnings/
        assert "audit" in str(audit_log), "audit log should be under learnings/audit/"
        # Check it's not directly in learnings/
        direct = SHARED / "learnings" / "hook-audit.jsonl"
        assert not direct.exists(), "hook-audit.jsonl must NOT be directly in learnings/"


class TestOutcomeRan:
    def test_both_markers_present_appends_ran(self, tmp_path):
        text = "Some output\n<!-- skill-done: debug -->\n<!-- learning-eval: debug -->\nDone."
        transcript = _make_transcript(text, tmp_path)
        sid = "test-session-ran"
        result = _run_hook(
            {"transcript_path": transcript, "session_id": sid},
            tmp_path=tmp_path,
            session_id=sid,
        )
        assert result.returncode == 0
        audit_log = tmp_path / "audit" / "hook-audit.jsonl"
        assert audit_log.exists()
        records = [json.loads(l) for l in audit_log.read_text().strip().splitlines()]
        assert any(r["skill"] == "debug" and r["outcome"] == "ran" for r in records)

    def test_ran_does_not_output_nudge(self, tmp_path):
        text = "<!-- skill-done: debug -->\n<!-- learning-eval: debug -->"
        transcript = _make_transcript(text, tmp_path)
        result = _run_hook(
            {"transcript_path": transcript, "session_id": "test-ran-no-nudge"},
            tmp_path=tmp_path,
            session_id="test-ran-no-nudge",
        )
        assert result.returncode == 0
        # No JSON output with continue:true
        if result.stdout.strip():
            out = json.loads(result.stdout)
            assert out.get("continue") is not True


class TestOutcomeNudged:
    def test_skill_done_without_learning_eval_appends_nudged(self, tmp_path):
        text = "Some output\n<!-- skill-done: debug -->\nFinal answer."
        transcript = _make_transcript(text, tmp_path)
        sid = "test-session-nudge"
        result = _run_hook(
            {"transcript_path": transcript, "session_id": sid},
            tmp_path=tmp_path,
            session_id=sid,
        )
        assert result.returncode == 0
        audit_log = tmp_path / "audit" / "hook-audit.jsonl"
        assert audit_log.exists()
        records = [json.loads(l) for l in audit_log.read_text().strip().splitlines()]
        assert any(r["skill"] == "debug" and r["outcome"] == "nudged" for r in records)

    def test_nudge_outputs_continue_json(self, tmp_path):
        text = "<!-- skill-done: run-tasks -->"
        transcript = _make_transcript(text, tmp_path)
        sid = "test-nudge-output"
        result = _run_hook(
            {"transcript_path": transcript, "session_id": sid},
            tmp_path=tmp_path,
            session_id=sid,
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "Expected JSON output for nudge"
        out = json.loads(result.stdout)
        assert out.get("continue") is True


class TestNoDoubleNudge:
    def test_second_run_does_not_nudge_again(self, tmp_path):
        text = "<!-- skill-done: debug -->"
        transcript = _make_transcript(text, tmp_path)
        sid = "test-no-double-nudge"
        # First run: nudge fires
        _run_hook(
            {"transcript_path": transcript, "session_id": sid},
            tmp_path=tmp_path,
            session_id=sid,
        )
        # Second run: should NOT nudge again
        result = _run_hook(
            {"transcript_path": transcript, "session_id": sid},
            tmp_path=tmp_path,
            session_id=sid,
        )
        assert result.returncode == 0
        out = result.stdout.strip()
        if out:
            parsed = json.loads(out)
            assert parsed.get("continue") is not True, "Should not nudge a second time"


class TestNoMarkersNoAction:
    def test_no_markers_exits_zero_silently(self, tmp_path):
        text = "Just a normal response with no skill markers."
        transcript = _make_transcript(text, tmp_path)
        result = _run_hook(
            {"transcript_path": transcript, "session_id": "test-no-markers"},
            tmp_path=tmp_path,
            session_id="test-no-markers",
        )
        assert result.returncode == 0
        assert not result.stdout.strip()
        audit_log = tmp_path / "audit" / "hook-audit.jsonl"
        assert not audit_log.exists() or audit_log.read_text().strip() == ""

    def test_missing_transcript_exits_zero(self, tmp_path):
        result = _run_hook(
            {"transcript_path": "/nonexistent/transcript.jsonl", "session_id": "test-missing"},
            tmp_path=tmp_path,
            session_id="test-missing",
        )
        assert result.returncode == 0
