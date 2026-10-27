#!/usr/bin/env python3
"""validate-schema.py — validate JSON schema files and their embedded examples.

Usage:
  python validate-schema.py <contracts_dir> [--self-test] [schema_file ...]

  --self-test  Run the built-in cross-file $ref mechanism self-test
  schema_file  Path(s) to validate. Defaults to all <contracts_dir>/*.json.

For each schema file: validates every item in examples[] against the schema
itself, with cross-file $ref resolution from contracts/*.json.
"""

import sys
import json
import pathlib
import re


def get_jsonschema_version():
    try:
        from importlib.metadata import version
        v = version("jsonschema")
        m = re.match(r"(\d+)\.(\d+)", v)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    try:
        import jsonschema
        v = jsonschema.__version__
        m = re.match(r"(\d+)\.(\d+)", v)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 0, 0


def load_all_schemas(contracts_dir):
    schemas = {}
    for sf in sorted(contracts_dir.glob("*.json")):
        try:
            data = json.loads(sf.read_text())
        except Exception as e:
            print(f"ERROR: could not parse {sf}: {e}", file=sys.stderr)
            sys.exit(1)
        schema_id = data.get("$id")
        if schema_id:
            schemas[schema_id] = data
    return schemas


def build_validator_factory(all_schemas):
    major, minor = get_jsonschema_version()
    use_registry = (major > 4) or (major == 4 and minor >= 18)

    if use_registry:
        try:
            from referencing import Registry, Resource
            from jsonschema import Draft7Validator

            resources = []
            for schema_id, schema_data in all_schemas.items():
                try:
                    from referencing.jsonschema import DRAFT7
                    res = Resource.from_contents(schema_data, default_specification=DRAFT7)
                except (ImportError, Exception):
                    res = Resource.from_contents(schema_data)
                resources.append((schema_id, res))

            registry = Registry().with_resources(resources)
            print(f"INFO: referencing.Registry active (jsonschema {major}.{minor})")

            def make_validator(schema):
                return Draft7Validator(schema, registry=registry)

            return make_validator
        except ImportError:
            print(
                f"WARNING: referencing package not available, falling back to RefResolver",
                file=sys.stderr,
            )

    # Fallback to deprecated RefResolver (jsonschema < 4.18)
    from jsonschema import Draft7Validator, RefResolver

    store = dict(all_schemas)
    print(f"INFO: RefResolver active (jsonschema {major}.{minor})")

    def make_validator(schema):
        schema_id = schema.get("$id", "")
        resolver = RefResolver(base_uri=schema_id, referrer=schema, store=store)
        return Draft7Validator(schema, resolver=resolver)

    return make_validator


def get_schema_version_const(schema):
    """Extract the const value for schema_version from a schema's properties."""
    props = schema.get("properties", {})
    sv_schema = props.get("schema_version", {})
    return sv_schema.get("const")


def format_error(schema_filename, example_idx, err, schema):
    path_parts = list(err.absolute_path)

    # Special case: schema_version const mismatch — emit a clear, actionable message.
    if "schema_version" in path_parts and err.validator in ("const", "enum", "not"):
        const_val = get_schema_version_const(schema)
        if const_val is not None:
            got = err.instance if not path_parts or path_parts[-1] == "schema_version" else "?"
            return (
                f"ERROR: {schema_filename}: examples[{example_idx}]: schema_version mismatch "
                f"— got {json.dumps(got)}, expected {json.dumps(const_val)}. "
                f"Run /to-seed to regenerate."
            )

    path_str = ".".join(str(p) for p in path_parts) if path_parts else "(root)"
    return f"ERROR: {schema_filename}: examples[{example_idx}]: {path_str}: {err.message}"


def validate_schema_file(schema_path, make_validator, verbose=False):
    schema_path = pathlib.Path(schema_path)
    try:
        schema = json.loads(schema_path.read_text())
    except Exception as e:
        print(f"ERROR: {schema_path.name}: could not parse: {e}", file=sys.stderr)
        return False

    examples = schema.get("examples", [])
    if not examples:
        if verbose:
            print(f"OK: {schema_path.name}: no examples (schema still valid)")
        return True

    try:
        validator = make_validator(schema)
    except Exception as e:
        print(f"ERROR: {schema_path.name}: could not build validator: {e}", file=sys.stderr)
        return False

    ok = True
    for i, example in enumerate(examples):
        errors = sorted(validator.iter_errors(example), key=lambda e: list(e.absolute_path))
        if errors:
            for err in errors:
                print(format_error(schema_path.name, i, err, schema), file=sys.stderr)
            ok = False

    if ok:
        print(f"OK: {schema_path.name}: {len(examples)} example(s) valid")
    return ok


def run_self_test(make_validator):
    """Prove the cross-file $ref mechanism using an in-memory consumer schema."""
    print("\n=== Self-test: cross-file $ref mechanism ===")

    consumer = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "_self-test-consumer",
        "type": "object",
        "properties": {
            "producer": {"$ref": "provenance-schema#/$defs/producer"},
            "source": {"$ref": "provenance-schema#/$defs/source"},
        },
        "required": ["producer", "source"],
    }

    try:
        validator = make_validator(consumer)
    except Exception as e:
        print(f"FAIL: could not build consumer validator: {e}", file=sys.stderr)
        return False

    ok = True

    def check(label, instance, expect_valid):
        nonlocal ok
        errors = list(validator.iter_errors(instance))
        if expect_valid and errors:
            print(f"FAIL: {label}: unexpected errors: {errors[0].message}", file=sys.stderr)
            ok = False
        elif not expect_valid and not errors:
            print(f"FAIL: {label}: expected rejection but instance was accepted", file=sys.stderr)
            ok = False
        else:
            result = "PASS"
            print(f"{result}: {label}")

    check(
        "valid stamp (type:seed, ref:non-null)",
        {"producer": "to-seed", "source": {"type": "seed", "ref": "docs/seeds/20260603-foo.json"}},
        expect_valid=True,
    )
    check(
        "invalid stamp (type:seed, ref:null) — should be rejected",
        {"producer": "to-tasks", "source": {"type": "seed", "ref": None}},
        expect_valid=False,
    )
    check(
        "valid stamp (type:session, ref:null)",
        {"producer": "debug", "source": {"type": "session", "ref": None}},
        expect_valid=True,
    )
    check(
        "invalid stamp (type:session, ref:non-null) — should be rejected",
        {"producer": "debug", "source": {"type": "session", "ref": "docs/seeds/foo.json"}},
        expect_valid=False,
    )
    check(
        "null source (root of chain)",
        {"producer": "to-seed", "source": None},
        expect_valid=True,
    )
    check(
        "invalid producer (unknown-skill) — should be rejected",
        {"producer": "unknown-skill", "source": None},
        expect_valid=False,
    )
    check(
        "valid stamp (type:prd, ref:non-null)",
        {"producer": "to-tasks", "source": {"type": "prd", "ref": "docs/prd/20260603-foo.html"}},
        expect_valid=True,
    )
    check(
        "invalid stamp (type:prd, ref:null) — should be rejected",
        {"producer": "to-tasks", "source": {"type": "prd", "ref": None}},
        expect_valid=False,
    )

    print(f"\nSelf-test: {'PASSED' if ok else 'FAILED'}")
    return ok


def validate_instance(schema_path, data_path, make_validator):
    """Validate data_path against schema_path. Returns True on success."""
    schema_path = pathlib.Path(schema_path)
    data_path = pathlib.Path(data_path)

    try:
        schema = json.loads(schema_path.read_text())
    except Exception as e:
        print(f"ERROR: could not read schema {schema_path}: {e}", file=sys.stderr)
        return False

    try:
        data = json.loads(data_path.read_text())
    except Exception as e:
        print(f"ERROR: could not read data {data_path}: {e}", file=sys.stderr)
        return False

    try:
        validator = make_validator(schema)
    except Exception as e:
        print(f"ERROR: could not build validator for {schema_path.name}: {e}", file=sys.stderr)
        return False

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        for err in errors:
            path_parts = list(err.absolute_path)
            path_str = ".".join(str(p) for p in path_parts) if path_parts else "(root)"
            print(f"ERROR: {data_path.name}: {path_str}: {err.message}", file=sys.stderr)
        return False

    print(f"OK: {data_path.name} is valid against {schema_path.name}")
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(
            "Usage: validate-schema.py <contracts_dir> [--self-test] [schema_file ...]\n"
            "       validate-schema.py <contracts_dir> --instance <schema-file> <data-file>",
            file=sys.stderr,
        )
        sys.exit(1)

    contracts_dir = pathlib.Path(args[0])
    if not contracts_dir.is_dir():
        print(f"ERROR: contracts_dir not found: {contracts_dir}", file=sys.stderr)
        sys.exit(1)

    args = args[1:]

    # --instance mode: validate a data file against a named schema
    if "--instance" in args:
        idx = args.index("--instance")
        rest = args[idx + 1:]
        if len(rest) < 2:
            print(
                "ERROR: --instance requires exactly 2 arguments: <schema-file> <data-file>",
                file=sys.stderr,
            )
            sys.exit(1)
        schema_file, data_file = rest[0], rest[1]
        all_schemas = load_all_schemas(contracts_dir)
        make_validator = build_validator_factory(all_schemas)
        ok = validate_instance(schema_file, data_file, make_validator)
        if not ok:
            sys.exit(1)
        return

    # Unknown flags
    for arg in args:
        if arg.startswith("--") and arg not in ("--self-test",):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            sys.exit(1)

    self_test = "--self-test" in args
    args = [a for a in args if a != "--self-test"]

    schema_files = [pathlib.Path(f) for f in args] if args else sorted(contracts_dir.glob("*.json"))

    all_schemas = load_all_schemas(contracts_dir)
    make_validator = build_validator_factory(all_schemas)

    ok = True

    if self_test:
        ok = run_self_test(make_validator) and ok

    if schema_files:
        print()
    for sf in schema_files:
        ok = validate_schema_file(sf, make_validator, verbose=True) and ok

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
