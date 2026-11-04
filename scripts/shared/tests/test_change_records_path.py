import os
import subprocess
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SHARED))

import change_records_path as crp  # noqa: E402

TOOLKIT = SHARED.parent.parent  # claude-code-shared
REPO = TOOLKIT.parent


def test_returns_repo_attribution_path():
    p = crp.change_records_path()
    assert p == REPO.resolve() / "attribution" / "change-records"
    assert p.is_absolute()
    assert p == p.resolve()


def test_symlink_to_toolkit_gives_same_path(tmp_path):
    link = tmp_path / "linked-toolkit"
    link.symlink_to(TOOLKIT)
    code = (
        "import sys; sys.path.insert(0, %r); import change_records_path as c; "
        "print(c.change_records_path())" % str(link / "scripts" / "shared")
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == str(crp.change_records_path())


def test_cli_prints_exact_path():
    r = subprocess.run([sys.executable, str(SHARED / "change_records_path.py")], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout == str(crp.change_records_path()) + "\n"
    assert r.stderr == ""


def test_import_has_no_side_effects():
    code = (
        "import sys, builtins; sys.path.insert(0, %r)\n"
        "calls = []\n"
        "orig = builtins.open\n"
        "builtins.open = lambda *a, **k: (calls.append(a), orig(*a, **k))[1]\n"
        "import change_records_path\n"
        "assert not calls, calls\n"
    ) % str(SHARED)
    before = set(os.listdir(REPO))
    folder_existed = (REPO / "attribution" / "change-records").exists()
    subprocess.run([sys.executable, "-c", code], check=True)
    assert set(os.listdir(REPO)) == before
    assert (REPO / "attribution" / "change-records").exists() == folder_existed
