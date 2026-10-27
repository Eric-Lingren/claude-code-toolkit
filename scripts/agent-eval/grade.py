#!/usr/bin/env python3
"""grade.py <contract.json> <records-dir-or-file>... — deterministic grader. No LLM.

Reads a contract and history/run records (see harvest.py) and prints per-check
pass/fail and pass rate. Exit 0 always unless usage error.

contract.json:
  {"agent": "...", "checks": [{"id": "...", "type": "<type>", "source": {...}, ...params}]}

Built-in check types (exactly these 8):
  json_parse      final output parses as JSON (bare, or one ```json fence). param: strict (bool, bare only)
  schema          parsed output matches "schema" (JSON-Schema subset: type, required,
                  properties, items, enum, additionalProperties:false)
  quote_in_input  every string at "path" (e.g. "evidence[].quote") is a substring of the
                  input text (spawn prompt + tool results; param "input": "prompt"|"all")
  tool_called     a tool call exists: "tool" (default Bash) whose JSON args contain "match"
                  (substring) or match "regex"
  tool_not_called no such tool call exists (same params as tool_called); use with "when"
                  for calls forbidden on one verdict
  file_written    a file was written whose path matches "glob" (fnmatch); live runs include shim writes
                  (the temp unified-learnings.jsonl)
  verdict_equals  value at "field" equals "expected", or record["expected"][field] when
                  "expected_from": "case"
  no_prose        final output is only JSON (no text outside one optional fence)

A check may set "when": {"field": ..., "equals": ...} on the parsed output to skip
(not applicable) records. Skipped records are excluded from the pass rate.

Format checks (json_parse, no_prose) are n/a on a live run (record "source": "live") that has no
SubagentHandback call. Production spawns return their answer as the handback tool's argument, which
models write bare. `claude -p` runs have no handback tool, so the answer is a chat message that models
fence and narrate around. Grading format there measures the bench, not the agent. These checks grade
from production history only.
"""
import fnmatch
import json
import os
import re
import sys

FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)
FORMAT_TYPES = ("json_parse", "no_prose")


def chat_channel(rec):
    """True for a live run whose answer came back as a chat message, not through SubagentHandback."""
    return rec.get("source") == "live" and not any(
        c.get("name") == "SubagentHandback" for c in rec.get("tool_calls", []))


def parse_output(text, strict=False):
    t = (text or "").strip()
    try:
        return json.loads(t), None
    except ValueError as e:
        err = str(e)
    if not strict:
        m = FENCE.match(t)
        if m:
            try:
                return json.loads(m.group(1)), None
            except ValueError as e:
                err = str(e)
        else:
            # narration can hold its own code fences before the answer; the answer comes last
            for body in reversed(re.findall(r"```(?:json)?\s*\n(.*?)\n\s*```", t, re.S)):
                try:
                    return json.loads(body), None
                except ValueError as e:
                    err = str(e)
    return None, err


def values_at(obj, path):
    """Minimal path: a.b[].c ; returns list of values."""
    cur = [obj]
    for part in path.split("."):
        nxt = []
        many = part.endswith("[]")
        key = part[:-2] if many else part
        for o in cur:
            if key:
                o = o.get(key) if isinstance(o, dict) else None
            if many:
                if isinstance(o, list):
                    nxt.extend(o)
            elif o is not None:
                nxt.append(o)
        cur = nxt
    return cur


TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(v, s, path="$"):
    errs = []
    t = s.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        ok = False
        for x in ts:
            if x == "integer":
                ok |= isinstance(v, int) and not isinstance(v, bool)
            elif x == "number":
                ok |= isinstance(v, (int, float)) and not isinstance(v, bool)
            else:
                ok |= isinstance(v, TYPES[x]) and not (x != "boolean" and isinstance(v, bool))
        if not ok:
            return [f"{path}: expected {t}, got {type(v).__name__}"]
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} not in enum")
    if isinstance(v, dict):
        for r in s.get("required", []):
            if r not in v:
                errs.append(f"{path}: missing required '{r}'")
        props = s.get("properties", {})
        for k, sub in props.items():
            if k in v:
                errs += validate(v[k], sub, f"{path}.{k}")
        if s.get("additionalProperties") is False:
            for k in v:
                if k not in props:
                    errs.append(f"{path}: unexpected key '{k}'")
    if isinstance(v, list) and "items" in s:
        for i, x in enumerate(v):
            errs += validate(x, s["items"], f"{path}[{i}]")
    return errs


def input_text(rec, mode):
    parts = [rec.get("spawn_prompt") or ""]
    if mode == "all":
        parts += [c.get("result") or "" for c in rec.get("tool_calls", [])]
    return "\n".join(parts)


def matching_call(c, rec, tool):
    # live runs also carry the shim log: shimmed calls (log-learning.py, gh, child agents) grade from it
    shimmed = [{"name": x.get("tool"), "input": x.get("input")} for x in rec.get("shim_calls", []) if x.get("input")]
    for call in rec.get("tool_calls", []) + shimmed:
        if call.get("name") != tool:
            continue
        args = json.dumps(call.get("input"), ensure_ascii=False)
        if "match" in c and c["match"] in args:
            return True
        if "regex" in c and re.search(c["regex"], args):
            return True
        if "match" not in c and "regex" not in c:
            return True
    return False


# each returns (result, detail); result True/False/None(n/a)
def check(c, rec):
    typ = c["type"]
    if typ in FORMAT_TYPES and chat_channel(rec):
        return None, "n/a: live run has no SubagentHandback; format graded from production history"
    out = rec.get("final_output") or ""
    parsed, perr = parse_output(out, strict=c.get("strict", False))
    when = c.get("when")
    if when:
        got = values_at(parsed, when["field"]) if parsed is not None else []
        if not got or got[0] != when["equals"]:
            return None, "n/a"
    if typ == "json_parse":
        return parsed is not None, perr or ""
    if typ == "no_prose":
        t = out.strip()
        if parsed is not None and (t.startswith("{") or t.startswith("[") or FENCE.match(t)):
            return True, ""
        return False, "output has text outside JSON"
    if typ == "schema":
        if parsed is None:
            return False, "unparseable"
        errs = validate(parsed, c["schema"])
        return not errs, "; ".join(errs[:3])
    if typ == "quote_in_input":
        if parsed is None:
            return False, "unparseable"
        text = input_text(rec, c.get("input", "all"))
        qs = [q for q in values_at(parsed, c["path"]) if isinstance(q, str)]
        bad = [q for q in qs if q not in text]
        return not bad, f"{len(bad)}/{len(qs)} quotes not in input: {bad[0][:80]!r}" if bad else ""
    if typ in ("tool_called", "tool_not_called"):
        tool = c.get("tool", "Bash")
        pat = c.get("match") or c.get("regex")
        found = matching_call(c, rec, tool)
        if typ == "tool_called":
            return found, "" if found else f"no {tool} call matching {pat!r}"
        return not found, f"forbidden {tool} call matching {pat!r}" if found else ""
    if typ == "file_written":
        for p in rec.get("files_written", []):
            if fnmatch.fnmatch(p, c["glob"]):
                return True, ""
        return False, f"no file matching {c['glob']}"
    if typ == "verdict_equals":
        if parsed is None:
            return False, "unparseable"
        exp = c.get("expected")
        if c.get("expected_from") == "case":
            exp = (rec.get("expected") or {}).get(c["field"])
            if exp is None:
                return None, "n/a"
        got = values_at(parsed, c["field"])
        return bool(got) and got[0] == exp, f"got {got[0] if got else None!r}, want {exp!r}"
    raise SystemExit(f"unknown check type: {typ}")


def load_records(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".json"))
        else:
            files.append(p)
    return [json.load(open(f)) for f in files]


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: grade.py <contract.json> <records-dir-or-file>...")
    contract = json.load(open(sys.argv[1]))
    recs = load_records(sys.argv[2:])
    print(f"agent: {contract.get('agent')}  records: {len(recs)}")
    for c in contract["checks"]:
        p = f = n = 0
        fails = []
        for r in recs:
            res, detail = check(c, r)
            if res is None:
                n += 1
            elif res:
                p += 1
            else:
                f += 1
                fails.append((r.get("agent_id", "?"), detail))
        tot = p + f
        rate = f"{100.0 * p / tot:.1f}%" if tot else "n/a"
        print(f"{c['id']:<28} {c['type']:<15} pass {p:>3} fail {f:>3} n/a {n:>3}  rate {rate}")
        for aid, d in fails[:2]:
            print(f"    fail e.g. {aid}: {d}")


if __name__ == "__main__":
    main()
