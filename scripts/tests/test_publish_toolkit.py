import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "publish" / "publish-toolkit.py"
spec = importlib.util.spec_from_file_location("publish_toolkit", SCRIPT)
pt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt)

MANIFEST = {
    "include": ["README.md", "skills/"],
    "exclude": ["skills/private/", "*.pyc"],
    "banned": ["acme[ -]?corp"],
    "allow": [],
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "src"
    files = {
        "README.md": "Toolkit readme\n",
        "skills/tdd/SKILL.md": "Red, green, refactor.\n",
        "skills/private/SKILL.md": "Acme Corp only.\n",
        "skills/tdd/cache.pyc": "x",
        "learnings/log.jsonl": "{}\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root / "publish.json").write_text(json.dumps(MANIFEST))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_selection_is_default_deny(repo):
    files = pt.select(pt.candidate_files(repo), MANIFEST)
    assert files == ["README.md", "skills/tdd/SKILL.md"]


def test_folder_patterns_need_the_trailing_slash():
    assert pt.matches("skills/a/b.md", "skills/")
    assert not pt.matches("skillset/a.md", "skills/")
    assert pt.matches("evals/x/cases.jsonl", "evals/*/cases*")


def test_folder_patterns_expand_globs():
    assert pt.matches("skills/bench/runs/a/scores.json", "skills/*/runs/")
    assert pt.matches("scripts/tests/fixtures/build-standup-data/x.json", "scripts/tests/fixtures/*standup*/")
    assert not pt.matches("skills/bench/SKILL.md", "skills/*/runs/")


@pytest.mark.parametrize(
    "line,name",
    [
        ("Works at ACME Corp", "banned: acme[ -]?corp"),
        ("see /Users/someone/notes", "home path"),
        ("mail me@example.com", "email"),
        ("token ghp_" + "a" * 36, "github token"),
        ("key sk-ant-" + "b" * 24, "anthropic key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
    ],
)
def test_scan_flags_each_leak_kind(line, name):
    hits = pt.scan_text("f.md", line, MANIFEST)
    assert [h[2] for h in hits] == [name]


def test_allow_entry_silences_one_known_false_positive():
    manifest = {**MANIFEST, "allow": [{"file": "docs/*", "pattern": r"^noreply@example\.com$"}]}
    assert pt.scan_text("docs/a.md", "noreply@example.com", manifest) == []
    assert pt.scan_text("other.md", "noreply@example.com", manifest) != []


def test_dry_run_copies_clean_files_and_exits_zero(repo, tmp_path):
    out = tmp_path / "out"
    assert pt.main(["--root", str(repo), "--out", str(out)]) == 0
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == ["README.md", "skills/tdd/SKILL.md"]


def test_leak_blocks_with_exit_two(repo, tmp_path):
    (repo / "skills/tdd/SKILL.md").write_text("Built for Acme Corp.\n")
    assert pt.main(["--root", str(repo), "--out", str(tmp_path / "out")]) == 2


def test_commit_lands_in_target_clone_only_when_clean(repo, tmp_path):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    target = tmp_path / "public"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=target, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=target, check=True)
    assert pt.main(["--root", str(repo), "--target", str(target), "--commit"]) == 0
    log = subprocess.run(["git", "log", "--format=%s"], cwd=target, capture_output=True, text=True).stdout
    assert log.startswith("Sync from dotfiles @ ")


def test_output_inside_source_is_refused(repo):
    with pytest.raises(SystemExit):
        pt.main(["--root", str(repo), "--out", str(repo / "out")])
