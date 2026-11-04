#!/usr/bin/env python3
"""Validate a learnings-lane clustering against cluster-contract.json.

Usage: validate_clusters.py INPUT_FILE OUTPUT_FILE [--target TARGET]
       validate_clusters.py --sample      (validates the contract's own sample)
  INPUT_FILE   JSON array of the target's eligible learning rows
  OUTPUT_FILE  clustering JSON {"clusters": [...]}
  --target     when given, every cluster.target must equal it
Reads stdin when a FILE is '-'. Exit 0 when the contract holds.

The contract file is loaded at import and is the single source of truth. Structure comes from its
request/response schemas. Cross-field rules are the functions in RULES; their names must equal the
keys of the contract's "rules" block, or import fails.
"""
import json
import sys
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent / "cluster-contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())

TYPES = {"string": str, "object": dict, "array": list, "boolean": bool}


def schema_errors(value, schema, path):
    """Stdlib check of the schema subset the contract uses."""
    errs = []
    want = schema.get("type")
    if want and not isinstance(value, TYPES[want]):
        return [f"{path} must be {want}"]
    if isinstance(value, str) and len(value.strip()) < schema.get("minLength", 0):
        errs.append(f"{path} missing or empty")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path} needs at least {schema['minItems']} item(s)")
        for n, item in enumerate(value):
            errs += schema_errors(item, schema.get("items", {}), f"{path}[{n}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path}.{key} missing")
        for key, item in value.items():
            if key in props:
                errs += schema_errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{key} not allowed by the contract")
    return errs


def _has_key(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def _dupes(items):
    seen, dup = set(), []
    for item in items:
        if item in seen and item not in dup:
            dup.append(item)
        seen.add(item)
    return dup


# Rule functions take (request, response) and return a list of error strings. Request or response
# is already structurally valid when these run.
def rule_unique_request_ids(req, _res):
    return [f"request learning id {i!r} duplicated" for i in _dupes(r["id"] for r in req["learnings"])]


def rule_pregroup_ids_known(req, _res):
    known = {r["id"] for r in req["learnings"]}
    return [f"pregroup id not in request: {i}" for g in req["pregroups"] for i in g if i not in known]


def rule_unique_cluster_ids(_req, res):
    return [f"cluster id {i!r} duplicated" for i in _dupes(c["id"] for c in res["clusters"])]


def rule_target_matches_request(req, res):
    return [f"clusters[{n}].target {c['target']!r} must equal request target {req['target']!r}"
            for n, c in enumerate(res["clusters"]) if c["target"] != req["target"]]


def rule_ids_known(req, res):
    known = {r["id"] for r in req["learnings"]}
    return [f"clusters[{n}] id not in input: {i}"
            for n, c in enumerate(res["clusters"]) for i in c["learning_ids"] if i not in known]


def rule_partition(req, res):
    errs, seen = [], {}
    for c in res["clusters"]:
        for i in c["learning_ids"]:
            if i in seen:
                errs.append(f"learning {i} in two clusters: {seen[i]} and {c['id']}")
            else:
                seen[i] = c["id"]
    return errs + [f"learning {r['id']} in no cluster" for r in req["learnings"] if r["id"] not in seen]


def rule_no_escape_point(_req, res):
    return ["output contains escape_point; the model never writes it"] if _has_key(res, "escape_point") else []


RULES = {
    "unique-request-ids": rule_unique_request_ids,
    "pregroup-ids-known": rule_pregroup_ids_known,
    "unique-cluster-ids": rule_unique_cluster_ids,
    "target-matches-request": rule_target_matches_request,
    "ids-known": rule_ids_known,
    "partition": rule_partition,
    "no-escape-point": rule_no_escape_point,
}

if set(RULES) != set(CONTRACT["rules"]):
    raise RuntimeError("cluster-contract.json rules and validate_clusters.RULES differ: "
                       f"{sorted(set(RULES) ^ set(CONTRACT['rules']))}")


def request_errors(req):
    errs = schema_errors(req, CONTRACT["request"], "request")
    if errs:
        return errs
    return [e for name in ("unique-request-ids", "pregroup-ids-known") for e in RULES[name](req, None)]


def response_errors(req, res):
    errs = schema_errors(res, CONTRACT["response"], "response")
    if errs:
        return RULES["no-escape-point"](req, res) + errs
    return [e for name, rule in RULES.items() if name not in ("unique-request-ids", "pregroup-ids-known")
            for e in rule(req, res)]


def errors(inp, out, target=None):
    """Errors for response `out` to learning rows `inp`. With `target`, enforces target-matches-request."""
    if not isinstance(inp, list):
        return ["input must be an array of learning rows"]
    req = {"target": target if target is not None else "", "learnings": inp, "pregroups": []}
    errs = schema_errors(inp, CONTRACT["request"]["properties"]["learnings"], "learnings")
    if errs:
        return errs
    errs = RULES["unique-request-ids"](req, None)
    res_errs = schema_errors(out, CONTRACT["response"], "response")
    if res_errs:
        return errs + RULES["no-escape-point"](req, out) + res_errs
    for name, rule in RULES.items():
        if name in ("unique-request-ids", "pregroup-ids-known"):
            continue
        if name == "target-matches-request" and target is None:
            continue
        errs += rule(req, out)
    return errs


def load(p):
    return json.loads(sys.stdin.read() if p == "-" else open(p).read())


def main(argv):
    if argv == ["--sample"]:
        s = CONTRACT["sample"]
        errs = request_errors(s["request"]) + response_errors(s["request"], s["response"])
    else:
        target = None
        if "--target" in argv:
            i = argv.index("--target")
            target = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        if len(argv) != 2:
            sys.exit(__doc__)
        errs = errors(load(argv[0]), load(argv[1]), target)
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
