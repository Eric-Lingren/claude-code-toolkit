"""Validation of the committed attribution/change-records/ records (the 60-day backfill and anything later).

Every record must satisfy change-record-schema.json, have a unique change_id that matches its
file name, point at a merged_sha that exists in git, and be dated inside the backfill window or
after it. Only skill, agent, script, hook and contract changes get records.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

SHARED = Path(__file__).resolve().parents[2]
DOTFILES = SHARED.parent
REPO = SHARED.parent
SCHEMA = json.loads((SHARED / "contracts" / "change-record-schema.json").read_text())
RECORDS_DIR = DOTFILES / "attribution" / "change-records"

# Backfill ran on 2026-11-03 with a 60-day window.
WINDOW_START = datetime(2026, 9, 4, tzinfo=timezone.utc)
RECORD_KINDS = {"skill", "agent", "contract", "hook", "script"}
UNIT_PREFIXES = tuple(f"{k}:" for k in RECORD_KINDS)


def load_records():
    return [(p, json.loads(p.read_text())) for p in sorted(RECORDS_DIR.glob("*.json"))]


RECORDS = load_records()


def git_ok(*args):
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, text=True,
                          check=False)


def test_backfill_records_exist():
    assert RECORDS, f"no change records under {RECORDS_DIR}"


@pytest.mark.parametrize("path,record", RECORDS, ids=[p.stem for p, _ in RECORDS])
def test_record_matches_schema(path, record):
    jsonschema.validate(record, SCHEMA)


def test_change_ids_unique_and_match_file_names():
    ids = [r["change_id"] for _, r in RECORDS]
    assert len(ids) == len(set(ids)), "duplicate change_id"
    for path, record in RECORDS:
        assert path.stem == record["change_id"]
        assert record["change_id"] == f"chg-{record['merged_sha'][:12]}"


def test_merged_shas_unique():
    shas = [r["merged_sha"] for _, r in RECORDS]
    assert len(shas) == len(set(shas)), "two records for one merge"


def test_merged_sha_exists_in_git():
    if git_ok("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone: older merge commits are not available")
    missing = [r["merged_sha"] for _, r in RECORDS
               if git_ok("cat-file", "-e", f"{r['merged_sha']}^{{commit}}").returncode != 0]
    assert not missing, f"merged_sha not in git: {missing}"


def test_merged_at_within_backfill_window_or_later():
    too_old = [r["change_id"] for _, r in RECORDS
               if datetime.fromisoformat(r["merged_at"]) < WINDOW_START]
    assert not too_old, f"merged before {WINDOW_START.date()}: {too_old}"


def test_only_skill_agent_script_hook_contract_changes_have_records():
    for _, r in RECORDS:
        assert r["kind"] in RECORD_KINDS, r["change_id"]
        assert r["touched_units"], f"{r['change_id']} has no touched_units"
        assert all(u.startswith(UNIT_PREFIXES) for u in r["touched_units"]), r["change_id"]
