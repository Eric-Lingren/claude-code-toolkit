"""run_killpg: subprocess.run replacement that owns a process group and kills the whole group on timeout."""
import os
import signal
import subprocess


def run_killpg(cmd, input=None, timeout=None, **kw):
    """Like subprocess.run(capture_output=True) but the child leads its own session. On timeout the whole
    group (grandchildren such as claude -p) is SIGKILLed before TimeoutExpired is re-raised."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else None, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True, **kw)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        p.communicate()
        raise
    except BaseException:
        _kill(p)
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        p.kill()
