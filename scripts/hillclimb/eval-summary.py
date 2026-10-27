#!/usr/bin/env python3
"""eval-summary.py — write a plain-English summary.html beside an eval's report.html.

Usage: eval-summary.py <flow-dir>
  e.g. eval-summary.py .claude/hillclimb/artifact-grounding-judge

Reads the hillclimb layout (build-eval / hillclimb guides in the claude-api skill):
  <flow>/_state.json            metrics, plus an optional "summary" block (below)
  <flow>/{baseline,v1,...}/results.jsonl, errors.jsonl, change.md, traces/

Optional _state.json "summary" block (every key optional):
  "what":       one sentence on what the eval tests
  "metrics":    {metric_id: plain-English meaning}
  "kinds":      {tags[0] value: plain-English meaning of that kind of case}
  "groups":     {"Fakes caught": [kinds...], ...}  headline score over a set of kinds
  "case_notes": path (relative to the repo root, the cwd) to a JSONL of
                {"id": ..., "note": ...} saying what each case tests

Everything read from disk is treated as data: every value goes through one
escape function, the page loads nothing from the network (CSP meta tag), and
trace links are only emitted for regular files inside the flow directory.
"""
import datetime
import html
import json
import math
import pathlib
import re
import sys

if len(sys.argv) != 2:
    sys.exit(__doc__)
FLOW = pathlib.Path(sys.argv[1]).resolve()
STATE = json.loads((FLOW / "_state.json").read_text()) if (FLOW / "_state.json").is_file() else {}
SUM = STATE.get("summary") or {}
E = lambda v: html.escape(str(v), quote=True)


def load_jsonl(p):
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def variant_key(name):
    return -1 if name == "baseline" else int(name[1:])


variants = sorted([d.name for d in FLOW.iterdir() if d.is_dir() and re.fullmatch(r"baseline|v[1-9]\d*", d.name)], key=variant_key)
if not variants:
    sys.exit(f"no baseline/ or v<N>/ directories under {FLOW}")
data = {v: {"rows": load_jsonl(FLOW / v / "results.jsonl"), "errors": load_jsonl(FLOW / v / "errors.jsonl")} for v in variants}

metrics = STATE.get("metrics") or [{"id": k, "kind": "binary"} for k in (data[variants[0]]["rows"] or [{}])[0].get("grade", {})]
head = next((m for m in metrics if m.get("kind") == "binary"), metrics[0] if metrics else None)
if head is None:
    sys.exit("no metrics in _state.json and no grade keys in results.jsonl")
HID = head["id"]
mlabel = lambda m: m.get("label") or m["id"]
mplain = lambda mid: (SUM.get("metrics") or {}).get(mid, "")

notes = {}
if SUM.get("case_notes"):
    np_ = pathlib.Path(SUM["case_notes"])
    notes = {r["id"]: r.get("note", "") for r in load_jsonl(np_ if np_.is_absolute() else pathlib.Path.cwd() / np_)}


def ok_rows(v):
    return [r for r in data[v]["rows"] if r.get("status", "ok") == "ok" and isinstance(r.get("grade"), dict)]


def per_case(v, mid):
    by = {}
    for r in ok_rows(v):
        if mid in r["grade"]:
            by.setdefault(r["prompt_id"], []).append(r["grade"][mid])
    return {k: sum(x) / len(x) for k, x in by.items()}


def run_rate(rows, mid):
    vals = [r["grade"][mid] for r in rows if mid in r["grade"]]
    return (sum(vals), len(vals))


def pct(k, n):
    return f"{k / n:.0%}" if n else "n/a"


def wilson(k, n, z=1.96):
    if not n:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def trace_link(v, r):
    p = FLOW / v / "traces" / f"{r['prompt_id']}_rep{r['rep']}.json"
    try:
        ok = p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(FLOW)
    except OSError:
        ok = False
    rel = p.relative_to(FLOW).as_posix() if ok else None
    return f'<a href="{E(rel)}">transcript</a>' if rel else "no transcript"


base = variants[0]
# Summarize the winning round when the hillclimb recorded one (best.round is
# 0-indexed, 0 = baseline); a reverted final round must not be the headline.
_best = (STATE.get("best") or {}).get("round")
latest = variants[_best] if isinstance(_best, int) and 0 <= _best < len(variants) else variants[-1]
rows_l = ok_rows(latest)
k, n = run_rate(rows_l, HID)
lo, hi = wilson(k, n)
cases_n = len({r["prompt_id"] for r in data[latest]["rows"]})
reps = max((r.get("rep", 0) for r in data[latest]["rows"]), default=0) + 1
noise = 100 / math.sqrt(n) if n else 0
models = sorted({r.get("model") for r in rows_l if r.get("model")})

P = []
P.append(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>{E(FLOW.name)} summary</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e3e3e8;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700;--card:#f6f6f8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececf0;--mut:#9a9aa2;--line:#2c2c31;--ok:#4cc26b;--bad:#ff6b6b;--warn:#e3b341;--card:#1e1e22}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}}
.mut{{color:var(--mut)}} .ok{{color:var(--ok)}} .bad{{color:var(--bad)}} .warn{{color:var(--warn)}}
.big{{font-size:40px;font-weight:700;line-height:1.1}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600}} code{{font-size:13px;word-break:break-word}}
details summary{{cursor:pointer}} pre{{white-space:pre-wrap;word-break:break-word;font-size:13px;margin:6px 0}}
.wrap{{overflow-x:auto}}
</style></head><body><main>""")

P.append(f"<h1>{E(FLOW.name)}: plain-English summary</h1>")
P.append(f'<p class="mut">{cases_n} test cases × {reps} tries each · model {E(", ".join(models) or "unknown")} · '
         f'{len(variants)} version(s) tested · built {E(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))} · '
         f'<a href="report.html">full report</a></p>')
if SUM.get("what"):
    P.append(f"<p>{E(SUM['what'])}</p>")

# --- headline
P.append("<h2>The score</h2>")
P.append(f'<div class="card"><div class="big">{pct(k, n)}</div>'
         f"<div>{E(mlabel(head))}: right on {k} of {n} tries"
         f'{" (latest version, " + E(latest) + ")" if len(variants) > 1 else ""}.</div>'
         f'<div class="mut">{E(mplain(HID))}</div>'
         f'<div class="mut">Likely true range: {lo:.0%} to {hi:.0%}. Higher is better.</div></div>')
P.append(f"<p>Every test case has a known right answer, written when the case was built. "
         f"Each try is graded by comparing the answer to that key. The score is the share of tries that matched.</p>")

if SUM.get("groups"):
    P.append("<h2>Broken down</h2><table><tr><th>What it should do</th><th>Score</th><th>Right / tries</th></tr>")
    for label, kinds in SUM["groups"].items():
        rs = [r for r in rows_l if r.get("tags") and r["tags"][0] in kinds]
        gk, gn = run_rate(rs, HID)
        cls = "ok" if gn and gk == gn else ("bad" if gn and gk / gn < 0.9 else "warn")
        P.append(f'<tr><td>{E(label)}</td><td class="{cls}"><b>{pct(gk, gn)}</b></td><td>{gk} / {gn}</td></tr>')
    P.append("</table>")

# --- other metrics
others = [m for m in metrics if m["id"] != HID]
if others:
    P.append("<h2>Other checks</h2><table><tr><th>Check</th><th>Score</th><th>What it means</th></tr>")
    for m in others:
        mk, mn = run_rate(rows_l, m["id"])
        cls = "ok" if mn and mk == mn else ("bad" if mn and mk / mn < 0.9 else "warn")
        P.append(f'<tr><td>{E(mlabel(m))}</td><td class="{cls}"><b>{pct(mk, mn)}</b> <span class="mut">({mk}/{mn})</span></td><td>{E(mplain(m["id"]))}</td></tr>')
    P.append("</table>")

# --- versions compared
if len(variants) > 1:
    P.append("<h2>Versions compared</h2>")
    P.append(f'<p class="mut">A change only counts as real if it moves the score by more than about ±{noise:.0f} points. Smaller moves can be luck.</p>')
    P.append("<table><tr><th>Version</th><th>What changed</th><th>Score</th><th>vs baseline</th></tr>")
    bk, bn = run_rate(ok_rows(base), HID)
    for v in variants:
        vk, vn = run_rate(ok_rows(v), HID)
        cm = FLOW / v / "change.md"
        desc = next((l.strip("# ").strip() for l in cm.read_text().splitlines() if l.strip()), "") if cm.is_file() else ("the agent as it was" if v == "baseline" else "")
        if v == base or not (vn and bn):
            delta = "-"
        else:
            d = (vk / vn - bk / bn) * 100
            verdict = "real gain" if d > noise else ("real drop" if d < -noise else "within noise")
            delta = f'<span class="{"ok" if d > noise else ("bad" if d < -noise else "mut")}">{d:+.0f} pts, {verdict}</span>'
        P.append(f"<tr><td><b>{E(v)}</b></td><td>{E(desc)}</td><td>{pct(vk, vn)}</td><td>{delta}</td></tr>")
    P.append("</table>")

# --- by kind
kind_rows = {}
for r in rows_l:
    kind_rows.setdefault((r.get("tags") or ["(untagged)"])[0], []).append(r)
P.append("<h2>By kind of test case</h2><div class=\"wrap\"><table><tr><th>Kind</th><th>Score</th><th>What this kind tests</th></tr>")
for kind, rs in sorted(kind_rows.items(), key=lambda kv: run_rate(kv[1], HID)[0] / max(1, run_rate(kv[1], HID)[1])):
    gk, gn = run_rate(rs, HID)
    cls = "ok" if gk == gn else ("bad" if gk / gn < 0.9 else "warn")
    P.append(f'<tr><td><code>{E(kind)}</code></td><td class="{cls}"><b>{pct(gk, gn)}</b> <span class="mut">({gk}/{gn})</span></td>'
             f'<td>{E((SUM.get("kinds") or {}).get(kind, ""))}</td></tr>')
P.append("</table></div>")

# --- mistakes
pc = per_case(latest, HID)
bad = sorted([c for c, s in pc.items() if s < 1], key=lambda c: pc[c])
P.append(f"<h2>Where it went wrong ({len(bad)} case{'s' if len(bad) != 1 else ''})</h2>")
if not bad:
    P.append('<p class="ok">Every case was right on every try.</p>')
else:
    P.append('<p class="mut">"2 of 3" means right on 2 of its 3 tries. A case that fails only sometimes is flaky. A case that always fails is a consistent blind spot.</p>')
for cid in bad:
    rs = sorted([r for r in rows_l if r["prompt_id"] == cid], key=lambda r: r.get("rep", 0))
    right = sum(r["grade"].get(HID, 0) for r in rs)
    P.append(f'<div class="card"><b><code>{E(cid)}</code></b> · <span class="{"bad" if right == 0 else "warn"}">right {int(right)} of {len(rs)}</span>')
    if notes.get(cid):
        P.append(f"<div>{E(notes[cid])}</div>")
    for r in rs:
        if r["grade"].get(HID, 0) >= 1:
            continue
        why = (r.get("explanation") or {}).get(HID, "")
        P.append(f'<details><summary>Try {r.get("rep", 0) + 1}: wrong · {trace_link(latest, r)}</summary><pre>{E(why)}</pre></details>')
    P.append("</div>")

# --- secondary-metric misses where the headline was right
sec = []
for m in others:
    for r in rows_l:
        if r["grade"].get(HID, 0) >= 1 and r["grade"].get(m["id"], 1) < 1:
            sec.append((m, r))
if sec:
    P.append(f"<h2>Right answer, but another check failed ({len(sec)})</h2>")
    shown = {}
    for m, r in sec:
        shown.setdefault(m["id"], []).append(r)
    for mid, rs in shown.items():
        m = next(x for x in metrics if x["id"] == mid)
        P.append(f"<details><summary><b>{E(mlabel(m))}</b>: {len(rs)} tries</summary><table><tr><th>Case</th><th>Try</th><th>Detail</th></tr>")
        for r in rs:
            why = (r.get("explanation") or {}).get(mid, "")
            P.append(f'<tr><td><code>{E(r["prompt_id"])}</code></td><td>{r.get("rep", 0) + 1} · {trace_link(latest, r)}</td><td>{E(why[:300])}</td></tr>')
        P.append("</table></details>")

# --- fine
good_kinds = [kd for kd, rs in kind_rows.items() if run_rate(rs, HID)[0] == run_rate(rs, HID)[1]]
if good_kinds:
    P.append("<h2>Working fine</h2><p>These kinds were right on every try. No change needed:</p><ul>")
    for kd in sorted(good_kinds):
        P.append(f"<li><code>{E(kd)}</code> {E((SUM.get('kinds') or {}).get(kd, ''))}</li>")
    P.append("</ul>")

# --- run health
errs = len(data[latest]["errors"])
trunc = sum(1 for r in data[latest]["rows"] if r.get("status") == "truncated")
cost = sum((r.get("cli_cost_usd") or 0) for r in data[latest]["rows"])
lat = sorted(r.get("latency_s", 0) for r in rows_l)
P.append("<h2>Run health</h2><ul>")
P.append(f"<li>{errs} tries crashed or timed out (not counted in the score).</li>")
P.append(f"<li>{trunc} tries were cut off by the length limit (not counted in the score).</li>")
if cost:
    P.append(f"<li>Cost of this version's run: about ${cost:.2f}.</li>")
if lat:
    P.append(f"<li>Typical time per try: {lat[len(lat) // 2]:.0f}s (slowest {lat[-1]:.0f}s).</li>")
P.append(f"<li>Score noise: about ±{noise:.0f} points at {n} tries. Treat smaller differences as luck.</li></ul>")
P.append('<p class="mut">For every case and full transcripts, open <a href="report.html">report.html</a>.</p>')
P.append("</main></body></html>")

out = FLOW / "summary.html"
if out.is_symlink():
    sys.exit(f"refusing to write through symlink: {out}")
out.write_text("\n".join(P))
print(f"OK: wrote {out}")
