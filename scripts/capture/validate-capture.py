#!/usr/bin/env python3
"""validate-capture.py — validate a capture-issue JSON document and render its issue body.

Usage:
  validate-capture.py <capture.json | ->

Validates the input against contracts/capture-issue-schema.json, then prints the
fixed markdown body to stdout: What, Why / trigger, Where, Session. A sealed
capture renders only What and Why / trigger.

Exit codes:
  0  valid; markdown body on stdout
  1  invalid or unreadable input; errors on stderr, nothing on stdout
"""

import json
import pathlib
import sys

SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "contracts" / "capture-issue-schema.json"
)


def load_input(arg):
    try:
        raw = sys.stdin.read() if arg == "-" else pathlib.Path(arg).read_text()
    except OSError as e:
        raise ValueError(f"could not read {arg}: {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}")


def collect_errors(data, schema):
    from jsonschema import Draft7Validator

    errors = []
    for err in sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path]):
        path = list(err.absolute_path)
        field = path[0] if path else None
        if err.validator == "maxLength" and field in ("where", "session"):
            errors.append(
                f"sealed capture must have empty '{field}' (got a non-empty value); "
                f"clear '{field}' or set sealed to false"
            )
        elif err.validator == "required":
            errors.append(err.message)
        elif err.validator == "enum" and field == "label":
            errors.append(f"label must be 'idea' or 'fix', got {json.dumps(err.instance)}")
        elif err.validator == "pattern" and field in ("title", "what"):
            errors.append(f"{field} must not be empty")
        else:
            where = ".".join(str(p) for p in path) or "(root)"
            errors.append(f"{where}: {err.message}")
    what = data.get("what")
    if isinstance(what, str) and what.strip() and len(what.strip().splitlines()) > 3:
        errors.append("what must be 1-3 lines")
    return errors


def render(data):
    def val(key):
        v = (data.get(key) or "").strip()
        return v if v else "n/a"

    parts = [f"## What\n{data['what'].strip()}", f"## Why / trigger\n{data['why'].strip()}"]
    if not data["sealed"]:
        parts.append(f"## Where\n{val('where')}")
        parts.append(f"## Session\n{val('session')}")
    return "\n\n".join(parts) + "\n"


def main(argv):
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        print("ERROR: jsonschema not installed. Run: pip install 'jsonschema[format]'", file=sys.stderr)
        return 1
    try:
        data = load_input(argv[1])
        schema = json.loads(SCHEMA_PATH.read_text())
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("ERROR: capture must be a JSON object", file=sys.stderr)
        return 1
    errors = collect_errors(data, schema)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
