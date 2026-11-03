"""Tests for the capture-issue contract and scripts/capture/validate-capture.py."""

import json
import pathlib
import subprocess
import sys

SHARED = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = SHARED / "scripts" / "capture" / "validate-capture.py"
SCHEMA = SHARED / "contracts" / "capture-issue-schema.json"
CONTRACT = SHARED / "contracts" / "capture-issue-contract.md"
OVERVIEW = SHARED / "contracts" / "OVERVIEW.md"
VALIDATE_SCHEMA = SHARED / "scripts" / "shared" / "validate-schema.sh"


def base(**over):
    d = {
        "title": "Fix the thing",
        "label": "fix",
        "sealed": False,
        "what": "The thing is broken.",
        "why": "Hit it while building.",
        "where": "repo/file.py",
        "session": "sess-123",
    }
    d.update(over)
    return d


def run(doc, tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
    return subprocess.run([sys.executable, str(SCRIPT), str(p)], capture_output=True, text=True)


def headings(out):
    return [l for l in out.splitlines() if l.startswith("## ")]


def test_unsealed_renders_four_sections_in_order(tmp_path):
    r = run(base(), tmp_path)
    assert r.returncode == 0, r.stderr
    assert headings(r.stdout) == ["## What", "## Why / trigger", "## Where", "## Session"]
    assert "repo/file.py" in r.stdout and "sess-123" in r.stdout


def test_sealed_empty_where_session_renders_what_and_why_only(tmp_path):
    r = run(base(sealed=True, where="", session=""), tmp_path)
    assert r.returncode == 0, r.stderr
    assert headings(r.stdout) == ["## What", "## Why / trigger"]


def test_sealed_with_absent_where_session_validates(tmp_path):
    d = base(sealed=True)
    del d["where"], d["session"]
    r = run(d, tmp_path)
    assert r.returncode == 0, r.stderr
    assert headings(r.stdout) == ["## What", "## Why / trigger"]


def test_sealed_with_nonempty_where_fails_clearly(tmp_path):
    r = run(base(sealed=True, session=""), tmp_path)
    assert r.returncode != 0
    assert r.stdout == ""
    assert "sealed" in r.stderr and "'where'" in r.stderr


def test_sealed_with_nonempty_session_fails_clearly(tmp_path):
    r = run(base(sealed=True, where=""), tmp_path)
    assert r.returncode != 0
    assert "sealed" in r.stderr and "'session'" in r.stderr


def test_missing_title_fails(tmp_path):
    d = base()
    del d["title"]
    r = run(d, tmp_path)
    assert r.returncode != 0
    assert "title" in r.stderr


def test_blank_title_fails(tmp_path):
    assert run(base(title="   "), tmp_path).returncode != 0


def test_invalid_label_fails(tmp_path):
    r = run(base(label="bug"), tmp_path)
    assert r.returncode != 0
    assert "label" in r.stderr


def test_what_over_three_lines_fails(tmp_path):
    r = run(base(what="a\nb\nc\nd"), tmp_path)
    assert r.returncode != 0
    assert "1-3 lines" in r.stderr


def test_what_three_lines_ok(tmp_path):
    assert run(base(what="a\nb\nc"), tmp_path).returncode == 0


def test_unknown_field_and_bad_json_fail(tmp_path):
    assert run(base(extra=1), tmp_path).returncode != 0
    assert run("{not json", tmp_path).returncode != 0


def test_stdin_input():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "-"], input=json.dumps(base()), capture_output=True, text=True
    )
    assert r.returncode == 0


def test_schema_examples_validate():
    r = subprocess.run(["bash", str(VALIDATE_SCHEMA), str(SCHEMA)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_contract_and_overview_reference_schema():
    assert "capture-issue-schema.json" in CONTRACT.read_text()
    assert "capture-issue-schema.json" in OVERVIEW.read_text()
    assert "capture-issue-contract.md" in OVERVIEW.read_text()
