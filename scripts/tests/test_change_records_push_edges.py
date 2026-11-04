"""Edge-branch tests for change_records_push: git timeout (code 124) and jsonschema missing."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "attribution"))
import change_records_push as crp  # noqa: E402


@pytest.fixture
def hanging_git(tmp_path, monkeypatch):
    """A fake `git` first on PATH: cheap verbs answer fast, `fetch` hangs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  symbolic-ref) echo main ;;\n'
        '  remote) echo https://example.invalid/x.git ;;\n'
        '  fetch) exec sleep 30 ;;\n'
        'esac\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return fake


def test_git_timeout_returns_124(hanging_git, tmp_path):
    proc = crp._git(tmp_path, "fetch", timeout=0.3)
    assert proc.returncode == 124
    assert proc.stderr == "git fetch timed out"
    assert proc.stdout == ""


def test_push_reports_fetch_timeout_as_warning(hanging_git, tmp_path, monkeypatch):
    monkeypatch.setattr(crp, "NET_TIMEOUT", 0.3)
    out = tmp_path / "records"
    out.mkdir()
    result = crp.push_change_records(tmp_path, out)
    assert result == {"status": "failed",
                      "message": "warning: push failed: fetch: git fetch timed out"}


def test_invalid_records_returns_none_without_jsonschema(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "jsonschema", None)  # makes `import jsonschema` raise
    assert crp._invalid_records(tmp_path, ["abc"], "records") is None


def test_push_skips_with_message_when_jsonschema_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    out = tmp_path / "records"
    out.mkdir()

    def fake_git(repo, *args, timeout=60):
        stdout = {"symbolic-ref": "main\n", "rev-list": "deadbeef\n"}.get(args[0], "")
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(crp, "_git", fake_git)
    monkeypatch.setattr(crp, "_is_record_commit", lambda *a: True)
    result = crp.push_change_records(tmp_path, out)
    assert result == {
        "status": "skipped",
        "message": "warning: push skipped: jsonschema unavailable, cannot validate records"}
