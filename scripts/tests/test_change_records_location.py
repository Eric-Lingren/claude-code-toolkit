"""Agreement guard: every script's default change-records folder is the helper's folder.

A wrong path fails silently (readers treat a missing folder as no records), so this
test pins every default to scripts/shared/change_records_path.py.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema

SHARED = Path(__file__).resolve().parents[2]
DOTFILES = SHARED.parent
SCRIPTS = SHARED / "scripts"
sys.path.insert(0, str(SCRIPTS / "shared"))

from change_records_path import change_records_path  # noqa: E402

HELPER = change_records_path()


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def py(code, *argv):
    out = subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_helper_is_attribution_change_records_at_repo_root():
    assert HELPER == DOTFILES.resolve() / "attribution" / "change-records"
    assert HELPER.is_dir()
    assert not (SHARED / "seed-bank").exists()


def test_tag_attribution_default():
    mod = load("learning/tag-attribution.py", "tag_attribution")
    assert Path(mod.DEFAULT_CHANGES_DIR).resolve() == HELPER


def test_drift_alert_default_via_symlink_and_real_path(tmp_path):
    link = tmp_path / ".claude-code-shared"
    link.symlink_to(SHARED)
    code = (
        "import importlib.util, sys; spec = importlib.util.spec_from_file_location('d', sys.argv[1]); "
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.DEFAULT_CHANGES)"
    )
    via_link = py(code, str(link / "scripts" / "usage" / "drift-alert.py"))
    via_real = py(code, str(SCRIPTS / "usage" / "drift-alert.py"))
    assert Path(via_link).resolve() == HELPER
    assert Path(via_real).resolve() == HELPER
    assert via_link == via_real == str(HELPER)


def _run_load_change_diff(tmp_path, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "learning" / "load-change-diff.py"), "--repo", str(tmp_path), *extra,
         "--change-id", "chg-000000000000", "--path", "x", "--section", "y"],
        capture_output=True, text=True)


def test_load_change_diff_default_names_helper_folder(tmp_path):
    res = _run_load_change_diff(tmp_path)
    assert res.returncode != 0
    assert str(HELPER) in res.stderr + res.stdout


def test_load_change_diff_changes_dir_override(tmp_path):
    res = _run_load_change_diff(tmp_path, "--changes-dir", str(tmp_path / "elsewhere"))
    assert str(tmp_path / "elsewhere") in res.stderr + res.stdout
    assert str(HELPER) not in res.stderr + res.stdout


def test_log_learning_changes_dir(monkeypatch, tmp_path):
    mod = load("learning/log-learning.py", "log_learning")
    monkeypatch.delenv("LOG_LEARNING_DOTFILES_DIR", raising=False)
    assert mod._changes_dir().resolve() == HELPER
    monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(tmp_path))
    assert mod._changes_dir() == tmp_path / "attribution" / "change-records"


def test_writer_default_out_dir_is_helper_folder(monkeypatch, tmp_path):
    wcr = load("attribution/write-change-records.py", "wcr")
    seen = {}

    def fake(repo, out_dir, **kw):
        seen["out_dir"] = out_dir
        return {"written": [], "skipped": [], "ignored": [], "errors": []}

    monkeypatch.setattr(wcr, "write_change_records", fake)
    wcr.main(["--repo", str(tmp_path), "--dry-run"])
    assert Path(seen["out_dir"]).resolve() == HELPER
    wcr.main(["--repo", str(tmp_path), "--dry-run", "--out-dir", str(tmp_path / "x")])
    assert seen["out_dir"] == str(tmp_path / "x")


def test_commit_and_push_treat_only_direct_json_files_as_records(tmp_path):
    sys.path.insert(0, str(SCRIPTS / "attribution"))
    import change_records_commit as crc
    import change_records_push as crp

    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}

    def vcs(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True,
                              check=True, env=env).stdout

    vcs("init", "-q")
    folder = tmp_path / "attribution" / "change-records"
    (folder / "sub").mkdir(parents=True)
    for rel in ("chg-aaaaaaaaaaaa.json", "note.txt", "sub/chg-bbbbbbbbbbbb.json"):
        (folder / rel).write_text("{}\n")
    (tmp_path / "chg-cccccccccccc.json").write_text("{}\n")
    assert crc.pending_record_files(tmp_path.resolve(), folder) == [
        "attribution/change-records/chg-aaaaaaaaaaaa.json"]

    vcs("add", "-A")
    vcs("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mixed")
    sha = vcs("rev-parse", "HEAD").strip()
    assert not crp._is_record_commit(tmp_path.resolve(), sha, "attribution/change-records")

    (folder / "chg-dddddddddddd.json").write_text("{}\n")
    vcs("add", "-A")
    vcs("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rec")
    sha = vcs("rev-parse", "HEAD").strip()
    assert crp._is_record_commit(tmp_path.resolve(), sha, "attribution/change-records")


def test_moved_records_validate_against_schema():
    schema = json.loads((SHARED / "contracts" / "change-record-schema.json").read_text())
    files = sorted(HELPER.glob("chg-*.json"))
    assert files
    for f in files:
        jsonschema.validate(json.loads(f.read_text()), schema)
