"""Deterministic pieces of the iterate stage: pick, apply/revert, keep rule, history. No LLM, no live runs."""
import json
import math
import os
import subprocess

import bench_lib as L
import grade

MAX_TARGETS = 3
MAX_SENTINELS = 2
TOKEN_RATIO_MAX = 1.5


def agent_dir(agent):
    return os.path.join(L.bench_dir(), agent)


def state_path(agent):
    return os.path.join(agent_dir(agent), "iterations.json")


def load_state(agent):
    return L.read_json(state_path(agent), {"agent": agent, "iterations": [], "stuck": []})


def save_state(agent, st):
    L.write_json(state_path(agent), st)


def load_contract(agent):
    p = os.path.join(L.evals_dir(), agent, "contract.json")
    if not os.path.exists(p):
        raise SystemExit(f"no frozen contract at {p}; run the contract stage first")
    return L.read_json(p)


def load_traces(d, rep=1):
    """case_id -> record (one rep) for a traces dir."""
    if not os.path.isdir(d):
        return {}
    out = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith(f"_rep{rep}.json"):
            r = L.read_json(os.path.join(d, fn))
            out[r["case_id"]] = r
    return out


def results(contract, recs):
    """{check_id: {case_id: (True|False|None, detail)}}"""
    return {c["id"]: {cid: grade.check(c, r) for cid, r in recs.items()} for c in contract["checks"]}


def tokens(rec):
    u = rec.get("usage") or {}
    return sum(u.get(k, 0) for k in ("input", "output", "cache_read", "cache_creation"))


def pick(contract, base_recs, stuck):
    """Worst failing check (lowest pass rate, then most failures, then contract order), skipping stuck checks."""
    res = results(contract, base_recs)
    rows = []
    for i, c in enumerate(contract["checks"]):
        r = res[c["id"]]
        fails = [cid for cid, (ok, _) in r.items() if ok is False]
        tot = len([1 for ok, _ in r.values() if ok is not None])
        if not fails or c["id"] in stuck:
            continue
        rows.append((len(fails) and (tot - len(fails)) / tot, -len(fails), i, c, fails))
    if not rows:
        return None
    rows.sort(key=lambda x: x[:3])
    _, _, _, check, fails = rows[0]
    targets = fails[:MAX_TARGETS]
    sentinels = [cid for cid in base_recs
                 if cid not in fails and all(res[c["id"]][cid][0] is not False for c in contract["checks"])
                 ][:MAX_SENTINELS]
    return {"check": check, "failing_cases": fails, "targets": targets, "sentinels": sentinels,
            "details": {cid: res[check["id"]][cid][1] for cid in targets}}


# ---- fix application ---------------------------------------------------------------------------

def allowed_edit_path(agent, rel):
    """rel is relative to claude-code-shared/. The agent's own file, or a shared script (never the bench harness)."""
    n = os.path.normpath(rel)
    if n.startswith("..") or os.path.isabs(n):
        return False
    if os.path.basename(n) == "log-learning.py":
        return False  # replaced by a sandbox shim in live runs, an edit would never be tested
    if n.startswith("agents" + os.sep) and n.endswith(os.sep + agent + ".md") or n == os.path.join("agents", agent + ".md"):
        return True
    return n.startswith("scripts" + os.sep) and not n.startswith(os.path.join("scripts", "agent-eval")) \
        and n.endswith((".py", ".sh", ".mjs"))


def apply_edits(agent, edits):
    """Exact find/replace, each find must match exactly once. Returns repo-root-relative files touched."""
    root = L.repo_root()
    staged = {}
    for e in edits:
        rel = e.get("file", "")
        if not allowed_edit_path(agent, rel):
            raise SystemExit(f"edit refused: {rel!r} is not the agent file or a shared script")
        p = os.path.join(L.SHARED_DIR, rel)
        txt = staged.get(p) or open(p, encoding="utf-8").read()
        n = txt.count(e.get("find", "\0"))
        if n != 1 or not e.get("find"):
            raise SystemExit(f"edit refused: find text matches {n} times in {rel} (need exactly 1)")
        staged[p] = txt.replace(e["find"], e.get("replace", ""), 1)
    for p, txt in staged.items():
        open(p, "w", encoding="utf-8").write(txt)
    return sorted(os.path.relpath(p, root) for p in staged)


def revert_files(files):
    """Restore the files to HEAD and prove the tree is clean for them. Raises if anything is left."""
    root = L.repo_root()
    if files:
        subprocess.run(["git", "checkout", "--"] + files, cwd=root, check=True)
    left = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no", "--"] + (files or ["."]),
                                   text=True, cwd=root).strip()
    if left:
        raise SystemExit(f"revert left changes behind:\n{left}")
    return True


def dirty(paths):
    root = L.repo_root()
    return subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no", "--"] + paths,
                                   text=True, cwd=root).strip()


# ---- keep rule ------------------------------------------------------------------------------------

def decide(contract, check_id, targets, sentinels, base, run1, run2, approve_tokens=False):
    """Keep only if: >= half of targets pass the check and again on run2 (2 of 2), no sentinel regresses on any
    check it passed at baseline, tokens/case (targets+sentinels, run1) <= 1.5x baseline. Returns a dict."""
    check = next(c for c in contract["checks"] if c["id"] == check_id)
    per_case = {}
    confirmed, flipped = [], []
    for cid in targets:
        r1 = run1.get(cid)
        ok1 = grade.check(check, r1)[0] if r1 else None
        r2 = run2.get(cid)
        ok2 = grade.check(check, r2)[0] if r2 else None
        if ok1:
            flipped.append(cid)
        if ok1 and ok2:
            confirmed.append(cid)
        per_case[cid] = {"role": "target", "run1": ok1, "run2": ok2}
    regressions = []
    for cid in sentinels:
        r = run1.get(cid)
        if r is None:
            regressions.append({"case": cid, "check": "*", "detail": "no trace (run did not complete), cannot verify"})
        else:
            for c in contract["checks"]:
                was = grade.check(c, base[cid])[0]
                now_ok, detail = grade.check(c, r)
                if was is not False and now_ok is False:
                    regressions.append({"case": cid, "check": c["id"], "detail": detail})
        per_case[cid] = {"role": "sentinel", "regressed": [x["check"] for x in regressions if x["case"] == cid]}
    ids = [c for c in list(targets) + list(sentinels) if c in run1 and c in base]
    t_new = sum(tokens(run1[c]) for c in ids) / len(ids) if ids else 0
    t_old = sum(tokens(base[c]) for c in ids) / len(ids) if ids else 0
    ratio = (t_new / t_old) if t_old else None
    need = math.ceil(len(targets) / 2)
    reasons = []
    if len(confirmed) < need:
        reasons.append(f"only {len(confirmed)}/{len(targets)} targeted cases pass 2 of 2 (need {need})")
    if regressions:
        reasons.append("sentinel regression: " + "; ".join(f"{x['case']}/{x['check']}" for x in regressions))
    if ratio is not None and ratio > TOKEN_RATIO_MAX and not approve_tokens:
        reasons.append(f"tokens per case {ratio:.2f}x baseline (> {TOKEN_RATIO_MAX}x) and not approved")
    return {"decision": "revert" if reasons else "keep", "reasons": reasons, "check": check_id,
            "targets": targets, "sentinels": sentinels, "flipped_run1": flipped, "confirmed_2of2": confirmed,
            "regressions": regressions, "tokens_per_case": {"baseline": round(t_old), "candidate": round(t_new),
                                                           "ratio": round(ratio, 3) if ratio else None},
            "per_case": per_case}


def rates(contract, recs):
    out = {}
    for cid_, per in results(contract, recs).items():
        p = sum(1 for ok, _ in per.values() if ok)
        t = sum(1 for ok, _ in per.values() if ok is not None)
        out[cid_] = {"pass": p, "total": t}
    return out


def consecutive_reverts(st, check_id):
    n = 0
    for it in reversed(st["iterations"]):
        if it["check"] != check_id:
            break
        if it["status"] != "reverted":
            break
        n += 1
    return n
