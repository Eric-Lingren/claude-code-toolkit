"""Tests for update-learning.py and the rejected/merged_sha/reason/clustered schema extension."""

import json
import os
import pathlib
import subprocess
import sys

import pytest
from jsonschema import Draft7Validator

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA = SHARED / "contracts" / "learning-schema.json"
CONTRACT = SHARED / "contracts" / "learning-contract.md"
UPDATE = SHARED / "scripts" / "learning" / "update-learning.py"
REAL_JSONL = SHARED / "learnings" / "unified-learnings.jsonl"

ROW_ID = "11111111-2222-4333-8444-555555555555"

BASE_ROW = {
    "schema_version": "2",
    "id": ROW_ID,
    "timestamp": "2026-11-04T12:00:00Z",
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
    "evidence": [{"source": "transcript", "ref": "/tmp/t.md", "quote": "do Y"}],
    "confidence": "confirmed",
}


@pytest.fixture(scope="module")
def validator():
    return Draft7Validator(json.loads(SCHEMA.read_text()))


@pytest.fixture
def dest(tmp_path):
    row = dict(BASE_ROW)
    (tmp_path / "unified-learnings.jsonl").write_text(json.dumps(row) + "\n")
    return tmp_path


def run_update(dest, *args):
    env = dict(os.environ, LOG_LEARNING_DEST=str(dest))
    return subprocess.run(
        [sys.executable, str(UPDATE), "--id", ROW_ID, *args],
        capture_output=True, text=True, env=env,
    )


def read_row(dest):
    lines = [l for l in (dest / "unified-learnings.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


class TestRejected:
    def test_sets_rejected_with_reason(self, dest, validator):
        r = run_update(dest, "--status", "rejected", "--reason", "Closed: not worth the churn")
        assert r.returncode == 0, r.stderr
        row = read_row(dest)
        assert row["status"] == "rejected"
        assert row["reason"] == "Closed: not worth the churn"
        assert list(validator.iter_errors(row)) == []

    def test_rejected_without_reason_fails(self, dest):
        r = run_update(dest, "--status", "rejected")
        assert r.returncode == 1
        assert "reason" in r.stderr
        assert "status" not in read_row(dest)

    def test_schema_rejected_requires_reason(self, validator):
        row = dict(BASE_ROW, status="rejected")
        assert list(validator.iter_errors(row)) != []

    def test_empty_reason_fails(self, dest):
        r = run_update(dest, "--status", "rejected", "--reason", "   ")
        assert r.returncode == 1


class TestMergedSha:
    def test_sets_merged_sha_on_applied(self, dest, validator):
        sha = "4c6e70c59d6e7d64f766b58eb2d04dc88011719d"
        r = run_update(dest, "--status", "applied", "--merged-sha", sha)
        assert r.returncode == 0, r.stderr
        row = read_row(dest)
        assert row["status"] == "applied"
        assert row["merged_sha"] == sha
        assert list(validator.iter_errors(row)) == []

    def test_bad_merged_sha_fails(self, dest):
        r = run_update(dest, "--status", "applied", "--merged-sha", "not-a-sha")
        assert r.returncode == 1
        assert "merged_sha" in r.stderr
        assert "status" not in read_row(dest)


class TestClustered:
    def test_row_with_clustered_state_validates(self, validator):
        row = dict(BASE_ROW, status="clustered:123")
        assert list(validator.iter_errors(row)) == []

    @pytest.mark.parametrize("bad", ["clustered:", "clustered:abc", "clustered:0", "clustered:12x", "clustered"])
    def test_malformed_clustered_state_fails(self, validator, bad):
        row = dict(BASE_ROW, status=bad)
        assert list(validator.iter_errors(row)) != []

    def test_cli_sets_clustered(self, dest, validator):
        r = run_update(dest, "--status", "clustered:42")
        assert r.returncode == 0, r.stderr
        row = read_row(dest)
        assert row["status"] == "clustered:42"
        assert list(validator.iter_errors(row)) == []

    def test_cli_rejects_bad_clustered(self, dest):
        r = run_update(dest, "--status", "clustered:nope")
        assert r.returncode == 1


class TestSchemaStrictness:
    def test_unknown_field_still_fails(self, validator):
        row = dict(BASE_ROW, bogus_field="x")
        assert list(validator.iter_errors(row)) != []

    def test_existing_statuses_still_valid(self, validator):
        for s in ("captured", "applied", "stale", "invalid"):
            assert list(validator.iter_errors(dict(BASE_ROW, status=s))) == []

    def test_unknown_status_fails(self, validator):
        assert list(validator.iter_errors(dict(BASE_ROW, status="bogus"))) != []


class TestExistingRows:
    def test_every_existing_row_validates(self, validator):
        bad = []
        n = 0
        for i, line in enumerate(REAL_JSONL.read_text().splitlines()):
            if not line.strip():
                continue
            n += 1
            errs = list(validator.iter_errors(json.loads(line)))
            if errs:
                bad.append((i + 1, errs[0].message[:80]))
        assert n > 0
        assert bad == []


class TestContractDoc:
    @pytest.fixture(scope="class")
    def doc(self):
        return CONTRACT.read_text()

    def test_documents_new_status_and_fields(self, doc):
        for token in ("rejected", "merged_sha", "reason", "clustered:<issue#>"):
            assert token in doc

    def test_documents_never_re_propose_rule(self, doc):
        assert "never re-proposed" in doc.lower() or "never re-propose" in doc.lower()
