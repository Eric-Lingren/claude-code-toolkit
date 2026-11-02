#!/usr/bin/env python3
"""validate-seed.py - validate a to-seed draft against contracts/seed-schema.json.

to-seed runs this on the verification temp seed and on the final seed file.
Uses jsonschema Draft7Validator when importable. Cross-file $refs (e.g.
provenance-schema) resolve from the other *.json files in the schema's dir.

If jsonschema is not importable, falls back to the schema's top-level
`required` list and the `status` enum only, and prints a WARNING that full
validation was skipped.

Usage:
  python3 validate-seed.py <seed-path> [--schema <path>]

  --schema  Schema file. Default: ../../contracts/seed-schema.json,
            relative to this script.

Output:
  OK: <seed-path> valid
  ERROR: <json-path>: <message>   (one line per violation, first 20 shown)

Exit codes:
  0  Seed is valid
  1  Seed is invalid, input is unreadable or not JSON, or bad arguments
"""

import json
import pathlib
import sys
import warnings

MAX_ERRORS = 20
DEFAULT_SCHEMA = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "contracts" / "seed-schema.json"
)


def fail(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    seed, schema = None, DEFAULT_SCHEMA
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--schema":
            if i + 1 >= len(argv):
                fail("--schema requires a path")
            schema = pathlib.Path(argv[i + 1])
            i += 2
            continue
        if arg.startswith("-"):
            fail(f"unknown flag: {arg}")
        if seed is not None:
            fail("expected exactly 1 seed path")
        seed = pathlib.Path(arg)
        i += 1
    if seed is None:
        fail("expected a seed path. Usage: validate-seed.py <seed-path> [--schema <path>]")
    return seed, schema


def load_json(path: pathlib.Path, label: str):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        fail(f"{label} not found: {path}")
    except (OSError, ValueError) as e:
        fail(f"could not read {label} {path}: {e}")


def json_path(parts) -> str:
    out = "$"
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def schema_store(schema_dir: pathlib.Path) -> dict:
    """Map $id -> schema for every parseable *.json in the schema dir."""
    store = {}
    for sf in sorted(schema_dir.glob("*.json")):
        try:
            data = json.loads(sf.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("$id"):
            store[data["$id"]] = data
    return store


def build_validator(schema: dict, schema_dir: pathlib.Path):
    """Raises ImportError when jsonschema is not importable."""
    from jsonschema import Draft7Validator

    store = schema_store(schema_dir)
    try:
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT7
    except ImportError:
        # jsonschema < 4.18: no referencing package, use RefResolver.
        from jsonschema import RefResolver

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            resolver = RefResolver(base_uri=schema.get("$id", ""), referrer=schema, store=store)
        return Draft7Validator(schema, resolver=resolver)

    registry = Registry().with_resources(
        (sid, Resource.from_contents(data, default_specification=DRAFT7))
        for sid, data in store.items()
    )
    return Draft7Validator(schema, registry=registry)


def fallback_errors(seed, schema: dict):
    """Top-level required keys and the status enum only."""
    if not isinstance(seed, dict):
        return [("$", "seed must be a JSON object")]
    errors = [
        ("$", f"'{key}' is a required property")
        for key in schema.get("required", [])
        if key not in seed
    ]
    allowed = schema.get("properties", {}).get("status", {}).get("enum")
    if allowed and "status" in seed and seed["status"] not in allowed:
        errors.append(("$.status", f"{json.dumps(seed['status'])} is not one of {allowed}"))
    return errors


def main():
    seed_path, schema_path = parse_args(sys.argv[1:])
    schema = load_json(schema_path, "schema")
    seed = load_json(seed_path, "seed")

    try:
        validator = build_validator(schema, schema_path.resolve().parent)
    except ImportError:
        print(
            "WARNING: jsonschema not importable. Full validation skipped; "
            "checked top-level required fields and the status enum only.",
            file=sys.stderr,
        )
        errors = fallback_errors(seed, schema)
    else:
        try:
            raw = sorted(
                validator.iter_errors(seed),
                key=lambda e: [str(p) for p in e.absolute_path],
            )
        except Exception as e:  # unresolvable $ref, malformed schema
            fail(f"could not validate {seed_path}: {e}")
        errors = [(json_path(e.absolute_path), e.message) for e in raw]

    if errors:
        for path, msg in errors[:MAX_ERRORS]:
            print(f"ERROR: {path}: {msg}", file=sys.stderr)
        if len(errors) > MAX_ERRORS:
            print(f"ERROR: $: {len(errors) - MAX_ERRORS} more violations not shown", file=sys.stderr)
        sys.exit(1)

    print(f"OK: {seed_path} valid")


if __name__ == "__main__":
    main()
