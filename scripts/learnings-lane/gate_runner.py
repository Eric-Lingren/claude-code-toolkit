#!/usr/bin/env python3
# gate_runner.py: layered lane gate runner; any failing layer yields a blocked draft verdict
# What: run_gate(layers, retries) runs ordered layers (name, callable -> {ok, log, infra}); returns {verdict: ready|blocked, draft, labels, failing_layer, pr_body, attempts, layers}
# When: after lane_gate.py deterministic checks, before the lane PR is opened; infra failures retry once, gate failures never; layers are injectable (stubs in tests)
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
REPO = SHARED.parent.parent
INFRA_RETRIES = 1


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


procgroup = _load("gate_procgroup", HERE / "procgroup.py")
config = _load("lane_config", HERE / "config.py")


def snapshot(results):
    """ci_snapshot format: the run-all-tests.sh JSON shape, as ci-snapshot-diff.py reads it."""
    passed = sum(1 for s in results.values() if s == "pass")
    return {"results": dict(results),
            "totals": {"passed": passed, "failed": len(results) - passed, "total": len(results)}}


def snapshot_diff(base_results, head_results):
    """Reuses ci-snapshot-diff.py compare() output as is."""
    mod = _load("ci_snapshot_diff", SHARED / "attribution" / "ci-snapshot-diff.py")
    return mod.compare(base_results, head_results)


def tests_layer(run_base, run_head):
    """Layer from injectable suite runners returning {file: pass|fail}; fails on newly failing files."""
    def layer():
        try:
            base, head = run_base(), run_head()
        except Exception as e:  # tool crash / timeout
            return {"ok": False, "infra": True, "log": f"suite runner crashed: {e}"}
        diff = snapshot_diff(base, head)
        failing = diff["newly_failing"]
        return {"ok": not failing, "infra": False,
                "snapshot": {"base": snapshot(base), "head": snapshot(head)}, "diff": diff,
                "log": ("newly failing: " + ", ".join(failing)) if failing else "no newly failing tests"}
    return layer


def review_layer(review, effort="medium"):
    """pr-code-review at `effort`; review(effort) -> list of findings {severity, message}. Blocking = severity 'blocking'."""
    def layer():
        try:
            findings = review(effort)
        except Exception as e:
            return {"ok": False, "infra": True, "log": f"review crashed: {e}"}
        blocking = [f for f in findings if f.get("severity") == "blocking"]
        return {"ok": not blocking, "infra": False, "findings": findings,
                "log": "\n".join(str(f.get("message", f)) for f in blocking) or "no blocking findings"}
    return layer


def smoke_layer(root=REPO, timeout=config.SMOKE_TIMEOUT_SECONDS):
    def layer():
        try:
            p = procgroup.run_killpg(
                [sys.executable, str(SHARED / "shared" / "smoke-test.py"), str(root)], cwd=root,
                text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"ok": False, "infra": True, "log": f"smoke test did not run: {e}"}
        return {"ok": p.returncode == 0, "infra": False, "log": (p.stdout + p.stderr).strip()}
    return layer


def _run_layer(fn, retries):
    attempts = 0
    while True:
        attempts += 1
        res = fn()
        if res.get("ok") or not res.get("infra") or attempts > retries:
            return res, attempts


def run_gate(layers, retries=INFRA_RETRIES):
    """layers: ordered list of (name, fn). Stops at first failing layer."""
    out, attempts = [], {}
    for name, fn in layers:
        res, n = _run_layer(fn, retries)
        attempts[name] = n
        out.append({"name": name, "ok": bool(res.get("ok")), "infra": bool(res.get("infra")), "log": res.get("log", "")})
        if not res.get("ok"):
            kind = "infra failure after retry" if res.get("infra") else "gate failure"
            body = f"Lane gate blocked at layer `{name}` ({kind}).\n\n```\n{res.get('log', '')}\n```"
            return {"verdict": "blocked", "draft": True, "labels": ["blocked"], "failing_layer": name,
                    "pr_body": body, "attempts": attempts, "layers": out}
    return {"verdict": "ready", "draft": False, "labels": [], "failing_layer": None,
            "pr_body": "", "attempts": attempts, "layers": out}


if __name__ == "__main__":
    print(json.dumps(run_gate([("smoke", smoke_layer())])))
