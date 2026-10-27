"""Helpers for the cases stage (cases_*.py). Kept apart from bench_lib so the stages stay independent.

Layout under <evals>/<agent>/ (fixtures/ is gitignored, everything else is committable):
  cases.jsonl        one JSON case per line (see below)
  cases.lock.json    {"cases": {<case_id>: {<path relative to fixtures/>: sha256}}, "skipped": [...]}
  fixtures/<case_id>/prompt.txt           frozen spawn prompt (the agent input)
  fixtures/<case_id>/files/<abs path>     frozen files the spawn referenced

Case line:
  {"id", "agent", "kind": "real|planted|imported", "input": {...}, "expected": {...}, ...}
  real:     input {"prompt_file", "files": [{"path","fixture","sha256","how"}], "omitted": [...]},
            source {"agent_id","session_id","timestamp"}, expected = recorded verdict fields,
            expected_source "recorded-output"
  planted:  base (a real case id), edit {"target","find","replace"}, expected from the proposal
  imported: legacy cases carried over once; "input" holds the legacy fields, "expected" untouched
"""
import json
import os
import shutil

import bench_lib as L

ANSWER_KEYS = ["verdict", "confidence", "status", "result", "decision", "kind"]


def agent_dir(agent):
    return os.path.join(L.evals_dir(), agent)


def fixtures_dir(agent):
    return os.path.join(agent_dir(agent), "fixtures")


def cases_path(agent):
    return os.path.join(agent_dir(agent), "cases.jsonl")


def lock_path(agent):
    return os.path.join(agent_dir(agent), "cases.lock.json")


def load_cases(agent):
    p = cases_path(agent)
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        return [json.loads(x) for x in fh if x.strip()]


def write_cases(agent, cases):
    p = cases_path(agent)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(p + ".tmp", p)


def load_lock(agent):
    return L.read_json(lock_path(agent), {"version": 1, "cases": {}, "skipped": []})


def hash_tree(root):
    """{relative path: sha256} for every file under root."""
    out = {}
    for d, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            out[os.path.relpath(p, root)] = L.sha256_file(p)
    return dict(sorted(out.items()))


def lock_case(agent, case_id):
    lock = load_lock(agent)
    lock["cases"][case_id] = hash_tree(os.path.join(fixtures_dir(agent), case_id))
    L.write_json(lock_path(agent), lock)


def copy_fixture(agent, src_id, dst_id):
    src = os.path.join(fixtures_dir(agent), src_id)
    dst = os.path.join(fixtures_dir(agent), dst_id)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def answer_of(final_output):
    """Expected answer recorded from an agent output: the verdict-like scalar fields, else the whole object."""
    parsed, _ = grade_parse(final_output)
    if not isinstance(parsed, dict):
        return None
    sub = {k: parsed[k] for k in ANSWER_KEYS if k in parsed}
    return sub or parsed


def grade_parse(text):
    return L.grade.parse_output(text)
