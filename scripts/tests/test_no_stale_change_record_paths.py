"""Stale-path guard: no tracked file may still name the old change-records location.

A missed rename fails silently (writers and readers treat a missing folder as "no
records"), so this scans every git-tracked text file in scope for the old paths and
names file and line. It also checks the umbrella seed, that no script derives the
records path on its own, and that .zshrc defines no `bank` alias.

scan(root, allowlist) takes the repo root so tests can run it on a scratch repo.
"""
import re
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(__file__).resolve().parents[3]

# Scope: git-tracked text files under these roots (attribution/ so its allowlist entry is real).
SCOPE = ("claude-code-shared/", ".github/", ".githooks/", ".zshrc", "docs/", "attribution/")

# `bank.sh` / `scripts/bank` must be the old names, not a suffix of e.g. test-bank.sh.
STALE = re.compile(
    r"seed-bank/changes"
    r"|(?<![\w-])scripts/bank(?![\w-])"
    r"|(?<![\w-])bank revert"
    r"|(?<![\w-])bank\.sh"
    r"|(?<![A-Za-z0-9])BANK_(?:REPO|CHANGES_DIR|BASE_BRANCH)"
)

# (path or dir/ prefix, why it may still name the old paths)
ALLOWLIST = [
    ("claude-code-shared/docs/adr/0007-change-records-live-apart-from-the-seed-bank.md",
     "ADR 0007 explains the move, so it names the old paths"),
    ("docs/seeds/20261103-2223-change-records-apart-from-seed-bank.json",
     "the seed that describes this move"),
    ("docs/tasks/20261103-2230-change-records-apart-from-seed-bank.json",
     "the task file that describes this move"),
    ("docs/tasks/.logs/20261103-2230-change-records-apart-from-seed-bank/",
     "trace logs of the task run that did this move"),
    ("claude-code-shared/learnings/unified-learnings.jsonl",
     "append-only history; old text is a record of what was said then"),
    ("claude-code-shared/learnings/audit/hook-audit.jsonl",
     "append-only audit history; never rewritten"),
    ("attribution/change-records/",
     "committed records quote old paths in their own diffs and summaries"),
    ("docs/tasks/20261103-2043-pr-review.json",
     "left unrewritten on purpose: a run may be using this task file (follow-up to rewrite later)"),
    ("docs/tasks/.logs/20261103-2043-pr-review.json/",
     "trace logs of that same task file; left unrewritten with it"),
    ("claude-code-shared/scripts/attribution/rewrite-old-paths.py",
     "the rewriter must spell out the old paths it replaces"),
    ("claude-code-shared/scripts/tests/test_rewrite_old_paths.py",
     "feeds old paths to the rewriter as test input"),
    ("claude-code-shared/scripts/tests/test_publish_private_dirs_guard.py",
     "asserts the old seed-bank/changes path stays protected"),
    ("claude-code-shared/scripts/tests/test-change-revert.sh",
     "asserts scripts/bank no longer exists"),
    ("claude-code-shared/scripts/tests/test_ci_snapshot_diff.py",
     "asserts scripts/bank is absent from the snapshot"),
    ("claude-code-shared/scripts/tests/test_protected_paths.py",
     "asserts scripts/bank is absent from the protected list"),
    ("claude-code-shared/scripts/tests/test_no_stale_change_record_paths.py",
     "this guard spells out the patterns it hunts"),
]

# Code that builds the records path itself. Only the helper (and the cases below) may.
SELF_DERIVED = re.compile(
    r"""["']attribution["']\s*/\s*["']change-records["']"""
    r"""|\$\{?\w+\}?/attribution/change-records"""
)
DERIVE_ALLOWLIST = [
    ("claude-code-shared/scripts/shared/change_records_path.py",
     "the helper itself: the one place that derives the path"),
    ("claude-code-shared/scripts/learning/log-learning.py",
     "LOG_LEARNING_DOTFILES_DIR names a different repo root, so the helper cannot apply"),
    ("claude-code-shared/scripts/attribution/commit-change-records.sh",
     "CC_CHANGE_RECORDS_REPO names a different repo root, so the helper cannot apply"),
]


def _tracked(root):
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.split("\0") if p and p.startswith(SCOPE)]


def _allowed(rel, allowlist):
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p, _ in allowlist)


def _lines(root, rel):
    try:
        return (Path(root) / rel).read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []  # binary, deleted, or a symlink to nowhere


def scan(root, allowlist=None):
    """Return 'file:line: text' for each stale reference outside the allowlist."""
    allowlist = ALLOWLIST if allowlist is None else allowlist
    hits = []
    for rel in _tracked(root):
        if _allowed(rel, allowlist):
            continue
        for n, line in enumerate(_lines(root, rel), 1):
            if STALE.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def scan_self_derived(root, allowlist=None):
    """Return 'file:line' for scripts that build the change-records path without the helper."""
    allowlist = DERIVE_ALLOWLIST if allowlist is None else allowlist
    hits = []
    for rel in _tracked(root):
        if not rel.startswith("claude-code-shared/scripts/") or not rel.endswith((".py", ".sh")):
            continue
        if "/tests/" in rel or _allowed(rel, allowlist):
            continue
        for n, line in enumerate(_lines(root, rel), 1):
            if SELF_DERIVED.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def _repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


BANK_ALIAS = [r"^\s*alias\s+bank=", r"^\s*(?:function\s+)?bank\s*\(\)"]


def _has_bank_alias(text):
    return any(re.search(p, text, re.M) for p in BANK_ALIAS)


# ---- the real tree ----

def test_real_tree_has_no_stale_paths():
    hits = scan(DOTFILES)
    assert not hits, "stale change-record paths:\n" + "\n".join(hits)


def test_umbrella_seed_has_no_seed_bank_briefs():
    seed = DOTFILES / "docs/seeds/20261103-0105-self-improvement-loop-umbrella.json"
    for n, line in enumerate(seed.read_text().splitlines(), 1):
        assert "seed-bank/briefs" not in line, f"{seed.name}:{n}"


def test_no_script_derives_records_path_itself():
    hits = scan_self_derived(DOTFILES)
    assert not hits, "use scripts/shared/change_records_path.py instead:\n" + "\n".join(hits)


def test_zshrc_defines_no_bank_alias():
    assert not _has_bank_alias((DOTFILES / ".zshrc").read_text())


def test_real_allowlist_entries_point_at_something():
    # a typo'd path would silently allow nothing; require each to match a tracked file
    tracked = subprocess.run(["git", "-C", str(DOTFILES), "ls-files"], capture_output=True, text=True,
                             check=True).stdout.split("\n")
    for p, why in ALLOWLIST + DERIVE_ALLOWLIST:
        assert why
        assert any(t == p or (p.endswith("/") and t.startswith(p)) for t in tracked), f"dead allowlist entry {p}"


# ---- scratch repos ----

SCOPED_LOCATIONS = [
    "claude-code-shared/scripts/x.sh",
    ".github/workflows/ci.yml",
    ".githooks/pre-push",
    ".zshrc",
    "docs/notes.md",
    "attribution/other.md",
]


@pytest.mark.parametrize("rel", SCOPED_LOCATIONS)
def test_scratch_stale_ref_in_each_scanned_location_names_file_and_line(tmp_path, rel):
    repo = _repo(tmp_path, {rel: "ok\nrun scripts/bank/bank.sh now\n"})
    hits = scan(repo)
    assert hits and hits[0].startswith(f"{rel}:2:")


@pytest.mark.parametrize("text", [
    "seed-bank/changes/x.json", "see scripts/bank/", "run bank revert", "bash bank.sh",
    "BANK_REPO=1", "BANK_CHANGES_DIR=1", "BANK_BASE_BRANCH=main",
])
def test_scratch_each_pattern_is_caught(tmp_path, text):
    assert scan(_repo(tmp_path, {"docs/a.md": f"x\n{text}\n"}))


@pytest.mark.parametrize("text", ["test-bank.sh", "the bank alias", "CHANGE_REVERT_REPO", "scripts/bank-x"])
def test_scratch_unrelated_text_is_not_flagged(tmp_path, text):
    assert scan(_repo(tmp_path, {"docs/a.md": text + "\n"})) == []


def test_scratch_out_of_scope_files_ignored(tmp_path):
    assert scan(_repo(tmp_path, {"other/a.md": "seed-bank/changes\n"})) == []


@pytest.mark.parametrize("entry", ALLOWLIST, ids=[p for p, _ in ALLOWLIST])
def test_each_allowlist_entry_is_exercised(tmp_path, entry):
    path, _ = entry
    rel = path + "probe.json" if path.endswith("/") else path
    repo = _repo(tmp_path, {rel: "seed-bank/changes/x.json\n"})
    assert scan(repo) == []
    without = [e for e in ALLOWLIST if e != entry]
    hits = scan(repo, without)
    assert hits and hits[0].startswith(f"{rel}:1:")


def test_scratch_self_derived_path_is_caught(tmp_path):
    repo = _repo(tmp_path, {
        "claude-code-shared/scripts/a/new.py": 'p = root / "attribution" / "change-records"\n',
        "claude-code-shared/scripts/a/new.sh": 'D="$REPO/attribution/change-records"\n',
        "claude-code-shared/scripts/a/tests/t.py": 'p = root / "attribution" / "change-records"\n',
    })
    hits = scan_self_derived(repo)
    assert [h.split(":")[0] for h in hits] == ["claude-code-shared/scripts/a/new.py",
                                               "claude-code-shared/scripts/a/new.sh"]


@pytest.mark.parametrize("entry", DERIVE_ALLOWLIST, ids=[p for p, _ in DERIVE_ALLOWLIST])
def test_each_derive_allowlist_entry_is_exercised(tmp_path, entry):
    path, _ = entry
    repo = _repo(tmp_path, {path: 'p = root / "attribution" / "change-records"\n'})
    assert scan_self_derived(repo) == []
    assert scan_self_derived(repo, [e for e in DERIVE_ALLOWLIST if e != entry])


@pytest.mark.parametrize("line", ["alias bank='x'", "bank() { :; }", "function bank() { :; }"])
def test_bank_alias_detector_catches_definitions(line):
    assert _has_bank_alias("export A=1\n" + line + "\n")


def test_bank_alias_detector_ignores_other_aliases():
    assert not _has_bank_alias("alias gs='git status'\n# alias bank was removed\n")
