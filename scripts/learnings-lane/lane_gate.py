#!/usr/bin/env python3
# lane_gate.py: deterministic checks on a learnings-lane branch diff
# What: evaluate(files, commit_messages) -> verdict dict {verdict, failing_check, draft, labels, issue_payload, checks}; CLI prints it as JSON
# When: run by the lane PR gate: lane_gate.py --base <sha> [--head HEAD]; exit 0 pass, 1 fail/needs-grill, 2 error
import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_cspec = importlib.util.spec_from_file_location("lane_config", HERE / "config.py")
config = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(config)
UNIFIED = "claude-code-shared/learnings/unified-learnings.jsonl"
TRAILER_RE = re.compile(r"^Learning-Ids:\s*\S+", re.M)
CONTRACT_RE = re.compile(r"(^claude-code-shared/contracts/)|(-contract\.md$)|(-schema\.json$)|(contract\.json$)")


def _load_protected():
    spec = importlib.util.spec_from_file_location(
        "check_protected_paths", HERE.parent / "protected-paths" / "check-protected-paths.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def protected_hits(files, list_file=None):
    mod = _load_protected()
    patterns = mod.load_patterns(list_file or mod.DEFAULT_LIST)
    return [f for f in files if mod.is_protected(f, patterns)]


def is_contract(path):
    return bool(CONTRACT_RE.search(path))


def default_promote(payload):
    """Injectable issue promotion; default only returns the payload (no network)."""
    return payload


def evaluate(files, commit_messages, promote=default_promote, protected_list=None):
    files = [f.strip().removeprefix("./") for f in files if f.strip()]
    checks = []

    def result(verdict, failing=None, **extra):
        out = {"verdict": verdict, "failing_check": failing, "draft": False,
               "labels": [], "issue_payload": None, "checks": checks, "files": files}
        out.update(extra)
        return out

    # 1: unified-learnings.jsonl must not be in the diff
    if UNIFIED in files:
        checks.append({"name": "no-unified-learnings", "ok": False})
        return result("fail", "no-unified-learnings")
    checks.append({"name": "no-unified-learnings", "ok": True})

    # 2: every commit has a Learning-Ids trailer
    missing = [i for i, m in enumerate(commit_messages) if not TRAILER_RE.search(m)]
    if missing or not commit_messages:
        checks.append({"name": "learning-ids-trailer", "ok": False, "commits_missing": missing})
        return result("fail", "learning-ids-trailer")
    checks.append({"name": "learning-ids-trailer", "ok": True})

    # 3: protected paths -> draft needs-grill
    hits = protected_hits(files, protected_list)
    if hits:
        checks.append({"name": "protected-paths", "ok": False, "hits": hits})
        return result("needs-grill", "protected-paths", draft=True, labels=["needs-grill"])
    checks.append({"name": "protected-paths", "ok": True})

    # 4: size / contract -> needs-grill + issue
    contracts = [f for f in files if is_contract(f)]
    if len(files) > config.MAX_LANE_FILES or contracts:
        name = "contract-change" if contracts else "fix-size"
        checks.append({"name": name, "ok": False})
        payload = {
            "title": "Learnings lane fix needs grilling: " + name,
            "labels": ["needs-grill"],
            "body": f"Lane fix touches {len(files)} file(s); contracts: {contracts or 'none'}.\n\n"
                    + "\n".join(f"- {f}" for f in files),
        }
        payload = promote(payload) or payload
        return result("needs-grill", name, labels=["needs-grill"], issue_payload=payload)
    checks.append({"name": "fix-size", "ok": True})
    return result("pass")


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    a = ap.parse_args(argv)
    try:
        files = _git("diff", "--name-only", f"{a.base}...{a.head}").splitlines()
        shas = _git("rev-list", f"{a.base}..{a.head}").split()
        msgs = [_git("log", "-1", "--format=%B", s) for s in shas]
    except subprocess.CalledProcessError as e:
        print(json.dumps({"verdict": "error", "failing_check": None, "error": e.stderr.strip()}))
        return 2
    v = evaluate(files, msgs)
    print(json.dumps(v))
    return 0 if v["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
