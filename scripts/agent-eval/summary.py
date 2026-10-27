#!/usr/bin/env python3
"""summary.py <agent>   write the plain-English summary page for an agent and print its path.

Output: <bench>/<agent>/summary.html  (default .claude/agent-bench/<agent>/summary.html)
Reads (all optional, missing pieces are said so on the page):
  queue.json                      stage the agent is at now
  <agent>/iterations.json         every iteration: check, kind, decision, reasons, sha, per-case results, stuck list
  <agent>/baseline/               live baseline traces (graded here for the "start" column)
  <agent>/iter/NNN/               proposal.json (the edit, shown as a diff) and run1/run2 traces (graded for before/after)
  <evals>/<agent>/baseline.json   pass rates on past production spawns, frozen with the contract
  <evals>/<agent>/scores.json     current pass rates per check (written when a fix is kept)
  <evals>/<agent>/contract.json   check ids and what each one verifies
Every rate is shown as a percentage with its change in percentage points (pp), so a run that helped,
hurt or did nothing reads at a glance. Separate from hillclimb and eval-report.sh; it shares nothing with
them. Run it after every invocation of /improve-agent-benchmarks, whatever stage ran.
Env: AGENT_BENCH_DIR, AGENT_EVALS_DIR as in bench_lib.
"""
import difflib
import html
import os
import sys

import bench_lib as L
import iterate_lib as I

STAGES = ["contract", "cases", "iterate"]
STAGE_TEXT = {"contract": "Write down what the agent must do (the contract), each rule cited from its source.",
              "cases": "Freeze real inputs and plant defects, so every rule can be tested.",
              "iterate": "Try one fix per run, keep it only if it measurably helps and breaks nothing."}

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#f5f5f7;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700;
--okbg:#e8f5ec;--badbg:#fdecea;--bar:#d1d1d6;--code:#f2f2f4}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f4;--muted:#a1a1a6;--line:#2c2c30;--card:#1f1f23;
--ok:#5fd07a;--bad:#ff6b5e;--warn:#e3b341;--okbg:#17301f;--badbg:#3a1a18;--bar:#3a3a40;--code:#26262b}}
body{font:15px/1.55 -apple-system,system-ui,sans-serif;max-width:920px;margin:2rem auto;padding:0 16px;color:var(--fg);background:var(--bg)}
h1{font-size:1.5rem;margin-bottom:.2rem}h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid var(--line);padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid var(--line);padding:.4rem .5rem;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
code{background:var(--code);padding:0 .25rem;border-radius:3px;font-size:13px}
.headline{background:var(--card);border-radius:10px;padding:1rem 1.2rem;margin:1rem 0;font-size:16px}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:1rem 0}.tile{flex:1 1 180px;background:var(--card);border-radius:10px;padding:.8rem 1rem}
.tile b{display:block;font-size:1.6rem;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:13px}
.steps{display:flex;gap:6px;margin:.6rem 0 0}.step{flex:1;padding:.4rem .6rem;border-radius:6px;background:var(--card);font-size:13px;color:var(--muted)}
.step.done{background:var(--okbg);color:var(--ok)}.step.now{outline:2px solid var(--fg);color:var(--fg);font-weight:600}
.bar{display:inline-block;width:70px;height:8px;background:var(--bar);border-radius:4px;vertical-align:middle;margin-right:6px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--ok)}
.diff{background:var(--code);border-radius:8px;padding:.6rem .8rem;overflow-x:auto;font:12.5px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
.diff .add{background:var(--okbg);color:var(--ok);display:block}.diff .del{background:var(--badbg);color:var(--bad);display:block}
.pill{display:inline-block;padding:0 .5rem;border-radius:10px;font-size:12px;font-weight:600}
.pill.ok{background:var(--okbg)}.pill.bad{background:var(--badbg)}
details{margin:.5rem 0}summary{cursor:pointer;color:var(--muted)}
"""


def e(x):
    return html.escape(str(x))


def rate(p, t):
    return None if not t else 100.0 * p / t


def fmt_rate(p, t):
    r = rate(p, t)
    if r is None:
        return "<span class='muted'>n/a</span>"
    return f"<span class='bar'><i style='width:{r:.0f}%'></i></span>{r:.0f}% <span class='muted'>({p}/{t})</span>"


def fmt_delta(before, after):
    """before/after are percentages or None; returns a colored pp change."""
    if before is None or after is None:
        return "<span class='muted'>n/a</span>"
    d = round(after - before)
    if d > 0:
        return f"<span class='ok'>&#9650; +{d} pp</span>"
    if d < 0:
        return f"<span class='bad'>&#9660; {d} pp</span>"
    return "<span class='muted'>no change</span>"


def label(c):
    """Plain-English name for a contract check, from its type and params."""
    t = c.get("type", "")
    w = c.get("when") or {}
    on = f" (when verdict is {w.get('equals')})" if w else ""
    if c.get("regex"):  # a regex reads badly; the check id names the rule
        return c.get("id", "").replace("_", " ").capitalize() + on
    tool = c.get("match") or c.get("tool") or "a tool"
    req = (c.get("schema") or {}).get("required") or []
    text = {
        "json_parse": "Reply is bare JSON, no ``` fences" if c.get("strict") else "Reply parses as JSON",
        "no_prose": "Reply has no text outside the JSON",
        "schema": f"Reply has fields {', '.join(req)}" if req else "Reply has the required fields and values",
        "quote_in_input": "Every quote comes from the input",
        "tool_called": f"Runs {tool}",
        "tool_not_called": f"Does not run {tool}",
        "file_written": f"Writes {c.get('glob', 'the output file')}",
        "verdict_equals": f"Gets the right {c.get('field', 'answer')}",
    }.get(t, t)
    return text + on


def plain_detail(c, rec, ok, detail):
    """Turn a grader detail into words for the case table."""
    if ok or rec is None:
        return ""
    out = (rec.get("final_output") or "").strip()
    if c.get("type") in ("json_parse", "no_prose"):
        if out.startswith("```"):
            return "wrapped the JSON in a ``` fence"
        if out and out[0] not in "{[":
            return "wrote text before the JSON: \"" + out.split("\n")[0][:60] + "\""
    return detail[:120]


def overall(rates):
    p = sum(v["pass"] for v in rates.values())
    t = sum(v["total"] for v in rates.values())
    full = sum(1 for v in rates.values() if v["total"] and v["pass"] == v["total"])
    graded = sum(1 for v in rates.values() if v["total"])
    return rate(p, t), full, graded


def edit_diff(edits):
    out = []
    for ed in edits or []:
        rows = list(difflib.unified_diff(ed.get("find", "").splitlines(), ed.get("replace", "").splitlines(), lineterm="", n=0))[2:]
        body = "".join(f"<span class='add'>{e(r)}</span>" if r.startswith("+") else
                       f"<span class='del'>{e(r)}</span>" if r.startswith("-") else f"{e(r)}\n" for r in rows)
        out.append(f"<p class='muted'>File: <code>{e(ed.get('file'))}</code></p><div class='diff'>{body}</div>")
    return "".join(out) or "<p class='muted'>No edit recorded.</p>"


def iteration_block(agent, it, contract, base_recs, checks):
    d = it.get("decision") or {}
    n = it["n"]
    it_dir = os.path.join(I.agent_dir(agent), "iter", f"{n:03d}")
    prop = L.read_json(os.path.join(it_dir, "proposal.json"), {})
    cases = list(it.get("targets") or []) + list(it.get("sentinels") or [])
    before = {k: v for k, v in base_recs.items() if k in cases}
    run1 = I.load_traces(os.path.join(it_dir, "run1"))
    run2 = I.load_traces(os.path.join(it_dir, "run2"))
    chk = it["check"]
    c = checks.get(chk, {"id": chk})
    out = []

    rb = I.rates(contract, before) if before else {}
    ra = I.rates(contract, run1) if run1 else {}
    tb, ta = rb.get(chk, {}), ra.get(chk, {})
    kept = it.get("status") == "kept"
    pill = "<span class='pill ok'>KEPT</span>" if kept else "<span class='pill bad'>REVERTED</span>"
    out.append(f"<h2>Iteration #{e(n)} {pill}</h2>")
    tgt = it.get("targets") or []
    need = -(-len(tgt) // 2)
    conf = d.get("confirmed_2of2") or []
    out.append("<ul>"
               f"<li><b>Goal:</b> make <b>{e(label(c))}</b> pass <code>{e(chk)}</code>. "
               f"Before the fix it passed on {fmt_rate(tb.get('pass', 0), tb.get('total', 0))} of the targeted cases.</li>"
               f"<li><b>After the fix:</b> {fmt_rate(ta.get('pass', 0), ta.get('total', 0))} "
               f"({fmt_delta(rate(tb.get('pass', 0), tb.get('total', 0)), rate(ta.get('pass', 0), ta.get('total', 0)))}).</li>"
               f"<li><b>Keep rule:</b> at least {need} of {len(tgt)} targeted cases must pass on two runs in a row, and no "
               f"previously passing case may break. Result: {len(conf)} of {len(tgt)} passed twice.</li>")
    for r in d.get("reasons") or []:
        out.append(f"<li class='bad'><b>Why it was {'kept' if kept else 'rolled back'}:</b> {e(r)}</li>")
    if kept and it.get("sha"):
        out.append(f"<li class='ok'><b>Committed:</b> <code>{e(it['sha'][:10])}</code></li>")
    tk = d.get("tokens_per_case") or {}
    if tk.get("ratio"):
        ch = round((tk["ratio"] - 1) * 100)
        out.append(f"<li><b>Cost:</b> {abs(ch)}% {'fewer' if ch < 0 else 'more'} tokens per case than the baseline"
                   f" ({tk['baseline']:,} vs {tk['candidate']:,}).</li>")
    out.append("</ul>")

    out.append(f"<p><b>What was changed</b> ({e(it.get('kind'))} fix):</p>" + edit_diff(prop.get("edits")))
    if it.get("rationale"):
        out.append(f"<p><b>Why the analyzer tried this:</b> {e(it['rationale'])}</p>")

    # per-case result on the targeted check
    per = d.get("per_case") or {}
    res_b, res_1, res_2 = (I.results(contract, x).get(chk, {}) if x else {} for x in (before, run1, run2))
    f = lambda r: "<span class='muted'>not run</span>" if r is None else ("<span class='ok'>pass</span>" if r[0] else
                                                                          "<span class='muted'>n/a</span>" if r[0] is None else "<span class='bad'>fail</span>")
    rows = "".join(f"<tr><td><code>{e(cid)}</code></td><td>{e((per.get(cid) or {}).get('role', ''))}</td>"
                   f"<td>{f(res_b.get(cid))}</td><td>{f(res_1.get(cid))}</td><td>{f(res_2.get(cid))}</td>"
                   f"<td class='muted'>{e(plain_detail(c, run1.get(cid), *(res_1.get(cid) or (True, ''))))}</td></tr>" for cid in cases)
    out.append(f"<p><b>Case by case, for <code>{e(chk)}</code>:</b></p><table><tr><th>case</th><th>role</th><th>before</th>"
               f"<th>after (run 1)</th><th>after (run 2)</th><th>detail</th></tr>{rows}</table>")

    # every other check on the same cases
    moved = [(k, rb[k], ra.get(k, {})) for k in rb if k != chk and rb[k]["total"] and ra.get(k, {}).get("total")
             and round(rate(rb[k]["pass"], rb[k]["total"])) != round(rate(ra[k]["pass"], ra[k]["total"]))]
    if moved:
        rows = "".join(f"<tr><td>{e(label(checks.get(k, {'id': k})))} <span class='muted'><code>{e(k)}</code></span></td>"
                       f"<td class='num'>{fmt_rate(b['pass'], b['total'])}</td><td class='num'>{fmt_rate(a['pass'], a['total'])}</td>"
                       f"<td class='num'>{fmt_delta(rate(b['pass'], b['total']), rate(a['pass'], a['total']))}</td></tr>" for k, b, a in moved)
        up = sum(1 for _, b, a in moved if rate(a["pass"], a["total"]) > rate(b["pass"], b["total"]))
        out.append("<p><b>Side effects on other checks</b> (same cases, before vs after run 1): "
                   f"<span class='ok'>{up} improved</span>, <span class='bad'>{len(moved) - up} got worse</span>"
                   f"{'. The fix was rolled back, so none of these changes stuck' if not kept else ''}.</p>"
                   f"<table><tr><th>check</th><th class='num'>before</th><th class='num'>after</th><th class='num'>change</th></tr>{rows}</table>")
    elif run1:
        out.append("<p class='muted'>No other check moved on these cases.</p>")
    return "".join(out), rb.get(chk), ra.get(chk)


def build(agent):
    bench = L.bench_dir()
    q = L.load_queue()["agents"].get(agent)
    stage = q["stage"] if q else "not queued"
    st = L.read_json(os.path.join(bench, agent, "iterations.json"), {"iterations": [], "stuck": []})
    scores = L.read_json(os.path.join(L.evals_dir(), agent, "scores.json"))
    contract = L.read_json(os.path.join(L.evals_dir(), agent, "contract.json"))
    hist = (L.read_json(os.path.join(L.evals_dir(), agent, "baseline.json")) or {}).get("checks", {})
    base_recs = I.load_traces(os.path.join(I.agent_dir(agent), "baseline")) if contract else {}
    its = st.get("iterations", [])
    checks = {c["id"]: c for c in (contract or {}).get("checks", [])}

    live0 = I.rates(contract, base_recs) if base_recs else {}
    now = (scores or {}).get("rates") or live0
    o0, _, _ = overall(live0) if live0 else (None, 0, 0)
    o1, full1, graded1 = overall(now) if now else (None, 0, 0)
    kept = [i for i in its if i.get("status") == "kept"]

    out = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{e(agent)} benchmark</title><style>{CSS}</style>",
           f"<h1>{e(agent)}</h1><p class='muted'>Agent benchmark summary. Generated {e(L.now())}.</p>"]

    # progress through the stages
    si = STAGES.index(stage) if stage in STAGES else -1
    out.append("<div class='steps'>" + "".join(
        f"<div class='step {'done' if i < si else 'now' if i == si else ''}'>{i + 1}. {s}{' &#10003;' if i < si else ''}</div>"
        for i, s in enumerate(STAGES)) + "</div>")
    if si >= 0:
        out.append(f"<p class='muted'>Now at <b>{e(stage)}</b>: {e(STAGE_TEXT[stage])}</p>")

    # headline in one sentence
    if not its:
        head = (f"No fix has been tried yet. The agent is at the <b>{e(stage)}</b> stage."
                + (f" On live runs it currently passes <b>{o1:.0f}%</b> of graded checks." if o1 is not None else ""))
    else:
        last = its[-1]
        moved = "" if o0 is None or o1 is None else (
            f" Overall pass rate is <b>{o1:.0f}%</b> ({fmt_delta(o0, o1)} since the live baseline).")
        if kept:
            head = f"<span class='ok'><b>{len(kept)} of {len(its)} fixes kept.</b></span>" + moved
        else:
            head = f"<span class='bad'><b>No improvement yet.</b></span> {len(its)} fix{'es' if len(its) > 1 else ''} tried, none kept." + moved
        head += f" Last run worked on <code>{e(last['check'])}</code> and was <b>{e(last.get('status'))}</b>."
    out.append(f"<div class='headline'>{head}</div>")

    if now:
        out.append("<div class='tiles'>"
                   f"<div class='tile'><span>Overall pass rate</span><b>{o1:.0f}%</b><span>{fmt_delta(o0, o1) if its else 'live baseline'}</span></div>"
                   f"<div class='tile'><span>Checks fully passing</span><b>{full1} / {graded1}</b><span>of checks with graded cases</span></div>"
                   f"<div class='tile'><span>Fixes kept</span><b>{len(kept)} / {len(its)}</b><span>{len(st.get('stuck') or [])} check(s) stuck</span></div>"
                   "</div>")

    # scoreboard
    out.append("<h2>Scoreboard</h2>")
    if not checks:
        out.append("<p>No contract yet, so there is nothing to score.</p>")
    else:
        out.append("<p class='muted'><b>Past spawns</b>: real production runs before any fix. "
                   "<b>Live start</b>: the same cases run live before the first fix. "
                   "<b>Now</b>: after the last kept fix (equals live start until a fix is kept). "
                   "<b>Change</b>: now minus live start, in percentage points.</p>")
        rows = []
        for cid, c in checks.items():
            h = hist.get(cid) or {}
            l0, n0 = live0.get(cid, {}), now.get(cid, {})
            r0, r1 = rate(l0.get("pass", 0), l0.get("total", 0)), rate(n0.get("pass", 0), n0.get("total", 0))
            stuck = " <span class='warn'>(stuck)</span>" if cid in (st.get("stuck") or []) else ""
            rows.append(f"<tr><td>{e(label(c))}{stuck}<br><span class='muted'><code>{e(cid)}</code></span></td>"
                        f"<td class='num'>{fmt_rate(h.get('pass', 0), h.get('pass', 0) + h.get('fail', 0))}</td>"
                        f"<td class='num'>{fmt_rate(l0.get('pass', 0), l0.get('total', 0))}</td>"
                        f"<td class='num'>{fmt_rate(n0.get('pass', 0), n0.get('total', 0))}</td>"
                        f"<td class='num'>{fmt_delta(r0, r1) if live0 else '<span class=muted>n/a</span>'}</td></tr>")
        out.append("<table><tr><th>check</th><th class='num'>past spawns</th><th class='num'>live start</th>"
                   "<th class='num'>now</th><th class='num'>change</th></tr>" + "".join(rows) + "</table>")
        if not base_recs:
            out.append("<p class='muted'>No live baseline yet: it is made on the first iterate run.</p>")

    # latest iteration in full, older ones summarized
    summaries = []
    for i, it in enumerate(its):
        block, b, a = iteration_block(agent, it, contract, base_recs, checks)
        if i == len(its) - 1:
            out.append(block)
        rb = rate(b["pass"], b["total"]) if b else None
        ra = rate(a["pass"], a["total"]) if a else None
        summaries.append(f"<tr><td>{e(it['n'])}</td><td>{e(label(checks.get(it['check'], {'id': it['check']})))}<br>"
                         f"<span class='muted'><code>{e(it['check'])}</code></span></td><td>{e(it.get('kind'))}</td>"
                         f"<td class='num'>{'n/a' if rb is None else f'{rb:.0f}%'} &rarr; {'n/a' if ra is None else f'{ra:.0f}%'}<br>{fmt_delta(rb, ra)}</td>"
                         f"<td>{'<span class=ok>kept</span>' if it.get('status') == 'kept' else '<span class=bad>' + e(it.get('status')) + '</span>'}</td>"
                         f"<td>{e((it.get('sha') or '-')[:10])}</td></tr>")

    out.append("<h2>Stuck checks</h2>")
    stuck = st.get("stuck") or []
    if not stuck:
        out.append("<p class='muted'>None. A check becomes stuck after two fixes in a row are rolled back; it is then skipped.</p>")
    for cid in stuck:
        att = [i for i in its if i["check"] == cid][-2:]
        out.append(f"<p class='warn'><b>{e(label(checks.get(cid, {'id': cid})))}</b> <code>{e(cid)}</code>: two fixes in a row were rolled back.</p><ul>" +
                   "".join(f"<li>#{e(i['n'])} {e(i.get('kind'))}: {e(i.get('rationale'))} &rarr; {e(', '.join((i.get('decision') or {}).get('reasons') or []))}</li>" for i in att) + "</ul>")

    out.append("<h2>All iterations</h2>")
    if not its:
        out.append("<p class='muted'>None yet.</p>")
    else:
        out.append("<p class='muted'>Before and after are the targeted check's pass rate on the targeted cases.</p>"
                   "<table><tr><th>#</th><th>check</th><th>kind</th><th class='num'>before &rarr; after</th><th>outcome</th><th>commit</th></tr>"
                   + "".join(summaries) + "</table>")

    out.append("<details><summary>How to read this page</summary><ul>"
               "<li><b>Target case</b>: a case that failed the check being fixed. The fix is judged on these.</li>"
               "<li><b>Sentinel case</b>: a case that passed every check. It must keep passing, so a fix cannot break something else.</li>"
               "<li><b>Passed twice</b>: a target must pass on two separate runs, so one lucky run does not count.</li>"
               "<li><b>pp</b>: percentage points. 40% to 60% is +20 pp.</li>"
               "<li><b>n/a</b>: no case applied (for example a check that only runs on rejected verdicts).</li></ul></details>")
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    agent = sys.argv[1]
    path = os.path.join(L.bench_dir(), agent, "summary.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build(agent))
    print(f"summary: {path}")


if __name__ == "__main__":
    main()
