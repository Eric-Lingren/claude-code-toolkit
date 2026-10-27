#!/usr/bin/env python3
"""summarize.py — headline numbers for one artifact-grounding-judge variant.

Usage: summarize.py [variant_dir]   (default .claude/hillclimb/artifact-grounding-judge/baseline)

Per-case means over status-ok reps, then over cases (same as the report).
Splits verdict accuracy into fake-catch rate (expected rejected) and
real-record pass rate (expected pass), with Wilson 95% intervals over runs.
"""
import collections
import json
import math
import pathlib
import sys

vdir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".claude/hillclimb/artifact-grounding-judge/baseline")
rows = [json.loads(l) for l in (vdir / "results.jsonl").read_text().splitlines() if l.strip()]
errs = [l for l in (vdir / "errors.jsonl").read_text().splitlines() if l.strip()] if (vdir / "errors.jsonl").exists() else []
ok = [r for r in rows if r.get("status") == "ok"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def line(label, rs, key="verdict_correct"):
    k, n = sum(r["grade"][key] for r in rs), len(rs)
    lo, hi = wilson(k, n)
    print(f"  {label:<34} {k:>3}/{n:<3} = {k / n:6.1%}   95% CI {lo:5.1%}-{hi:5.1%}" if n else f"  {label:<34} n/a")


fake = [r for r in ok if r["meta"]["expected"]["verdict"] == "rejected"]
real = [r for r in ok if r["meta"]["expected"]["verdict"] == "pass"]
print(f"{vdir}: {len(rows)} rows ({len(ok)} ok), {len(errs)} failed attempts, "
      f"{len({r['prompt_id'] for r in rows})} cases")
line("Verdict correct (all)", ok)
line("Fakes caught (should reject)", fake)
line("Real records passed (should pass)", real)
line("Confidence correct", ok, "confidence_correct")
line("Write correct", ok, "write_correct")
line("JSON-only format", ok, "format_ok")

print("\nVerdict correct by case kind:")
by = collections.defaultdict(list)
for r in ok:
    by[r["tags"][0]].append(r)
for kind, rs in sorted(by.items(), key=lambda kv: sum(r["grade"]["verdict_correct"] for r in kv[1]) / len(kv[1])):
    line(kind, rs)

flaky = collections.defaultdict(list)
for r in ok:
    flaky[r["prompt_id"]].append(r["grade"]["verdict_correct"])
mixed = sorted(k for k, v in flaky.items() if 0 < sum(v) < len(v))
always_wrong = sorted(k for k, v in flaky.items() if sum(v) == 0)
print(f"\nAlways wrong ({len(always_wrong)}): {', '.join(always_wrong) or '-'}")
print(f"Inconsistent across reps ({len(mixed)}): {', '.join(mixed) or '-'}")

cost = [r.get("cli_cost_usd") or 0 for r in ok]
lat = sorted(r["latency_s"] for r in ok)
if cost:
    print(f"\nCost: ${sum(cost):.2f} total, ${sorted(cost)[len(cost) // 2]:.3f} median/run. "
          f"Latency: {lat[len(lat) // 2]:.1f}s median, {lat[-1]:.1f}s max.")
