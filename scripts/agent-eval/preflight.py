#!/usr/bin/env python3
"""preflight.py <agent> — start-of-run checks for improve-agent-benchmarks. Zero tokens, no model calls.
preflight.py approve <agent>   accept hand edits to cited files after the user reviewed them
preflight.py gate <agent>      silent when live runs are allowed; prints the reason and exits 3 when blocked

Run at the start of every invocation for an agent, before any stage work. Steps:
  0. Harvest the agent's recorded spawns into <bench>/<agent>/history/ (same parser as harvest.py).
  1. Confirmation. For each kept fix (iterations.json, status kept, with sha) that is on main
     (origin/main, else main; override with AGENT_BENCH_MAIN_REF), grade the spawns made after the
     fix commit with the history-gradable checks (shape: json_parse/schema/no_prose, quote:
     quote_in_input, side effect: tool_called/tool_not_called/file_written). Verdict per fix:
       confirmed    >= 2 post-fix spawns and the fixed check never fails after the fix
       regressed    fixed check's pass rate fell below its pre-fix rate, or a check that passed every
                    pre-fix spawn fails after the fix. Prints the exact `git revert <sha>`.
       unconfirmed  anything else (too few spawns, not on main yet, still failing but not worse)
     Role checks (verdict_equals, proposed types) are not gradable from history and are labeled so.
  2. Staleness. Every cited file's sha256 is compared with contract.json's fingerprint. A changed file
     is fine when every commit since the fingerprinted state was made by this skill (subject starts
     "agent-bench("). Anything else (uncommitted edit, hand commit, unknown state) is a hand edit:
     only the checks that cite it are re-run (citation, before/after historical pass rate, file diff)
     and live runs are BLOCKED (exit 3, and iterate.py refuses) until `approve`.
  3. End state. Contract checks all passing on planted cases (baseline traces) and on history, no
     pending stale edit and no regressed fix => queue entry gets end_state (rank.py drops it to the
     bottom). It reopens on a cited-file change, a regressed fix, or production failures. History here
     is only spawns made since the agent file's last commit (bench_lib.current_history); older spawns
     ran a different prompt.

State: <bench>/<agent>/preflight.json (audit log), queue.json end_state. Env: AGENT_BENCH_DIR,
AGENT_EVALS_DIR (bench_lib), HOME (where recorded spawns are read), AGENT_BENCH_MAIN_REF.
"""
import difflib
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import bench_lib as L
import harvest

GRADABLE = {"json_parse", "schema", "no_prose", "quote_in_input", "tool_called", "tool_not_called", "file_written"}
FAMILY = {"json_parse": "shape", "schema": "shape", "no_prose": "shape", "quote_in_input": "quote",
          "tool_called": "side effect", "tool_not_called": "side effect", "file_written": "side effect"}
SKILL_PREFIX = "agent-bench("
MIN_CONFIRM = 2
WALK_CAP = 200


def git(*args, check=False, raw=False):
    r = subprocess.run(["git", *args], cwd=L.repo_root(), capture_output=True, text=not raw)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr}")
    return r


# ---- harvest ------------------------------------------------------------------------------------

def harvest_history(agent):
    out = os.path.join(L.bench_dir(), agent, "history")
    os.makedirs(out, exist_ok=True)
    n = 0
    home = os.path.expanduser("~")
    for cfg in (".cch", ".cco"):
        for mp in sorted(glob.glob(os.path.join(home, cfg, "projects", "*", "*", "subagents", "agent-*.meta.json"))):
            try:
                meta = json.load(open(mp))
            except ValueError:
                continue
            jp = mp[: -len(".meta.json")] + ".jsonl"
            if meta.get("agentType") != agent or not os.path.exists(jp):
                continue
            rec = harvest.parse(jp, meta)
            if rec["spawn_prompt"] is None:
                continue
            aid = os.path.basename(jp)[len("agent-"):-len(".jsonl")]
            rec.update({"agent": agent, "agent_id": aid, "config_dir": cfg, "source_path": jp,
                        "session_id": rec["session_id"] or os.path.basename(os.path.dirname(os.path.dirname(jp)))})
            with open(os.path.join(out, aid + ".json"), "w") as fh:
                json.dump(rec, fh, indent=1)
            n += 1
    return n


# ---- helpers --------------------------------------------------------------------------------------

def parse_ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def contract_path(agent):
    return os.path.join(L.evals_dir(), agent, "contract.json")


def state_path(agent):
    return os.path.join(L.bench_dir(), agent, "preflight.json")


def load_state(agent):
    return L.read_json(state_path(agent), {"agent": agent, "approvals": [], "confirmations": []})


def rate_str(p, f):
    return f"{p}/{p + f}" if p + f else "n/a"


def check_stats(check, recs):
    p = f = 0
    for r in recs:
        res, _ = L.grade.check(check, r)
        if res is True:
            p += 1
        elif res is False:
            f += 1
    return p, f


# ---- staleness ------------------------------------------------------------------------------------

def classify(rel, fp):
    """-> {state: unchanged|skill|manual, reason, base_commit}. rel is repo-root relative."""
    root = L.repo_root()
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return {"state": "manual", "reason": "cited file is gone", "base_commit": None}
    if L.sha256_file(path) == fp:
        return {"state": "unchanged"}
    if git("status", "--porcelain", "--untracked-files=no", "--", rel).stdout.strip():
        return {"state": "manual", "reason": "uncommitted edit", "base_commit": _fp_commit(rel, fp)}
    base, later = _walk(rel, fp)
    if base is None:
        return {"state": "manual", "reason": "fingerprinted state not found in history", "base_commit": None}
    hand = [c for c in later if not c[1].startswith(SKILL_PREFIX)]
    if hand:
        return {"state": "manual", "reason": "hand commit(s): " + ", ".join(f"{c[0][:7]} {c[1][:50]}" for c in hand),
                "base_commit": base}
    return {"state": "skill", "reason": f"{len(later)} commit(s) by this skill: " +
            ", ".join(c[0][:7] for c in later), "base_commit": base}


def _walk(rel, fp):
    """Commits touching rel newest first until the blob hash equals fp. -> (base_sha|None, [(sha, subject)] newer)."""
    import hashlib
    log = git("log", f"-n{WALK_CAP}", "--format=%H%x09%s", "--", rel).stdout.splitlines()
    later = []
    for ln in log:
        sha, _, subj = ln.partition("\t")
        blob = git("show", f"{sha}:{rel}", raw=True)
        if blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == fp:
            return sha, later
        later.append((sha, subj))
    return None, later


def _fp_commit(rel, fp):
    return _walk(rel, fp)[0]


def stale_files(agent, contract):
    """{file(relative to shared): classification} for every changed fingerprint."""
    root = L.repo_root()
    out = {}
    for f, fp in (contract.get("fingerprints") or {}).items():
        p = L.cited_path(f)
        rel = os.path.relpath(p, root) if p else f
        c = classify(rel, fp)
        c["rel"] = rel
        if c["state"] != "unchanged":
            out[f] = c
    return out


def manual_files(agent, contract):
    return {f: c for f, c in stale_files(agent, contract).items() if c["state"] == "manual"}


def gate(agent):
    """Block live runs on unapproved hand edits to cited files. Returns the list of blocked files."""
    c = L.read_json(contract_path(agent))
    return sorted(manual_files(agent, c)) if c else []


def report_stale(agent, contract, recs, stale):
    base = L.read_json(os.path.join(L.evals_dir(), agent, "baseline.json"), {"checks": {}})
    manual = {f: c for f, c in stale.items() if c["state"] == "manual"}
    for f, c in stale.items():
        if c["state"] == "skill":
            print(f"  ok   {f}: changed by this skill only ({c['reason']}), no review needed")
    if not manual:
        return
    print("STALE CONTRACT: cited file(s) changed by hand. Re-running only the checks that cite them.")
    for f, c in manual.items():
        print(f"  file {f}: {c['reason']}")
        old = git("show", f"{c['base_commit']}:{c['rel']}").stdout if c.get("base_commit") else None
        p = L.cited_path(f)
        new = open(p, encoding="utf-8", errors="replace").read() if p and os.path.exists(p) else ""
        if old is not None:
            d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1))
            print("    diff (before = frozen state):")
            for ln in d[:40]:
                print("      " + ln)
            if len(d) > 40:
                print(f"      ... {len(d) - 40} more diff lines")
        for ck in contract["checks"]:
            if ck["source"]["file"] != f:
                continue
            why = L.validate_source(ck["source"])
            b = base["checks"].get(ck["id"], {})
            p_, f_ = check_stats(ck, recs)
            print(f"    check {ck['id']} [{ck['type']}] cites {f}:{ck['source']['line']}")
            print(f"      citation now: {'still valid' if not why else 'BROKEN: ' + why}")
            print(f"      historical pass rate before (frozen baseline): {rate_str(b.get('pass', 0), b.get('fail', 0))}"
                  f"   after (current history, {len(recs)} spawns): {rate_str(p_, f_)}")
    print("  Review the above with the user. Nothing live runs until: preflight.py approve " + agent)


def cmd_approve(agent):
    contract = L.read_json(contract_path(agent))
    if not contract:
        sys.exit(f"no frozen contract for {agent}")
    manual = manual_files(agent, contract)
    if not manual:
        print("nothing to approve: no hand-edited cited files")
        return
    bad = []
    for ck in contract["checks"]:
        if ck["source"]["file"] in manual:
            why = L.validate_source(ck["source"])
            if why:
                bad.append(f"{ck['id']}: {why}")
    if bad:
        sys.exit("refusing: citation(s) broken by the edit, fix the contract first:\n  " + "\n  ".join(bad))
    for f in manual:
        contract["fingerprints"][f] = L.sha256_file(L.cited_path(f))
    L.write_json(contract_path(agent), contract)
    st = load_state(agent)
    st["approvals"].append({"ts": L.now(), "files": sorted(manual),
                            "checks": [c["id"] for c in contract["checks"] if c["source"]["file"] in manual]})
    L.write_json(state_path(agent), st)
    print(f"approved: refreshed {len(manual)} fingerprint(s) in {contract_path(agent)}; commit contract.json. Live runs allowed.")


# ---- confirmation ---------------------------------------------------------------------------------

def main_ref():
    cand = [os.environ["AGENT_BENCH_MAIN_REF"]] if os.environ.get("AGENT_BENCH_MAIN_REF") else ["origin/main", "main"]
    for r in cand:
        if git("rev-parse", "--verify", "-q", r + "^{commit}").returncode == 0:
            return r
    return None


def confirm_fixes(agent, contract, recs):
    st = L.read_json(os.path.join(L.bench_dir(), agent, "iterations.json"), {"iterations": []})
    ref = main_ref()
    out = []
    for it in st["iterations"]:
        if it.get("status") != "kept" or not it.get("sha"):
            continue
        sha, cid = it["sha"], it["check"]
        row = {"n": it["n"], "check": cid, "sha": sha}
        check = next((c for c in contract["checks"] if c["id"] == cid), None)
        if check is None:
            row.update(verdict="unconfirmed", why="fixed check no longer in the contract")
        elif check["type"] not in GRADABLE:
            row.update(verdict="unconfirmed", why=f"role check ({check['type']}): not confirmable from production history")
        elif not ref or git("merge-base", "--is-ancestor", sha, ref).returncode != 0:
            row.update(verdict="unconfirmed", why=f"{sha[:7]} is not on {ref or 'main'} yet, nothing is live")
        else:
            live = parse_ts(git("show", "-s", "--format=%cI", sha).stdout.strip())
            pre = [r for r in recs if (t := parse_ts(r.get("timestamp"))) and t < live]
            post = [r for r in recs if (t := parse_ts(r.get("timestamp"))) and t >= live]
            pp, pf = check_stats(check, pre)
            qp, qf = check_stats(check, post)
            fam = FAMILY[check["type"]]
            row.update(live_at=live.strftime("%Y-%m-%dT%H:%M:%SZ"), post_spawns=len(post),
                       pre=rate_str(pp, pf), post=rate_str(qp, qf), family=fam)
            others = []
            for c in contract["checks"]:
                if c["id"] == cid or c["type"] not in GRADABLE:
                    continue
                a, b = check_stats(c, pre)
                x, y = check_stats(c, post)
                if a > MIN_CONFIRM and not b and y:
                    others.append(f"{c['id']} now fails {y}/{x + y} (passed every pre-fix spawn)")
            pre_rate = pp / (pp + pf) if pp + pf else None
            post_rate = qp / (qp + qf) if qp + qf else None
            if post_rate is not None and ((pre_rate is not None and post_rate < pre_rate) or others):
                bits = []
                if pre_rate is not None and post_rate < pre_rate:
                    bits.append(f"{cid} fell from {row['pre']} to {row['post']}")
                row.update(verdict="regressed", why="; ".join(bits + others), revert=f"git revert {sha}")
            elif qp + qf >= MIN_CONFIRM and qf == 0:
                row.update(verdict="confirmed", why=f"{cid} passes {row['post']} post-fix spawns ({fam} check; was {row['pre']} before)")
            elif qp + qf < MIN_CONFIRM:
                row.update(verdict="unconfirmed", why=f"{len(post)} post-fix spawn(s), {qp + qf} gradable by {cid}; need {MIN_CONFIRM}")
            else:
                row.update(verdict="unconfirmed", why=f"{cid} still fails in {qf} of {qp + qf} post-fix spawns (not worse than before: {row['pre']})")
        out.append(row)
    return out


def report_confirm(rows):
    if not rows:
        print("CONFIRMATION: no kept fixes recorded yet.")
        return
    print("CONFIRMATION: real-world check of kept fixes (post-fix production spawns, history-gradable checks only)")
    for r in rows:
        print(f"  fix #{r['n']} {r['check']} ({r['sha'][:7]}): {r['verdict'].upper()}: {r['why']}")
        if r.get("revert"):
            print(f"    revert with: {r['revert']}")


# ---- end state ------------------------------------------------------------------------------------

def end_state_problems(agent, contract, recs, manual, confirm_rows):
    """[] when the agent is at its end state, else the reasons it is not (first is the reopen reason)."""
    probs = []
    if manual:
        probs.append("cited file changed by hand: " + ", ".join(sorted(manual)))
    reg = [r for r in confirm_rows if r["verdict"] == "regressed"]
    if reg:
        probs.append("fix regressed: " + ", ".join(f"#{r['n']} {r['check']}" for r in reg))
    failed = sum(any(L.grade.check(c, r)[0] is False for c in contract["checks"]) for r in recs)
    if not recs:
        probs.append("no production spawns since the agent file last changed, nothing to confirm passing")
    elif failed:
        probs.append(f"production failures: {failed} of {len(recs)} spawns fail a check (failure rate {failed / len(recs):.1%})")
    cases = [json.loads(ln) for ln in open(os.path.join(L.evals_dir(), agent, "cases.jsonl"))] \
        if os.path.exists(os.path.join(L.evals_dir(), agent, "cases.jsonl")) else []
    planted = [c["id"] for c in cases if c.get("kind") == "planted"]
    if planted:
        traces = {}
        d = os.path.join(L.bench_dir(), agent, "baseline")
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith("_rep1.json"):
                    r = L.read_json(os.path.join(d, fn))
                    traces[r["case_id"]] = r
        run = [c for c in planted if c in traces]
        if not run:
            probs.append("planted cases have no live trace yet")
        for c in run:
            bad = [k["id"] for k in contract["checks"] if L.grade.check(k, traces[c])[0] is False]
            if bad:
                probs.append(f"planted case {c} fails {', '.join(bad)}")
    return probs


def update_end_state(agent, contract, recs, stale, confirm_rows):
    manual = {f: c for f, c in stale.items() if c["state"] == "manual"}
    probs = end_state_problems(agent, contract, recs, manual, confirm_rows)
    q = L.load_queue()
    e = q["agents"].get(agent)
    if e is None:
        print("END STATE: agent not in queue, skipped")
        return
    was = e.get("end_state")
    if not probs:
        if not was:
            e["end_state"] = {"since": L.now(), "history_spawns": len(recs)}
            e.pop("reopened", None)
            L.write_json(L.queue_path(), q)
        print("END STATE: all checks pass on planted cases and history; agent drops to the bottom of the queue")
    else:
        if was:
            e.pop("end_state")
            e["reopened"] = {"at": L.now(), "reason": probs[0]}
            L.write_json(L.queue_path(), q)
            print(f"END STATE: REOPENED: {probs[0]}")
        else:
            print("END STATE: not reached: " + "; ".join(probs))


def still_ended(agent):
    """For rank.py: an end-state agent stays at the bottom only while no cited file was hand-edited."""
    c = L.read_json(contract_path(agent))
    return bool(c) and not manual_files(agent, c)


# ---- main -----------------------------------------------------------------------------------------

def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "approve":
        return cmd_approve(a[1])
    if len(a) == 2 and a[0] == "gate":
        blocked = gate(a[1])
        if blocked:
            print("BLOCKED: hand-edited cited file(s) not approved: " + ", ".join(blocked) +
                  f". Review, then: preflight.py approve {a[1]}", file=sys.stderr)
            sys.exit(3)
        return
    if len(a) != 1 or a[0].startswith("-"):
        sys.exit(__doc__)
    agent = a[0]
    n = harvest_history(agent)
    recs = L.load_history(agent)
    print(f"PREFLIGHT {agent}: {n} recorded spawns harvested")
    contract = L.read_json(contract_path(agent))
    if not contract:
        print("no frozen contract yet: confirmation, staleness and end-state checks do not apply")
        print("LIVE_RUNS: allowed")
        return
    rows = confirm_fixes(agent, contract, recs)
    report_confirm(rows)
    st = load_state(agent)
    st["confirmations"] = [{"ts": L.now(), **r} for r in rows]
    L.write_json(state_path(agent), st)
    stale = stale_files(agent, contract)
    if not stale:
        print("STALENESS: every cited file matches its frozen fingerprint")
    else:
        report_stale(agent, contract, recs, stale)
    cur, note = L.current_history(agent, recs)
    print(f"END STATE {note}")
    update_end_state(agent, contract, cur, stale, rows)
    blocked = [f for f, c in stale.items() if c["state"] == "manual"]
    print("LIVE_RUNS: " + (f"BLOCKED until approved ({', '.join(blocked)})" if blocked else "allowed"))
    if blocked:
        sys.exit(3)


if __name__ == "__main__":
    main()
