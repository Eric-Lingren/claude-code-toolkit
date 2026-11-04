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


# ── Attribution spine: dotfiles_sha / dotfiles_dirty / session_id ─────────────

SESSION_UUID = "3df6cc36-8bb6-485e-bab4-5e773c4a29e5"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def dotfiles_repo(tmp_path):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run_stamped(payload, learnings_dir, repo):
    import os
    env = {
        **os.environ,
        "LOG_LEARNING_DEST": str(learnings_dir),
        "LOG_LEARNING_DOTFILES_DIR": str(repo),
    }
    return subprocess.run(
        [sys.executable, str(LOG_LEARNING)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def _written(learnings_dir):
    lines = (learnings_dir / "unified-learnings.jsonl").read_text().strip().split("\n")
    return [json.loads(l) for l in lines]


def _with_ref(ref):
    return {**MINIMAL_SELF, "evidence": [{"source": "transcript", "ref": ref, "quote": "q"}]}


class TestWriterStampsAttribution:
    def test_stamps_sha_dirty_and_session_id(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        ref = f".claude/projects/-p/{SESSION_UUID}.jsonl"
        result = _run_stamped(_with_ref(ref), out, dotfiles_repo)
        assert result.returncode == 0, result.stderr
        rec = _written(out)[0]
        assert rec["dotfiles_sha"] == _head(dotfiles_repo)
        assert isinstance(rec["dotfiles_dirty"], bool)
        assert rec["session_id"] == SESSION_UUID

    def test_dirty_false_when_clean(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        assert _run_stamped(MINIMAL_SELF, out, dotfiles_repo).returncode == 0
        assert _written(out)[0]["dotfiles_dirty"] is False

    def test_dirty_true_with_modified_tracked_file(self, tmp_path, dotfiles_repo):
        (dotfiles_repo / "a.txt").write_text("changed\n")
        out = tmp_path / "out"
        assert _run_stamped(MINIMAL_SELF, out, dotfiles_repo).returncode == 0
        assert _written(out)[0]["dotfiles_dirty"] is True

    def test_dirty_true_with_untracked_file(self, tmp_path, dotfiles_repo):
        (dotfiles_repo / "new.txt").write_text("x\n")
        out = tmp_path / "out"
        assert _run_stamped(MINIMAL_SELF, out, dotfiles_repo).returncode == 0
        assert _written(out)[0]["dotfiles_dirty"] is True

    def test_session_id_null_without_uuid_in_refs(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        assert _run_stamped(_with_ref("docs/seeds/foo.json"), out, dotfiles_repo).returncode == 0
        rec = _written(out)[0]
        assert "session_id" in rec and rec["session_id"] is None

    def test_session_id_taken_from_any_evidence_ref(self, tmp_path, dotfiles_repo):
        payload = {**MINIMAL_SELF, "evidence": [
            {"source": "artifact", "ref": "docs/seeds/foo.json", "quote": "q"},
            {"source": "transcript", "ref": f"/t/{SESSION_UUID.upper()}.jsonl", "quote": "q"},
        ]}
        out = tmp_path / "out"
        assert _run_stamped(payload, out, dotfiles_repo).returncode == 0
        assert _written(out)[0]["session_id"] == SESSION_UUID

    @pytest.mark.parametrize("field,value", [
        ("dotfiles_sha", "abc123"),
        ("dotfiles_dirty", False),
        ("session_id", SESSION_UUID),
    ])
    def test_caller_supplied_stamp_field_rejected(self, tmp_path, dotfiles_repo, field, value):
        out = tmp_path / "out"
        result = _run_stamped({**MINIMAL_SELF, field: value}, out, dotfiles_repo)
        assert result.returncode != 0
        assert field in result.stderr
        assert not (out / "unified-learnings.jsonl").exists()

    def test_written_stamped_record_validates(self, tmp_path, dotfiles_repo):
        out = tmp_path / "out"
        assert _run_stamped(_with_ref(f"/t/{SESSION_UUID}.jsonl"), out, dotfiles_repo).returncode == 0
        errors = list(_build_validator(_load_schema()).iter_errors(_written(out)[0]))
        assert errors == [], [e.message for e in errors]


class TestSchemaAttributionSpine:
    def test_accepts_optional_escape_point(self):
        rec = {**MINIMAL_ATTRIBUTION, "escape_point": {"path": "skills/to-tasks/SKILL.md", "section": "Step 3"}}
        assert _validate(rec) == []

    def test_accepts_escape_point_with_null_section(self):
        rec = {**MINIMAL_ATTRIBUTION, "escape_point": {"path": "skills/to-tasks/SKILL.md", "section": None}}
        assert _validate(rec) == []

    def test_rejects_escape_point_missing_path(self):
        rec = {**MINIMAL_ATTRIBUTION, "escape_point": {"section": "x"}}
        assert _validate(rec) != []

    def test_rejects_escape_point_extra_keys(self):
        rec = {**MINIMAL_ATTRIBUTION, "escape_point": {"path": "p", "section": None, "x": 1}}
        assert _validate(rec) != []

    def test_accepts_stamp_fields(self):
        rec = {**MINIMAL_SELF, "dotfiles_sha": "a" * 40, "dotfiles_dirty": True, "session_id": SESSION_UUID}
        assert _validate(rec) == []

    def test_accepts_null_session_id(self):
        assert _validate({**MINIMAL_SELF, "session_id": None}) == []

    def test_rejects_non_boolean_dirty(self):
        assert _validate({**MINIMAL_SELF, "dotfiles_dirty": "yes"}) != []

    def test_existing_rows_still_validate(self, tmp_path):
        # Representative rows written before the optional stamp/escape_point
        # fields existed, plus rows that carry them. Uses a tmp_path copy so the
        # test never depends on the live learnings file that sessions append to.
        base_ids = [
            "a1b2c3d4-e5f6-4890-abcd-ef1234567891",
            "a1b2c3d4-e5f6-4890-abcd-ef1234567892",
            "a1b2c3d4-e5f6-4890-abcd-ef1234567893",
            "a1b2c3d4-e5f6-4890-abcd-ef1234567894",
        ]
        payloads = [
            MINIMAL_SELF,
            MINIMAL_ATTRIBUTION,
            {**MINIMAL_SELF, "dotfiles_sha": "a" * 40, "dotfiles_dirty": False, "session_id": SESSION_UUID},
            {**MINIMAL_ATTRIBUTION, "escape_point": {"path": "skills/to-tasks/SKILL.md", "section": None}},
        ]
        path = tmp_path / "unified-learnings.jsonl"
        path.write_text(
            "\n".join(json.dumps({**_SERVER_STUB, "id": i, **p}) for i, p in zip(base_ids, payloads)) + "\n"
        )
        validator = _build_validator(_load_schema())
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        assert len(rows) == len(payloads)
        bad = [(r.get("id"), e.message) for r in rows for e in validator.iter_errors(r)]
        assert bad == [], bad[:5]


class TestAttributionTagFailSoft:
    def test_tagging_failure_still_writes_row_with_warning(self, tmp_path, dotfiles_repo):
        import os
        import shutil
        # Mirror the layout log-learning.py resolves from (scripts/learning, contracts)
        # with a tag-attribution.py stub that raises.
        shared = tmp_path / "shared"
        learning = shared / "scripts" / "learning"
        learning.mkdir(parents=True)
        shutil.copy(LOG_LEARNING, learning / "log-learning.py")
        (learning / "tag-attribution.py").write_text(
            "def load_changes(d):\n    return []\n"
            "def load_applied(p):\n    return []\n"
            "def tag_attribution(*a, **k):\n    raise RuntimeError('boom')\n"
        )
        (shared / "contracts").symlink_to(SHARED / "contracts")
        out = tmp_path / "out"
        env = {
            **os.environ,
            "LOG_LEARNING_DEST": str(out),
            "LOG_LEARNING_DOTFILES_DIR": str(dotfiles_repo),
        }
        result = subprocess.run(
            [sys.executable, str(learning / "log-learning.py")],
            input=json.dumps(MINIMAL_SELF), capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        rows = _written(out)
        assert len(rows) == 1
        assert "attribution_tag" not in rows[0]
        assert "WARNING: attribution_tag not set: boom" in result.stderr


class TestDefaultJudgeCommandIsLockedDown:
    def test_no_tools_and_hooks_disabled(self):
        import importlib.util
        import shlex
        spec = importlib.util.spec_from_file_location("log_learning_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        argv = shlex.split(mod.DEFAULT_JUDGE_CMD)
        assert argv[:2] == ["claude", "-p"]
        assert argv[argv.index("--tools") + 1] == ""
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings["disableAllHooks"] is True


class TestParseVerdict:
    @pytest.fixture
    def parse(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("log_learning_parse_mod", LOG_LEARNING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._parse_verdict

    def test_bare_json(self, parse):
        assert parse('{"verdict": "pass"}') == {"verdict": "pass"}

    def test_json_fenced(self, parse):
        text = 'Here you go:\n```json\n{"verdict": "pass", "reason": "grounded"}\n```\n'
        assert parse(text) == {"verdict": "pass", "reason": "grounded"}

    def test_plain_fence_without_language(self, parse):
        text = '```\n{"verdict": "rejected", "reason": "not in diff"}\n```'
        assert parse(text) == {"verdict": "rejected", "reason": "not in diff"}

    def test_verdict_embedded_in_prose(self, parse):
        text = 'After review my verdict is {"verdict": "pass", "reason": "ok"} as stated.'
        assert parse(text) == {"verdict": "pass", "reason": "ok"}

    def test_last_embedded_object_wins(self, parse):
        text = 'Draft {"verdict": "pass"} but final {"verdict": "rejected", "reason": "x"}'
        assert parse(text) == {"verdict": "rejected", "reason": "x"}

    def test_rejected_verdict_is_returned(self, parse):
        assert parse('{"verdict": "rejected", "reason": "no"}')["verdict"] == "rejected"

    @pytest.mark.parametrize("text", [
        "not json at all",
        "",
        '{"verdict": "maybe"}',
        '{"reason": "no verdict key"}',
        '["verdict", "pass"]',
        'prose {"verdict": "unknown"} more prose',
    ])
    def test_unreadable_or_invalid_returns_none(self, parse, text):
        assert parse(text) is None
