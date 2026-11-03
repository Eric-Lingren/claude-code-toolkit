"""Tests for the REVIEW_WORKER=1 guard in egress/post-pr-review.py.

gh (and cmux, terminal-notifier, claude) are replaced by fake binaries on PATH
that record every invocation, so no real external call is ever made.
"""

import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "egress" / "post-pr-review.py"

FAKE_GH = """#!/bin/sh
echo "$@" >> "$GH_CALL_LOG"
case "$1 $2" in
  "pr view") echo '{"headRefOid":"abc123","url":"https://github.com/o/r/pull/1"}' ;;
  "repo view") echo 'o/r' ;;
  "pr diff") printf '%s\\n' 'diff --git a/f.py b/f.py' '--- a/f.py' '+++ b/f.py' '@@ -1,1 +1,2 @@' ' a' '+b' ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    for name in ("cmux", "terminal-notifier", "claude"):
        stub = bin_dir / name
        stub.write_text('#!/bin/sh\necho "$0 $@" >> "$GH_CALL_LOG.other"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gh-calls.log"
    environ = {k: v for k, v in os.environ.items() if k != "REVIEW_WORKER"}
    environ["PATH"] = f"{bin_dir}{os.pathsep}{environ['PATH']}"
    environ["GH_CALL_LOG"] = str(log)
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps([{"file": "f.py", "line": 2, "body": "nit"}]))
    return environ, log, findings


def run(environ, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          env=environ, capture_output=True, text=True)


def gh_calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_refuses_under_review_worker(env):
    environ, log, findings = env
    environ["REVIEW_WORKER"] = "1"
    result = run(environ, "--pr", "1", "--findings", str(findings))
    assert result.returncode != 0
    assert "REVIEW_WORKER=1" in result.stderr
    assert "refusing" in result.stderr
    assert gh_calls(log) == []


def test_refuses_under_review_worker_even_with_dry_run_and_append(env):
    environ, log, findings = env
    environ["REVIEW_WORKER"] = "1"
    for extra in (["--dry-run"], ["--append-pending"], ["--dry-run", "--append-pending"]):
        result = run(environ, "--pr", "1", "--findings", str(findings), *extra)
        assert result.returncode != 0
        assert "refusing" in result.stderr
    assert gh_calls(log) == []


def test_without_review_worker_reaches_dry_run_path(env):
    environ, log, findings = env
    result = run(environ, "--pr", "1", "--findings", str(findings), "--dry-run")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["commit_id"] == "abc123"
    assert payload["comments"][0]["line"] == 2
    calls = gh_calls(log)
    assert calls and all(not c.startswith("api --method POST") for c in calls)


@pytest.mark.parametrize("value", ["0", ""])
def test_other_review_worker_values_do_not_block(env, value):
    environ, log, findings = env
    environ["REVIEW_WORKER"] = value
    result = run(environ, "--pr", "1", "--findings", str(findings), "--dry-run")
    assert result.returncode == 0, result.stderr
