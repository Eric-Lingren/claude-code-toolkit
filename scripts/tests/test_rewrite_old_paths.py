import json
import subprocess
import sys
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent.parent.parent
REPO = TOOLKIT.parent
SCRIPT = TOOLKIT / "scripts" / "attribution" / "rewrite-old-paths.py"
VALIDATE = TOOLKIT / "scripts" / "shared" / "validate-schema.sh"
CONTRACTS = TOOLKIT / "contracts"

OLD = (
    "a claude-code-shared/seed-bank/changes/x.json\n"
    "b seed-bank/changes/y.json\n"
    "c claude-code-shared/scripts/bank/bank.sh\n"
    "d run bank revert now\n"
)
NEW = (
    "a attribution/change-records/x.json\n"
    "b attribution/change-records/y.json\n"
    "c claude-code-shared/scripts/attribution/change-revert.sh\n"
    "d run change-revert now\n"
)
BARE = "the seed-bank at repo root holds seeds; to-seed targets seed-bank/\nbank of fog\n"
EXEMPT_SEED = "docs/seeds/20261103-2223-change-records-apart-from-seed-bank.json"
EXEMPT_TASK = "docs/tasks/20261103-2230-change-records-apart-from-seed-bank.json"


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def make_tree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    tracked = {
        "docs/seeds/s.json": OLD + BARE,
        "docs/tasks/t.json": OLD,
        "docs/tasks/.logs/t/T-1.md": OLD,
        "docs/handoffs/phase-1-h.md": OLD + BARE,
        "docs/seeds/bare.json": BARE,
        EXEMPT_SEED: OLD,
        EXEMPT_TASK: OLD,
        "docs/tasks/.logs/20261103-2230-change-records-apart-from-seed-bank/T-9.md": OLD,
        "claude-code-shared/learnings/unified-learnings.jsonl": OLD,
        "claude-code-shared/learnings/audit/hook-audit.jsonl": OLD,
        "attribution/change-records/rec.json": OLD,
    }
    for rel, text in tracked.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    write(root, "docs/tasks/untracked.json", OLD)
    write(root, "docs/tasks/.sprout/state.json", OLD)
    return root, tracked


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}


def test_four_patterns_rewritten_bare_untouched(tmp_path):
    root, _ = make_tree(tmp_path)
    run(root, "--apply")
    assert (root / "docs/seeds/s.json").read_text() == NEW + BARE
    assert (root / "docs/tasks/t.json").read_text() == NEW
    assert (root / "docs/tasks/.logs/t/T-1.md").read_text() == NEW
    assert (root / "docs/handoffs/phase-1-h.md").read_text() == NEW + BARE
    assert (root / "docs/seeds/bare.json").read_text() == BARE


def test_exempt_and_untracked_never_modified(tmp_path):
    root, tracked = make_tree(tmp_path)
    before = snapshot(root)
    out = run(root, "--apply")
    after = snapshot(root)
    untouched = ["docs/tasks/untracked.json", "docs/tasks/.sprout/state.json", EXEMPT_SEED, EXEMPT_TASK,
                 "docs/tasks/.logs/20261103-2230-change-records-apart-from-seed-bank/T-9.md",
                 "claude-code-shared/learnings/unified-learnings.jsonl",
                 "claude-code-shared/learnings/audit/hook-audit.jsonl",
                 "attribution/change-records/rec.json"]
    for rel in untouched:
        assert after[rel] == before[rel], rel
    assert "docs/tasks/untracked.json" in out
    assert "docs/tasks/.sprout/state.json" in out


def test_dry_run_changes_nothing_and_apply_idempotent(tmp_path):
    root, _ = make_tree(tmp_path)
    before = snapshot(root)
    out = run(root)
    assert snapshot(root) == before
    assert "dry-run" in out
    run(root, "--apply")
    once = snapshot(root)
    assert once != before
    out2 = run(root, "--apply")
    assert snapshot(root) == once
    assert "files changed: 0" in out2


def test_report_lists_manual_review_bare_mentions(tmp_path):
    root, _ = make_tree(tmp_path)
    out = run(root, "--apply")
    assert "manual review" in out
    assert "docs/handoffs/phase-1-h.md:5" in out or "docs/handoffs/phase-1-h.md" in out
    assert "bank of fog" in out
    assert "docs/seeds/bare.json" not in out.split("manual review")[1]


def test_file_flag_rewrites_one_file(tmp_path):
    root, _ = make_tree(tmp_path)
    f = write(root, "pr-body.md", OLD)
    other = (root / "docs/tasks/t.json").read_bytes()
    out = run(root, "--apply", "--file", str(f))
    assert f.read_text() == NEW
    assert (root / "docs/tasks/t.json").read_bytes() == other
    assert "pr-body.md" in out


def _validate(schema, data):
    r = subprocess.run(["bash", str(VALIDATE), "--instance", str(CONTRACTS / schema), str(data)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _inject(src: Path, dst: Path):
    text = src.read_text()
    text = text.replace('"title": "', '"title": "seed-bank/changes bank.sh ', 1)
    assert text != src.read_text()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)


def test_rewritten_seed_and_task_validate(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    seed = root / "docs/seeds/s.json"
    task = root / "docs/tasks/t.json"
    _inject(REPO / "docs/seeds/20261103-0219-phase-1-attribution-spine.json", seed)
    _inject(REPO / "docs/tasks/20261103-1510-phase-1-attribution-spine.json", task)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    run(root, "--apply")
    assert "attribution/change-records change-revert.sh" in seed.read_text()
    assert "attribution/change-records change-revert.sh" in task.read_text()
    json.loads(seed.read_text())
    _validate("seed-schema.json", seed)
    _validate("task-schema.json", task)
