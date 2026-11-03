"""Guard: seed-bank/ must never be selected by the real publish allowlist."""
import copy
import importlib.util
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2]
SCRIPT = SHARED / "scripts" / "publish" / "publish-toolkit.py"
spec = importlib.util.spec_from_file_location("publish_toolkit", SCRIPT)
pt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt)

MANIFEST = pt.load_manifest(SHARED / "publish.json")
SEED_BANK_PATHS = ["seed-bank/", "seed-bank", "seed-bank/example.json"]


def selected(path, manifest):
    return pt.select([path], manifest) == [path]


def test_seed_bank_not_selected_by_real_manifest():
    for path in SEED_BANK_PATHS:
        assert not selected(path, MANIFEST), f"{path} would be published"


def test_no_include_pattern_matches_seed_bank():
    for pattern in MANIFEST["include"]:
        for path in SEED_BANK_PATHS:
            assert not pt.matches(path, pattern), f"include {pattern!r} matches {path}"


def test_negative_control_included_entry_still_matches():
    assert "skills/" in MANIFEST["include"]
    assert selected("skills/tdd/SKILL.md", MANIFEST)


def test_guard_fails_on_scratch_manifest_with_seed_bank_include():
    for extra in ("seed-bank/", "seed-bank/*", "*.json"):
        scratch = copy.deepcopy(MANIFEST)
        scratch["include"].append(extra)
        assert selected("seed-bank/example.json", scratch), extra
        assert any(
            pt.matches(p, pat)
            for pat in scratch["include"]
            for p in SEED_BANK_PATHS
        ), extra
    # the real manifest is untouched
    assert not selected("seed-bank/example.json", MANIFEST)
