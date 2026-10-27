"""Tests for learning-schema.json and the log-learning.py writer."""

import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import uuid

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA = SHARED / "contracts" / "learning-schema.json"
LOG_LEARNING = SHARED / "scripts" / "learning" / "log-learning.py"
LEARNINGS_DIR = SHARED / "learnings"

MINIMAL_SELF = {
    "type": "self",
    "reported_by": "debug",
    "improves": "debug",
    "improves_type": "skill",
    "cause": "general_best_practice",
    "cause_label": None,
    "problem": "Did X wrong.",
    "why_missed": "Not in SKILL.md.",
    "lesson": "Always do Y.",
    "fix": None,
    "evidence": [{"source": "transcript", "ref": "/tmp/transcript.md", "quote": "User said: 'do Y'"}],
    "confidence": "confirmed",
}

MINIMAL_ATTRIBUTION = {
    "type": "attribution",
    "reported_by": "debug",
    "improves": "to-tasks",
    "improves_type": "skill",
    "cause": "requirement_lost_between_docs",
    "cause_label": None,
    "problem": "Criterion lost between seed and tasks.",
    "why_missed": "to-tasks never saw the criterion.",
    "lesson": "to-tasks must check seed for implicit constraints.",
    "fix": "Add step to to-tasks SKILL.md.",
    "evidence": [{"source": "artifact", "ref": "docs/seeds/foo.json", "quote": "criterion X present in seed"}],
    "trace": {"seed": "docs/seeds/foo.json", "branch": "feat/bar"},
    "confidence": "candidate",
}


_SERVER_STUB = {
    "schema_version": "2",
    "id": "a1b2c3d4-e5f6-4890-abcd-ef1234567890",
    "timestamp": "2026-06-05T12:00:00Z",
}


def _load_schema():
    return json.loads(SCHEMA.read_text())


def _build_validator(schema):
    from jsonschema import Draft7Validator
    return Draft7Validator(schema)


def _validate(payload):
    """Validate a caller payload by injecting server fields first."""
    complete = {**_SERVER_STUB, **payload}
    schema = _load_schema()
    validator = _build_validator(schema)
    return list(validator.iter_errors(complete))


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestSchemaV2Structure:
    def test_schema_file_exists(self):
        assert SCHEMA.exists(), f"Schema not found: {SCHEMA}"

    def test_schema_is_valid_json(self):
        schema = _load_schema()
        assert isinstance(schema, dict)
        assert "$schema" in schema


class TestMinimalSelfRecord:
    def test_validates_minimal_self_record(self):
        errors = _validate(MINIMAL_SELF)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"

    def test_self_record_with_cause_other_requires_cause_label(self):
        record = {**MINIMAL_SELF, "cause": "other", "cause_label": "my_custom_cause"}
        errors = _validate(record)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"

    def test_self_record_with_cause_other_and_null_cause_label_fails(self):
        record = {**MINIMAL_SELF, "cause": "other", "cause_label": None}
        errors = _validate(record)
        assert len(errors) > 0, "Expected validation error for cause='other' with null cause_label"


class TestMinimalAttributionRecord:
    def test_validates_minimal_attribution_record(self):
        errors = _validate(MINIMAL_ATTRIBUTION)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"

    def test_attribution_with_null_improves(self):
        record = {**MINIMAL_ATTRIBUTION, "improves": None}
        errors = _validate(record)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"

    def test_attribution_with_all_trace_fields(self):
        record = {**MINIMAL_ATTRIBUTION, "trace": {
            "seed": "docs/seeds/foo.json",
            "tasks": "docs/tasks/foo.json",
            "task_id": "T-0001",
            "pr": "https://github.com/foo/bar/pull/1",
            "branch": "feat/bar",
        }}
        errors = _validate(record)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"


class TestRequiredFieldRejection:
    def test_rejects_missing_type(self):
        record = {k: v for k, v in MINIMAL_SELF.items() if k != "type"}
        errors = _validate(record)
        assert any("type" in e.message or "'type'" in str(e.path) for e in errors)

    def test_rejects_missing_reported_by(self):
        record = {k: v for k, v in MINIMAL_SELF.items() if k != "reported_by"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_missing_problem(self):
        record = {k: v for k, v in MINIMAL_SELF.items() if k != "problem"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_missing_evidence(self):
        record = {k: v for k, v in MINIMAL_SELF.items() if k != "evidence"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_invalid_type_value(self):
        record = {**MINIMAL_SELF, "type": "unknown"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_invalid_confidence_value(self):
        record = {**MINIMAL_SELF, "confidence": "maybe"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_invalid_improves_type(self):
        record = {**MINIMAL_SELF, "improves_type": "function"}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_evidence_item_missing_source(self):
        record = {**MINIMAL_SELF, "evidence": [{"ref": "/tmp/t.md", "quote": "x"}]}
        errors = _validate(record)
        assert len(errors) > 0

    def test_rejects_non_two_schema_version(self):
        """schema_version must be '2'; any other value should fail."""
        complete = {**_SERVER_STUB, **MINIMAL_SELF, "schema_version": "1"}
        schema = _load_schema()
        errors = list(_build_validator(schema).iter_errors(complete))
        assert len(errors) > 0


# ── Writer tests ───────────────────────────────────────────────────────────────

def _run_writer(payload: dict, learnings_dir: pathlib.Path = None) -> subprocess.CompletedProcess:
    env_override = {}
    if learnings_dir is not None:
        env_override["LOG_LEARNING_DEST"] = str(learnings_dir)
    import os
    env = {**os.environ, **env_override}
    return subprocess.run(
        [sys.executable, str(LOG_LEARNING)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


class TestWriterInjectsFields:
    def test_injects_schema_version(self, tmp_path):
        result = _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        line = (tmp_path / "unified-learnings.jsonl").read_text().strip()
        record = json.loads(line)
        assert record["schema_version"] == "2"

    def test_injects_timestamp(self, tmp_path):
        result = _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        assert result.returncode == 0
        record = json.loads((tmp_path / "unified-learnings.jsonl").read_text().strip())
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", record["timestamp"])

    def test_injects_uuid_id(self, tmp_path):
        result = _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        assert result.returncode == 0
        record = json.loads((tmp_path / "unified-learnings.jsonl").read_text().strip())
        assert "id" in record
        parsed = uuid.UUID(record["id"])  # raises if invalid
        assert parsed.version == 4

    def test_caller_cannot_override_schema_version(self, tmp_path):
        payload = {**MINIMAL_SELF, "schema_version": "99"}
        result = _run_writer(payload, learnings_dir=tmp_path)
        assert result.returncode == 1
        assert "schema_version" in result.stderr or "id" in result.stderr or "additional" in result.stderr.lower()

    def test_caller_cannot_override_id(self, tmp_path):
        payload = {**MINIMAL_SELF, "id": "fake-id"}
        result = _run_writer(payload, learnings_dir=tmp_path)
        assert result.returncode == 1


class TestWriterAppendsToUnified:
    def test_appends_to_unified_jsonl(self, tmp_path):
        result = _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        unified = tmp_path / "unified-learnings.jsonl"
        assert unified.exists()
        lines = unified.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "self"

    def test_appends_multiple_entries(self, tmp_path):
        _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        _run_writer(MINIMAL_ATTRIBUTION, learnings_dir=tmp_path)
        lines = (tmp_path / "unified-learnings.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_written_record_validates_against_schema(self, tmp_path):
        result = _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        assert result.returncode == 0
        line = (tmp_path / "unified-learnings.jsonl").read_text().strip()
        record = json.loads(line)
        errors = _validate(record)
        assert errors == [], f"Written record fails schema: {[e.message for e in errors]}"

    def test_does_not_write_to_per_skill_file(self, tmp_path):
        _run_writer(MINIMAL_SELF, learnings_dir=tmp_path)
        per_skill = tmp_path / "debug.jsonl"
        assert not per_skill.exists(), "Writer must not create per-skill files"


class TestWriterRejectsMalformed:
    def test_rejects_empty_stdin(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(LOG_LEARNING)],
            input="",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_rejects_invalid_json(self, tmp_path):
        import os
        env = {**os.environ, "LOG_LEARNING_DEST": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(LOG_LEARNING)],
            input="{not json}",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1

    def test_rejects_missing_required_field(self, tmp_path):
        payload = {k: v for k, v in MINIMAL_SELF.items() if k != "problem"}
        result = _run_writer(payload, learnings_dir=tmp_path)
        assert result.returncode == 1
        assert "ERROR" in result.stderr

    def test_rejects_invalid_type_value(self, tmp_path):
        payload = {**MINIMAL_SELF, "type": "bogus"}
        result = _run_writer(payload, learnings_dir=tmp_path)
        assert result.returncode == 1
