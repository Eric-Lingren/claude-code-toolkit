#!/usr/bin/env python3
"""iterate.py <agent> [--dry-run] [--approve-tokens]   one iteration for an agent at stage iterate, then stop.
iterate.py decide <agent> <n>                         re-apply the keep rule to iteration n's recorded runs
iterate.py revert <agent> <n>                         revert iteration n's edit and prove the tree is clean

One iteration:
  1. baseline: live traces under <bench>/<agent>/baseline/ (made once if absent: up to 6 real cases plus
     every planted case)
  2. pick the worst failing check (stuck checks skipped); targets = up to 3 failing cases of it,
     sentinels = up to 2 cases that pass every check
  3. ONE Sonnet analyzer call reads only the failing traces and proposes one fix, a script fix considered first
  4. apply the edit (agent .md or a shared script), live-run targets + sentinels (run1), re-run the flips (run2)
  5. keep rule (iterate_lib.decide). Keep -> scores.json written, iterate_commit.py commits + pushes to main
     (--dry-run only prints). Revert -> git checkout of the edited files, clean tree proven.
  6. history: <bench>/<agent>/iterations.json (attempts, decision, SHA). Two reverts in a row on one check
     mark it stuck; the next invocation moves to the next failing check.
Env: AGENT_BENCH_DIR, AGENT_EVALS_DIR as in bench_lib. CLAUDE_BIN overrides the claude binary.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import bench_lib as L
import cases_lib as C
import iterate_lib as I
import preflight

HERE = L.HERE
RUN = os.path.join(HERE, "run.mjs")
BASELINE_REAL = 6
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or os.path.expanduser(
    "~/.local/share/fnm/node-versions/v24.19.0/installation/bin/claude")


def say(*a):
    print(*a, flush=True)


def baseline_cases(agent):
    """Up to BASELINE_REAL real cases plus every planted case. Planted cases carry the judgment tests, so
    they need a live trace too (cases.jsonl lists real cases first, so a plain --limit would skip them)."""
    cases = C.load_cases(agent)
    real = [c["id"] for c in cases if c["kind"] == "real"][:BASELINE_REAL]
    return real + [c["id"] for c in cases if c["kind"] == "planted"]


def live_run(agent, cases, out, limit=None):
    cmd = ["node", RUN, agent, "--kind", "real,planted", "--out", out]
    if cases:
        cmd += ["--cases", ",".join(cases)]
    if limit:
        cmd += ["--limit", str(limit)]
    say("run: " + " ".join(cmd))
    r = subprocess.run(cmd, text=True, capture_output=True, cwd=HERE)
    say(r.stdout.rstrip())
    if r.returncode not in (0,):
        say(f"run.mjs exit {r.returncode}: {r.stderr[-400:]}")
    if r.returncode == 4:
        sys.exit("ABORT: real unified-learnings.jsonl changed during a live run")
    return r.returncode


def usage_limit_hit(agent, since):
    p = os.path.join(I.agent_dir(agent), "errors.jsonl")
    if not os.path.exists(p):
        return False
    for ln in open(p):
        row = json.loads(ln)
        if row.get("failure_class") == "usage_limit" and row["ts"] >= since:
            return True
    return False


# ---- analyzer ---------------------------------------------------------------------------------------

def clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n...[{len(s) - n} more chars]"


def scripts_in(body):
    names = sorted(set(re.findall(r"scripts/([\w.-]+\.(?:py|sh|mjs))", body)))
    return [n for n in names if os.path.exists(os.path.join(L.SHARED_DIR, "scripts", n))]


def analyzer_prompt(agent, picked, base):
    agent_rel = f"agents/{agent}.md"
    body = open(os.path.join(L.SHARED_DIR, agent_rel), encoding="utf-8").read()
    parts = [f"""You are the analyzer for one iteration of an agent-improvement loop. The agent is `{agent}`. One of its checks keeps failing in live runs. Read ONLY the failing traces below and propose ONE fix.

## The failing check
{json.dumps({k: v for k, v in picked["check"].items() if k != "source"}, indent=1)}

## Rules
- Consider a SCRIPT fix before a prompt edit. A script fix means changing a script the agent calls (listed under "Scripts the agent calls") so the failure cannot happen, or the agent no longer has to do the fragile step by hand. Choose a prompt edit only when no script change would fix the failures, and say why in `script_fix_considered`.
- Exactly one fix, as exact find/replace edits. Each `find` must appear EXACTLY ONCE in its file (copy it verbatim from the file contents given). Keep edits small. Do not add steps that cost many more tokens per run (tokens per case must stay within 1.5x).
- Editable files (paths relative to claude-code-shared/): `{agent_rel}` or a script under `scripts/` that the agent calls. Never edit tests, contracts, cases or the benchmark harness.
- The fix must address the cause seen in the traces, not special-case these inputs.

## Output
Reply with a single JSON object and nothing else (no fences):
{{"kind": "script" or "prompt", "script_fix_considered": "<one or two sentences: what script change you weighed and why you chose or rejected it>", "rationale": "<which failure cause the fix removes>", "edits": [{{"file": "<path relative to claude-code-shared/>", "find": "<exact text, once>", "replace": "<new text>"}}]}}

## Agent file: {agent_rel}
```
{body}
```
"""]
    called = scripts_in(body)
    parts.append("## Scripts the agent calls (claude-code-shared/scripts/)\n" +
                 ("\n".join(f"### scripts/{n}\n```\n{clip(open(os.path.join(L.SHARED_DIR, 'scripts', n), encoding='utf-8', errors='replace').read(), 6000)}\n```" for n in called) or "(none found)"))
    parts.append("## Failing traces (check `%s`)" % picked["check"]["id"])
    for cid in picked["targets"]:
        r = base[cid]
        calls = [f"{c.get('name')}: {clip(json.dumps(c.get('input'), ensure_ascii=False), 300)}" for c in r.get("tool_calls", [])]
        parts.append(f"### case {cid}\nGrader detail: {picked['details'][cid]}\n"
                     f"Spawn prompt (agent input):\n{clip(r.get('spawn_prompt'), 2500)}\n"
                     f"Tool calls ({len(calls)}):\n" + "\n".join(calls[:12]) +
                     f"\nFinal output:\n{clip(r.get('final_output'), 1800)}\n")
    return "\n".join(parts)


def run_analyzer(prompt, outdir):
    os.makedirs(outdir, exist_ok=True)
    open(os.path.join(outdir, "analyzer-prompt.txt"), "w").write(prompt)
    cfg = next((d for d in (os.path.expanduser("~/.cch"), os.path.expanduser("~/.cco")) if os.path.isdir(d)), None)
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1"}
    if cfg:
        env["CLAUDE_CONFIG_DIR"] = os.environ.get("AGENT_BENCH_CONFIG_DIR", cfg)
    cmd = [CLAUDE_BIN, "-p", prompt, "--model", "sonnet", "--output-format", "json", "--tools", "",
           "--setting-sources", "project,local", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands", "--no-session-persistence", "--max-budget-usd", "1.0"]
    r = subprocess.run(cmd, text=True, capture_output=True, env=env, cwd=outdir, timeout=600)
    open(os.path.join(outdir, "analyzer-raw.json"), "w").write(r.stdout)
    try:
        ev = json.loads(r.stdout)
    except ValueError:
        sys.exit(f"analyzer produced no JSON (exit {r.returncode}): {r.stderr[-300:]}")
    text = (ev.get("result") or "").strip()
    if re.search(r"(usage limit|session limit|weekly limit|hit your .*limit)", text[:300], re.I):
        sys.exit(f"STOP: usage limit: {text[:200]}")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        prop = json.loads(m.group(0))
    except (AttributeError, ValueError):
        sys.exit(f"analyzer reply is not JSON: {text[:300]}")
    prop["_cost_usd"] = ev.get("total_cost_usd")
    return prop


# ---- subcommands ----------------------------------------------------------------------------------

def cmd_iterate(agent, dry, approve):
    q = L.load_queue()["agents"].get(agent)
    if not q or q["stage"] != "iterate":
        sys.exit(f"{agent} is at stage {q['stage'] if q else 'unqueued'}, not iterate")
    contract = I.load_contract(agent)
    blocked = preflight.gate(agent)
    if blocked:
        sys.exit("BLOCKED: hand-edited cited file(s) not approved: " + ", ".join(blocked) +
                 f". Review them, then run preflight.py approve {agent}. No live run started.")
    st = I.load_state(agent)
    ad = I.agent_dir(agent)
    n = len(st["iterations"]) + 1
    it_dir = os.path.join(ad, "iter", f"{n:03d}")
    base_dir = os.path.join(ad, "baseline")
    base = I.load_traces(base_dir)
    if not base:
        ids = baseline_cases(agent)
        say(f"no baseline traces; making one ({len(ids)} cases: real and planted)")
        live_run(agent, ids, base_dir)
        base = I.load_traces(base_dir)
        if not base:
            sys.exit("baseline run produced no traces (see errors.jsonl)")
    say("baseline rates: " + json.dumps(I.rates(contract, base)))

    picked = I.pick(contract, base, st["stuck"])
    if not picked:
        say("nothing failing outside stuck checks; nothing to iterate on")
        return
    cid = picked["check"]["id"]
    say(f"worst failing check: {cid}; targets={picked['targets']} sentinels={picked['sentinels']}")
    if not picked["sentinels"]:
        say("warning: no all-passing sentinel cases at baseline")

    agent_rel = os.path.relpath(os.path.join(L.SHARED_DIR, "agents", f"{agent}.md"), L.repo_root())
    if I.dirty([agent_rel]):
        sys.exit(f"refusing: {agent_rel} has uncommitted changes before the iteration")

    prop = run_analyzer(analyzer_prompt(agent, picked, base), it_dir)
    L.write_json(os.path.join(it_dir, "proposal.json"), prop)
    say(f"analyzer: kind={prop.get('kind')} script_fix_considered={prop.get('script_fix_considered')!r}")
    say(f"rationale: {prop.get('rationale')}")
    files = I.apply_edits(agent, prop.get("edits") or [])
    say("applied edit to: " + ", ".join(files))
    subprocess.run(["git", "diff", "--stat", "--"] + files, cwd=L.repo_root())

    started = L.now()
    run1_dir, run2_dir = os.path.join(it_dir, "run1"), os.path.join(it_dir, "run2")
    cases1 = picked["targets"] + picked["sentinels"]
    live_run(agent, cases1, run1_dir)
    run1 = I.load_traces(run1_dir)
    flips = [c for c in picked["targets"] if c in run1 and grade_ok(contract, cid, run1[c])]
    if flips and not usage_limit_hit(agent, started):
        live_run(agent, flips, run2_dir)
    run2 = I.load_traces(run2_dir)
    entry = {"n": n, "ts": L.now(), "check": cid, "kind": prop.get("kind"), "rationale": prop.get("rationale"),
             "script_fix_considered": prop.get("script_fix_considered"), "files": files,
             "targets": picked["targets"], "sentinels": picked["sentinels"], "run_dirs": [run1_dir, run2_dir],
             "analyzer_cost_usd": prop.get("_cost_usd"), "sha": None}
    if usage_limit_hit(agent, started):
        I.revert_files(files)
        entry.update(status="aborted", decision={"decision": "revert", "reasons": ["usage limit hit mid-run"]})
        finish(agent, st, entry, [])
        sys.exit("STOP: usage limit; edit reverted, iteration recorded as aborted (does not count toward stuck)")

    d = I.decide(contract, cid, picked["targets"], picked["sentinels"], base, run1, run2, approve)
    entry["decision"] = d
    L.write_json(os.path.join(it_dir, "decision.json"), d)
    say("decision: " + json.dumps({k: d[k] for k in ("decision", "reasons", "confirmed_2of2", "regressions", "tokens_per_case")}))
    if d["decision"] == "keep":
        entry["status"] = "kept"
        write_scores(agent, contract, base, run1, n, cid)
        out = subprocess.run([sys.executable, os.path.join(HERE, "iterate_commit.py"), agent, cid,
                              "--files=" + ",".join(files)] + (["--dry-run"] if dry else []),
                             text=True, capture_output=True, cwd=L.repo_root())
        say(out.stdout.rstrip())
        if out.returncode:
            sys.exit(f"commit step failed: {out.stderr}")
        sha = out.stdout.strip().splitlines()[-1].split("sha:", 1)[1].strip()
        entry["sha"] = None if dry else sha
        entry["committed"] = not dry
        for c, r in run1.items():
            shutil.copy(os.path.join(run1_dir, f"{c}_rep1.json"), os.path.join(base_dir, f"{c}_rep1.json"))
    else:
        entry["status"] = "reverted"
        I.revert_files(files)
        say("reverted; git status for edited files is clean")
    finish(agent, st, entry, files)


def grade_ok(contract, check_id, rec):
    import grade
    c = next(x for x in contract["checks"] if x["id"] == check_id)
    return grade.check(c, rec)[0] is True


def write_scores(agent, contract, base, run1, n, cid):
    after = {**base, **run1}
    L.write_json(os.path.join(L.evals_dir(), agent, "scores.json"),
                 {"agent": agent, "updated": L.now(), "iteration": n, "check_fixed": cid,
                  "rates": I.rates(contract, after), "cases": sorted(after)})


def finish(agent, st, entry, files):
    st["iterations"].append(entry)
    note = ""
    if entry["status"] == "reverted" and I.consecutive_reverts(st, entry["check"]) >= 2:
        if entry["check"] not in st["stuck"]:
            st["stuck"].append(entry["check"])
        att = [i for i in st["iterations"] if i["check"] == entry["check"]][-2:]
        note = f"STUCK: {entry['check']} reverted twice in a row. Attempts: " + " | ".join(
            f"#{i['n']} {i['kind']}: {i['rationale']} -> {', '.join(i['decision']['reasons'])}" for i in att)
    I.save_state(agent, st)
    say(f"iteration #{entry['n']}: check={entry['check']} status={entry['status']} sha={entry.get('sha')}")
    if note:
        say(note)
    say("history: " + I.state_path(agent))
    say("tree: " + (I.dirty(files) or "clean for edited files") if files else "")


def load_it(agent, n):
    st = I.load_state(agent)
    return st, next(i for i in st["iterations"] if i["n"] == int(n))


def cmd_decide(agent, n):
    contract = I.load_contract(agent)
    st, it = load_it(agent, n)
    base = I.load_traces(os.path.join(I.agent_dir(agent), "baseline"))
    d = I.decide(contract, it["check"], it["targets"], it["sentinels"], base, I.load_traces(it["run_dirs"][0]),
                 I.load_traces(it["run_dirs"][1]))
    say(json.dumps(d, indent=1))
    say(f"recorded status: {it['status']}; recomputed decision: {d['decision']}")


def cmd_revert(agent, n):
    st, it = load_it(agent, n)
    I.revert_files(it["files"])
    say(f"reverted {it['files']}; clean")


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not a:
        sys.exit(__doc__)
    if a[0] == "decide" and len(a) == 3:
        cmd_decide(a[1], a[2])
    elif a[0] == "revert" and len(a) == 3:
        cmd_revert(a[1], a[2])
    elif len(a) == 1:
        cmd_iterate(a[0], "--dry-run" in sys.argv, "--approve-tokens" in sys.argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
