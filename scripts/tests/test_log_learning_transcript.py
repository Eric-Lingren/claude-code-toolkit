"""Tests for log-learning.py --transcript-path: session_id stem and skills_run stamps."""

import json
import os
import pathlib
import shutil
import subprocess
import sys

from test_log_learning import LOG_LEARNING, MINIMAL_ATTRIBUTION, MINIMAL_SELF

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SESSION = "11111111-2222-4333-8444-555555555555"


def _run(payload, dest, *args):
    env = {**os.environ, "LOG_LEARNING_DEST": str(dest)}
    return subprocess.run(
        [sys.executable, str(LOG_LEARNING), *args],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def _rows(dest):
    text = (dest / "unified-learnings.jsonl").read_text().strip()
    return [json.loads(line) for line in text.split("\n")]


def _transcript(tmp_path, fixture):
    path = tmp_path / f"{SESSION}.jsonl"
    shutil.copy(FIXTURES / fixture, path)
    return path


def test_attribution_session_id_is_transcript_stem(tmp_path):
    t = _transcript(tmp_path, "transcript-two-skills.jsonl")
    dest = tmp_path / "out"
    res = _run(MINIMAL_ATTRIBUTION, dest, "--transcript-path", str(t))
    assert res.returncode == 0, res.stderr
    assert _rows(dest)[0]["session_id"] == SESSION


def test_skills_run_extracted_in_order_deduped(tmp_path):
    t = _transcript(tmp_path, "transcript-two-skills.jsonl")
    dest = tmp_path / "out"
    res = _run(MINIMAL_ATTRIBUTION, dest, "--transcript-path", str(t))
    assert res.returncode == 0, res.stderr
    assert _rows(dest)[0]["skills_run"] == ["grill-me", "to-seed"]


def test_no_skills_gives_empty_list(tmp_path):
    t = _transcript(tmp_path, "transcript-no-skills.jsonl")
    dest = tmp_path / "out"
    res = _run(MINIMAL_ATTRIBUTION, dest, "--transcript-path", f"{t}")
    assert res.returncode == 0, res.stderr
    assert _rows(dest)[0]["skills_run"] == []


def test_unreadable_transcript_still_logs_without_skills_run(tmp_path):
    dest = tmp_path / "out"
    res = _run(MINIMAL_ATTRIBUTION, dest, "--transcript-path", str(tmp_path / f"{SESSION}.jsonl"))
    assert res.returncode == 0, res.stderr
    row = _rows(dest)[0]
    assert row["session_id"] == SESSION
    assert "skills_run" not in row


def test_caller_cannot_supply_skills_run(tmp_path):
    res = _run({**MINIMAL_ATTRIBUTION, "skills_run": ["x"]}, tmp_path / "out")
    assert res.returncode == 1
    assert "skills_run" in res.stderr


def test_self_row_session_id_unchanged_and_no_skills_run(tmp_path):
    ref = f"/tmp/{SESSION}.jsonl"
    payload = {**MINIMAL_SELF, "evidence": [{"source": "transcript", "ref": ref, "quote": "q"}]}
    dest = tmp_path / "out"
    res = _run(payload, dest)
    assert res.returncode == 0, res.stderr
    row = _rows(dest)[0]
    assert row["session_id"] == SESSION
    assert "skills_run" not in row


def test_existing_rows_not_rewritten(tmp_path):
    dest = tmp_path / "out"
    assert _run(MINIMAL_SELF, dest).returncode == 0
    first = (dest / "unified-learnings.jsonl").read_text()
    t = _transcript(tmp_path, "transcript-two-skills.jsonl")
    assert _run(MINIMAL_ATTRIBUTION, dest, "--transcript-path", str(t)).returncode == 0
    assert (dest / "unified-learnings.jsonl").read_text().startswith(first)
