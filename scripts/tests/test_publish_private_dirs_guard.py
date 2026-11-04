"""Guard: seed-bank/ and attribution/ must never be selected by the real publish allowlist."""
import copy
import importlib.util
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2]
SCRIPT = SHARED / "scripts" / "publish" / "publish-toolkit.py"
spec = importlib.util.spec_from_file_location("publish_toolkit", SCRIPT)
pt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt)

sys.path.insert(0, str(SHARED / "scripts" / "shared"))
from change_records_path import change_records_path  # noqa: E402

MANIFEST = pt.load_manifest(SHARED / "publish.json")
CHANGE_RECORD = "seed-bank/changes/chg-0123456789ab.json"
SEED_BANK_PATHS = [
    "seed-bank/",
    "seed-bank",
    "seed-bank/example.json",
    "seed-bank/changes/",
    CHANGE_RECORD,
]
ATTRIBUTION_RECORD = "attribution/change-records/chg-0123456789ab.json"
ATTRIBUTION_PATHS = [
    "attribution/",
    "attribution",
    "attribution/change-records/",
    ATTRIBUTION_RECORD,
]
PRIVATE_PATHS = SEED_BANK_PATHS + ATTRIBUTION_PATHS


def selected(path, manifest):
    return pt.select([path], manifest) == [path]


def test_private_dirs_not_selected_by_real_manifest():
    for path in PRIVATE_PATHS:
        assert not selected(path, MANIFEST), f"{path} would be published"


def test_no_include_pattern_matches_private_dirs():
    for pattern in MANIFEST["include"]:
        for path in PRIVATE_PATHS:
            assert not pt.matches(path, pattern), f"include {pattern!r} matches {path}"


def test_change_record_files_not_selected_alongside_published_files():
    paths = [
        "skills/tdd/SKILL.md",
        CHANGE_RECORD,
        ATTRIBUTION_RECORD,
        "contracts/change-record-schema.json",
    ]
    assert pt.select(paths, MANIFEST) == [paths[0], paths[3]]


def test_real_change_records_on_disk_are_never_selected():
    records = sorted(change_records_path().glob("*.json"))
    assert records, "change-records folder is empty"
    for rec in records:
        rel = rec.relative_to(SHARED.parent).as_posix()
        assert not selected(rel, MANIFEST), f"{rel} would be published"


def test_negative_control_included_entry_still_matches():
    assert "skills/" in MANIFEST["include"]
    assert selected("skills/tdd/SKILL.md", MANIFEST)


def test_guard_fails_on_scratch_manifest_with_private_dir_include():
    for extra in ("seed-bank/", "seed-bank/*", "*.json"):
        scratch = copy.deepcopy(MANIFEST)
        scratch["include"].append(extra)
        assert selected("seed-bank/example.json", scratch), extra
        assert selected(CHANGE_RECORD, scratch), extra
        assert any(
            pt.matches(p, pat)
            for pat in scratch["include"]
            for p in SEED_BANK_PATHS
        ), extra
    for extra in ("attribution/", "attribution/*", "*.json"):
        scratch = copy.deepcopy(MANIFEST)
        scratch["include"].append(extra)
        assert selected(ATTRIBUTION_RECORD, scratch), extra
        assert any(
            pt.matches(p, pat)
            for pat in scratch["include"]
            for p in ATTRIBUTION_PATHS
        ), extra
    # the real manifest is untouched
    assert not selected("seed-bank/example.json", MANIFEST)
    assert not selected(ATTRIBUTION_RECORD, MANIFEST)
