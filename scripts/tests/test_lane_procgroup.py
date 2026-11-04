"""run_killpg kills grandchildren on timeout."""
import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / "learnings-lane" / "procgroup.py"
spec = importlib.util.spec_from_file_location("procgroup", P)
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_timeout_kills_grandchild(tmp_path):
    pidfile = tmp_path / "gc.pid"
    cmd = f"sleep 30 & echo $! > {pidfile}; wait"
    with pytest.raises(subprocess.TimeoutExpired):
        pg.run_killpg(cmd, shell=True, text=True, timeout=1)
    gc = int(pidfile.read_text())
    for _ in range(20):
        if not _alive(gc):
            break
        time.sleep(0.1)
    assert not _alive(gc)


def test_ok_and_input():
    p = pg.run_killpg(["cat"], input="hi", text=True, timeout=5)
    assert p.returncode == 0 and p.stdout == "hi"
