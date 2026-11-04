#!/usr/bin/env python3
# pr_opener.py: open one lane PR per target skill from the headless branch, using the gate verdict
# What: open_pr(target, run_file, details, gate, gh=, base=) builds a structured body (per cluster: problem, anchor quotes, why this fix, diff summary; plus dropped stale/invalid learnings) and opens the PR via gh
# When: after gate_runner verdict; gate verdict ready -> normal PR, blocked -> draft + "blocked" label, needs-grill -> draft + "needs-grill" label
# Test hook: gh is an injectable callable gh(args: list[str]) -> str (stdout). Tests never call GitHub.
# Merge method: GitHub has no per-PR merge-method preset (chosen at merge time via `gh pr merge --rebase` or
#   auto-merge's mergeMethod). So the body states "Rebase merge", and a gate check refuses when the repo disallows it.
import importlib.util
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
MERGE_METHOD_LINE = "**Merge method: rebase merge** (one commit per cluster). Do not squash or merge-commit."
DROPPED = ("stale", "invalid")


def _cfg():
    spec = importlib.util.spec_from_file_location("lane_config", HERE / "config.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PrRefused(Exception):
    pass


def default_gh(args, cwd=None):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def count_open_lane_prs(gh, label):
    out = gh(["pr", "list", "--label", label, "--state", "open", "--json", "number", "--limit", str(_cfg().GH_LIST_LIMIT_OPEN_PRS)])
    return len(json.loads(out or "[]"))


def find_open_pr(gh, branch):
    """URL of an open PR whose head is branch, or None."""
    out = gh(["pr", "list", "--head", branch, "--state", "open", "--json", "url", "--limit", "1"])
    prs = json.loads(out or "[]")
    return prs[0]["url"] if prs else None


def rebase_allowed(gh):
    out = gh(["repo", "view", "--json", "rebaseMergeAllowed"])
    return bool(json.loads(out).get("rebaseMergeAllowed"))


def build_body(target, run_file, details, gate):
    lines = [f"## Learnings lane: `{target}`", "", MERGE_METHOD_LINE, ""]
    if gate.get("pr_body"):
        lines += [gate["pr_body"], ""]
    for c in run_file.get("clusters", []):
        d = details.get(c["id"], {})
        lines += [f"### Cluster {c['id']} ({c['status']})", "",
                  f"**Problem:** {d.get('problem', '(none given)')}", "", "**Evidence:**"]
        lines += [f"> {q}" for q in d.get("anchor_quotes", [])] or ["> (none)"]
        lines += ["", f"**Why this fix:** {d.get('fix_rationale', '(none given)')}", "",
                  f"**What the diff does:** {d.get('diff_summary', '(none given)')}", ""]
        if c.get("commit"):
            lines += [f"Commit: `{c['commit']}`", ""]
        if c.get("reasons"):
            lines += ["Needs grill: " + "; ".join(c["reasons"]), ""]
    dropped = [v for v in run_file.get("verdicts", []) if v.get("verdict") in DROPPED]
    lines += ["### Dropped learnings", ""]
    lines += [f"- `{v['learning_id']}` ({v['verdict']}, cluster {v['cluster_id']}): {v['reason']}" for v in dropped] \
        or ["None."]
    return "\n".join(lines) + "\n"


def pr_flags(gate):
    """(draft, labels) from the gate verdict: ready, blocked, or needs-grill."""
    v = gate.get("verdict")
    if v == "ready":
        return False, []
    if v in ("blocked", "needs-grill"):
        return True, [v]
    raise ValueError(f"unknown gate verdict: {v!r}")


def open_pr(target, run_file, details, gate, gh=default_gh, base="main"):
    cfg = _cfg()
    if count_open_lane_prs(gh, cfg.LANE_LABEL) >= cfg.OPEN_PR_CAP:
        raise PrRefused(f"{cfg.OPEN_PR_CAP} lane PRs already open")
    if not rebase_allowed(gh):
        raise PrRefused("repo does not allow rebase merge; lane PRs need it")
    draft, labels = pr_flags(gate)
    args = ["pr", "create", "--base", base, "--head", cfg.LANE_BRANCH_PREFIX + target,
            "--title", f"Learnings lane: {target}", "--body", build_body(target, run_file, details, gate)]
    for lab in [cfg.LANE_LABEL, *labels]:
        args += ["--label", lab]
    if draft:
        args.append("--draft")
    return gh(args).strip()
