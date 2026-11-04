# test_protected_paths.py: tests for protected-paths/check-protected-paths.py and its list file
# What: every list entry yields needs-grill; unprotected and empty diffs do not; list file is a member; real entries exist
# When: run by run-all-tests.sh; extend when protected_paths.txt gains entries
import re
import subprocess
import sys
from pathlib import Path

import pytest

SHARED = Path(__file__).resolve().parents[2]
REPO = SHARED.parent
DIR = SHARED / "scripts" / "protected-paths"
SCRIPT = DIR / "check-protected-paths.py"
LIST = DIR / "protected_paths.txt"


def entries():
    out = []
    for line in LIST.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        anticipated = bool(re.search(r"#\s*anticipated", line))
        pat = re.sub(r"\s+#.*$", "", line).strip()
        out.append((pat, anticipated))
    return out


def sample(pat):
    return pat + "x.txt" if pat.endswith("/") else pat.replace("*", "x")


def run(*args, stdin=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin or "",
                          capture_output=True, text=True)


@pytest.mark.parametrize("pat", [p for p, _ in entries()])
def test_each_entry_is_needs_grill(pat):
    r = run(stdin=f"README.md\n{sample(pat)}\n")
    assert r.returncode == 1
    assert r.stdout.splitlines()[0] == "needs-grill"
    assert sample(pat) in r.stdout


@pytest.mark.parametrize("pat", [p for p, a in entries() if not a])
def test_non_anticipated_entries_exist(pat):
    assert (REPO / pat).exists()


DETECTOR_PATHS = [
    "claude-code-shared/scripts/learning/tag-attribution.py",
    "claude-code-shared/scripts/learning/load-change-diff.py",
    "claude-code-shared/scripts/shared/md_headings.py",
    "claude-code-shared/scripts/usage/drift-alert.py",
    "claude-code-shared/scripts/attribution/write-change-records.py",
    "claude-code-shared/scripts/attribution/change_records_commit.py",
    "claude-code-shared/scripts/attribution/commit-change-records.sh",
    "claude-code-shared/scripts/attribution/change-revert.sh",
]


@pytest.mark.parametrize("path", DETECTOR_PATHS)
def test_change_attribution_detector_path_is_protected(path):
    r = run(path)
    assert r.returncode == 1
    assert path in r.stdout
    assert (REPO / path).exists()
    assert (path, False) in entries()


PHASE1_GRILL_PATHS = [
    "claude-code-shared/scripts/learning/validate-escape-point.py",
    "claude-code-shared/scripts/usage/correction-rate.py",
    "claude-code-shared/scripts/usage/weekly-usage-report.sh",
    "claude-code-shared/scripts/attribution/ci-snapshot-diff.py",
    "claude-code-shared/scripts/attribution/ci-post-snapshot-comment.sh",
    ".githooks/post-merge",
    ".github/workflows/test.yml",
]


@pytest.mark.parametrize("path", PHASE1_GRILL_PATHS)
def test_phase1_grill_path_is_protected(path):
    r = run(path)
    assert r.returncode == 1
    assert path in r.stdout


def test_phase1_grill_entries_listed():
    listed = [p for p, _ in entries()]
    for e in PHASE1_GRILL_PATHS[:5] + [".githooks/", ".github/workflows/"]:
        assert e in listed


def test_attribution_dir_entry_present_and_bank_dir_gone():
    listed = [p for p, _ in entries()]
    assert "claude-code-shared/scripts/attribution/" in listed
    assert not any("scripts/bank" in p for p in listed)


def test_root_attribution_folder_is_protected():
    assert "attribution/" in [p for p, _ in entries()]
    r = run("attribution/change-records/chg-dd15bc0672ce.json")
    assert r.returncode == 1
    assert "attribution/change-records/chg-dd15bc0672ce.json" in r.stdout


def test_seed_bank_is_not_protected():
    assert not any(p.startswith("seed-bank") for p, _ in entries())
    r = run("seed-bank/example.json")
    assert r.returncode == 0
    assert "needs-grill" not in r.stdout


def test_args_form_works():
    assert run("run-all-tests.sh").returncode == 1


def test_unprotected_only_is_ok():
    r = run(stdin="README.md\nclaude-code-shared/skills/tdd/SKILL.md\n")
    assert r.returncode == 0
    assert "needs-grill" not in r.stdout


def test_empty_diff_is_ok():
    r = run(stdin="")
    assert r.returncode == 0
    assert "needs-grill" not in r.stdout


def test_list_file_itself_is_protected():
    rel = LIST.relative_to(REPO).as_posix()
    assert run(rel).returncode == 1
    assert (rel, False) in entries()


def test_missing_list_is_error():
    assert run("--list", "/nonexistent", "a").returncode == 2
