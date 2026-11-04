"""Tests for scripts/attribution/write-change-records.py against a temp git repo with fixture merges."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

SHARED = Path(__file__).resolve().parents[2]
SCRIPT = SHARED / "scripts" / "attribution" / "write-change-records.py"
SCHEMA = json.loads((SHARED / "contracts" / "change-record-schema.json").read_text())

spec = importlib.util.spec_from_file_location("write_change_records", SCRIPT)
wcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wcr)

SKILL_MD = """---
name: debug
---

Intro text.

## Reproduce

Step one.

```
# not a heading
```

## Instrument

Add logging.

## Fix

Apply the fix.
"""


def git(repo, *args, date=None):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
    )
    if date:
        env["GIT_COMMITTER_DATE"] = date
        env["GIT_AUTHOR_DATE"] = date
    out = subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def merge_branch(repo, name, pr, files, date, branch_msg=None, merge_body=None):
    """Create branch `name` off main with `files` ({rel: text}), merge --no-ff into main.

    branch_msg replaces the branch commit message; merge_body is appended to the merge message.
    """
    git(repo, "checkout", "-q", "-b", name, "main")
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", branch_msg or f"work on {name}", date=date)
    git(repo, "checkout", "-q", "main")
    merge_msg = f"Merge pull request #{pr} from someone/{name}"
    if merge_body:
        merge_msg += f"\n\n{merge_body}"
    git(repo, "merge", "--no-ff", "-q", name, "-m", merge_msg, date=date)
    return git(repo, "rev-parse", "HEAD")


def squash_commit(repo, files, message, date):
    """A squash-merged change: one ordinary commit directly on main."""
    git(repo, "checkout", "-q", "main")
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, date=date)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "claude-code-shared/skills/debug/SKILL.md", SKILL_MD)
    write(r, "claude-code-shared/learnings/unified-learnings.jsonl", '{"id": "L-1"}\n')
    write(r, "claude-code-shared/scripts/learning/log.py", "print('v1')\n")
    write(r, "README.md", "readme\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "initial", date="2026-10-01T00:00:00+00:00")
    return r


def run_writer(repo, out_dir, **kw):
    return wcr.write_change_records(
        repo, out_dir, since="2000-01-01", ref="main", **kw
    )


def records(out_dir):
    return {p.name: json.loads(p.read_text()) for p in sorted(Path(out_dir).glob("*.json"))}


def test_skill_merge_produces_correct_record(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    edited = SKILL_MD.replace("Add logging.", "Add logging and tracing.")
    merged = merge_branch(
        repo, "feat/debug-tweak", 7,
        {"claude-code-shared/skills/debug/SKILL.md": edited},
        "2026-10-05T12:00:00+00:00",
    )
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert len(result["written"]) == 1
    recs = records(out)
    assert list(recs) == [f"chg-{merged[:12]}.json"]
    rec = recs[f"chg-{merged[:12]}.json"]
    assert rec["change_id"] == f"chg-{merged[:12]}"
    assert rec["kind"] == "skill"
    assert rec["touched_units"] == ["skill:debug"]
    assert rec["touched_paths"] == ["claude-code-shared/skills/debug/SKILL.md"]
    assert rec["sections"] == [
        {"path": "claude-code-shared/skills/debug/SKILL.md", "section": "## Instrument"}
    ]
    assert rec["merged_sha"] == merged
    assert rec["base_sha"] == base
    assert rec["merged_at"].startswith("2026-10-05T12:00:00")
    assert rec["lane"] == "manual"
    assert "pr:7" in rec["source_refs"]
    assert "branch:feat/debug-tweak" in rec["source_refs"]
    assert rec["target_repo"]


def test_hunk_in_preamble_and_multiple_sections(repo, tmp_path):
    edited = SKILL_MD.replace("Intro text.", "Intro text changed.").replace(
        "Apply the fix.", "Apply the fix carefully."
    )
    merge_branch(repo, "feat/multi", 8,
                 {"claude-code-shared/skills/debug/SKILL.md": edited},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert [s["section"] for s in rec["sections"]] == ["## Fix", "(preamble)"]


def test_pure_deletion_of_a_step_line_is_attributed_to_its_section(repo, tmp_path):
    # A pure deletion is a "+N,0" hunk: N is the line kept just before the gap, so the section
    # that line sits in is the one recorded (the count == 0 branch of derive_sections).
    edited = SKILL_MD.replace("Apply the fix.\n", "")
    merge_branch(repo, "feat/del-step", 20,
                 {"claude-code-shared/skills/debug/SKILL.md": edited},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert [s["section"] for s in rec["sections"]] == ["## Fix"]


def test_pure_deletion_of_a_whole_heading_is_attributed_to_the_section_above(repo, tmp_path):
    # Heading and body removed: the gap follows the Reproduce fence, so Reproduce is recorded.
    edited = SKILL_MD.replace("## Instrument\n\nAdd logging.\n\n", "")
    merge_branch(repo, "feat/del-heading", 21,
                 {"claude-code-shared/skills/debug/SKILL.md": edited},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert [s["section"] for s in rec["sections"]] == ["## Reproduce"]


def test_code_fence_comment_is_not_a_heading(repo, tmp_path):
    edited = SKILL_MD.replace("Step one.", "Step one revised.").replace(
        "# not a heading", "# still not a heading"
    )
    merge_branch(repo, "feat/fence", 9,
                 {"claude-code-shared/skills/debug/SKILL.md": edited},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert [s["section"] for s in rec["sections"]] == ["## Reproduce"]


def test_indented_and_closed_headings_match_what_the_validator_accepts(repo, tmp_path):
    vspec = importlib.util.spec_from_file_location(
        "validate_escape_point", SHARED / "scripts" / "learning" / "validate-escape-point.py"
    )
    validator = importlib.util.module_from_spec(vspec)
    vspec.loader.exec_module(validator)

    text = "intro\n\n  ## Indented\n\nbody\n\n## Closed ##\n\nmore\n\n~~~\n# in tilde fence\n~~~\n"
    # Every heading the validator accepts is a recorded section, and nothing else is.
    recorded = {h for h in wcr.heading_map(text) if h}
    assert {h.lstrip("#").strip() for h in recorded} == set(validator._headings(text))
    assert recorded == {"## Indented", "## Closed"}

    base = SKILL_MD + "\n  ## Indented\n\nbody\n\n## Closed ##\n\nmore\n"
    write(repo, "claude-code-shared/skills/debug/SKILL.md", base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add odd headings", date="2026-10-02T00:00:00+00:00")
    merge_branch(repo, "feat/odd", 11,
                 {"claude-code-shared/skills/debug/SKILL.md":
                  base.replace("body", "body edited").replace("more", "more edited")},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    recs = [r for r in records(out).values() if r["merged_at"].startswith("2026-10-05")]
    assert [s["section"] for s in recs[0]["sections"]] == ["## Closed", "## Indented"]


def test_learnings_only_merge_produces_no_record(repo, tmp_path):
    merge_branch(
        repo, "learnings/batch", 10,
        {"claude-code-shared/learnings/unified-learnings.jsonl":
         '{"id": "L-1"}\n{"id": "L-2"}\n'},
        "2026-10-06T12:00:00+00:00",
    )
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert result["written"] == []
    assert not out.exists() or list(out.glob("*.json")) == []


def test_non_qualifying_merges_produce_no_record(repo, tmp_path):
    merge_branch(repo, "docs/readme", 11, {"README.md": "changed\n"},
                 "2026-10-06T12:00:00+00:00")
    merge_branch(repo, "chore/config", 12,
                 {"claude-code-shared/publish.json": "{}\n",
                  "claude-code-shared/resources/x.json": "{}\n"},
                 "2026-10-07T12:00:00+00:00")
    merge_branch(repo, "test/only", 13,
                 {"claude-code-shared/scripts/tests/test_x.py": "pass\n"},
                 "2026-10-08T12:00:00+00:00")
    out = tmp_path / "changes"
    assert run_writer(repo, out)["written"] == []


def test_mixed_kinds_priority_and_units(repo, tmp_path):
    merge_branch(
        repo, "feat/mixed", 14,
        {
            "claude-code-shared/agents/build/build-runner.md": "# Runner\n\nbody\n",
            "claude-code-shared/scripts/learning/log.py": "print('v2')\n",
            "claude-code-shared/contracts/foo-contract.md": "# Foo\n",
            "claude-code-shared/contracts/foo-schema.json": "{}\n",
            "claude-code-shared/hooks/guard.sh": "#!/bin/sh\n",
            "claude-code-shared/skills/debug/scripts/helper.sh": "#!/bin/sh\n",
            "claude-code-shared/learnings/unified-learnings.jsonl": '{"id": "L-9"}\n',
        },
        "2026-10-09T12:00:00+00:00",
    )
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert rec["kind"] == "skill"
    assert rec["touched_units"] == [
        "agent:build-runner",
        "contract:foo-contract",
        "contract:foo-schema",
        "hook:guard.sh",
        "script:learning/log.py",
        "skill:debug",
    ]
    assert "claude-code-shared/learnings/unified-learnings.jsonl" in rec["touched_paths"]
    # agent markdown (added file) is covered by sections
    assert {"path": "claude-code-shared/agents/build/build-runner.md",
            "section": "# Runner"} in rec["sections"]


def test_script_only_merge_kind_script(repo, tmp_path):
    merge_branch(repo, "fix/script", 15,
                 {"claude-code-shared/scripts/learning/log.py": "print('v3')\n"},
                 "2026-10-09T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    assert rec["kind"] == "script"
    assert rec["sections"] == []


def test_rerun_is_idempotent_and_byte_identical(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/skills/debug/SKILL.md": SKILL_MD + "\nmore\n"},
                 "2026-10-05T12:00:00+00:00")
    merge_branch(repo, "feat/b", 21,
                 {"claude-code-shared/scripts/learning/log.py": "print('b')\n"},
                 "2026-10-06T12:00:00+00:00")
    out = tmp_path / "changes"
    first = run_writer(repo, out)
    assert len(first["written"]) == 2
    before = {p.name: p.read_bytes() for p in out.glob("*")}
    mtimes = {p.name: p.stat().st_mtime_ns for p in out.glob("*")}
    second = run_writer(repo, out)
    assert second["written"] == []
    assert len(second["skipped"]) == 2
    after = {p.name: p.read_bytes() for p in out.glob("*")}
    assert before == after
    assert mtimes == {p.name: p.stat().st_mtime_ns for p in out.glob("*")}


def test_rerun_after_new_merge_only_adds_new_record(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    before = {p.name: p.read_bytes() for p in out.glob("*")}
    merge_branch(repo, "feat/b", 21,
                 {"claude-code-shared/scripts/learning/log.py": "print('b')\n"},
                 "2026-10-06T12:00:00+00:00")
    result = run_writer(repo, out)
    assert len(result["written"]) == 1
    after = {p.name: p.read_bytes() for p in out.glob("*")}
    assert len(after) == 2
    for name, data in before.items():
        assert after[name] == data


def test_every_record_validates_against_schema(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/skills/debug/SKILL.md": SKILL_MD.replace("Fix", "Repair")},
                 "2026-10-05T12:00:00+00:00")
    merge_branch(repo, "feat/b", 21,
                 {"claude-code-shared/scripts/learning/log.py": "print('b')\n",
                  "claude-code-shared/hooks/h.sh": "x\n"},
                 "2026-10-06T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    recs = records(out)
    assert len(recs) == 2
    for rec in recs.values():
        jsonschema.validate(rec, SCHEMA)


def test_schema_rejects_malformed_record(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    for mutate in (
        lambda r: r.update(lane="auto"),
        lambda r: r.update(kind="docs"),
        lambda r: r.pop("merged_sha"),
        lambda r: r.update(extra=1),
    ):
        bad = json.loads(json.dumps(rec))
        mutate(bad)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, SCHEMA)


def test_git_helper_default_has_no_timeout(repo, monkeypatch):
    """The shared git() helper stays untimed by default so the weekly writer is unchanged."""
    seen = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(wcr.subprocess, "run", spy)
    assert wcr.git(repo, "rev-parse", "HEAD").returncode == 0
    assert seen == [None]
    wcr.git(repo, "rev-parse", "HEAD", timeout=5)
    assert seen == [None, 5]


def test_git_helper_timeout_raises_when_exceeded(repo, tmp_path, monkeypatch):
    """An opt-in timeout surfaces as subprocess.TimeoutExpired."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "git"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(subprocess.TimeoutExpired):
        wcr.git(repo, "status", timeout=0.5)


def test_since_filters_old_merges(repo, tmp_path):
    merge_branch(repo, "feat/old", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-01-05T12:00:00+00:00")
    merge_branch(repo, "feat/new", 21,
                 {"claude-code-shared/scripts/learning/log.py": "print('b')\n"},
                 "2026-10-06T12:00:00+00:00")
    out = tmp_path / "changes"
    result = wcr.write_change_records(repo, out, since="2026-06-01", ref="main")
    assert len(result["written"]) == 1


def test_cli_writes_records_and_reports(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--repo", str(repo), "--since", "2000-01-01",
         "--ref", "main", "--out-dir", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert len(list(out.glob("chg-*.json"))) == 1
    assert "1 written" in proc.stdout


def test_cli_dry_run_writes_nothing(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-10-05T12:00:00+00:00")
    out = tmp_path / "changes"
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--repo", str(repo), "--since", "2000-01-01",
         "--ref", "main", "--out-dir", str(out), "--dry-run"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not out.exists()


def _corrupt_first_build(monkeypatch):
    """Make build_record return a schema-invalid record for the first merge only."""
    real = wcr.build_record
    seen = []

    def build(repo, merge, *a, **kw):
        rec = real(repo, merge, *a, **kw)
        if not seen:
            seen.append(merge["sha"])
            rec["lane"] = "auto"  # not in the schema's lane enum
        return rec

    monkeypatch.setattr(wcr, "build_record", build)
    return seen


def test_invalid_record_lands_in_errors_and_is_not_written(repo, tmp_path, monkeypatch):
    merge_branch(repo, "feat/a", 20, {SCRIPT_FILE: "print('a')\n"}, "2026-10-05T12:00:00+00:00")
    seen = _corrupt_first_build(monkeypatch)
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert result["written"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith(f"{seen[0]}:")
    assert "auto" in result["errors"][0]
    assert not out.exists() or list(out.iterdir()) == []


def test_invalid_record_does_not_block_later_valid_records(repo, tmp_path, monkeypatch):
    merge_branch(repo, "feat/a", 20, {SCRIPT_FILE: "print('a')\n"}, "2026-10-05T12:00:00+00:00")
    merge_branch(repo, "feat/b", 21, {SCRIPT_FILE: "print('b')\n"}, "2026-10-06T12:00:00+00:00")
    _corrupt_first_build(monkeypatch)
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert len(result["errors"]) == 1
    assert len(result["written"]) == 1
    assert [p.name for p in out.iterdir()] == [f"{result['written'][0]}.json"]


def test_cli_exits_1_and_reports_error_when_record_invalid(repo, tmp_path, monkeypatch, capsys):
    merge_branch(repo, "feat/a", 20, {SCRIPT_FILE: "print('a')\n"}, "2026-10-05T12:00:00+00:00")
    seen = _corrupt_first_build(monkeypatch)
    out = tmp_path / "changes"
    rc = wcr.main(["--repo", str(repo), "--since", "2000-01-01", "--ref", "main",
                   "--out-dir", str(out)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "0 written" in captured.out and "1 errors" in captured.out
    assert f"error: {seen[0]}:" in captured.err
    assert not out.exists() or list(out.iterdir()) == []


def test_does_not_modify_the_repo(repo, tmp_path):
    merge_branch(repo, "feat/a", 20,
                 {"claude-code-shared/scripts/learning/log.py": "print('a')\n"},
                 "2026-10-05T12:00:00+00:00")
    head = git(repo, "rev-parse", "HEAD")
    run_writer(repo, tmp_path / "changes")
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""


SCRIPT_FILE = "claude-code-shared/scripts/learning/log.py"
TRAILERS_DOC = SHARED / "resources" / "change-record-trailers.md"


def only_record(repo, tmp_path):
    out = tmp_path / "changes"
    run_writer(repo, out)
    (rec,) = records(out).values()
    jsonschema.validate(rec, SCHEMA)
    return rec


def test_seed_id_trailer_on_branch_commit_yields_seed_lane(repo, tmp_path):
    merge_branch(repo, "seed/s1", 30, {SCRIPT_FILE: "print('s')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Apply seed\n\nSeed-Id: 20261103-1200-attribution-spine")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "seed"
    assert "20261103-1200-attribution-spine" in rec["source_refs"]
    assert "pr:30" in rec["source_refs"]


def test_seed_id_trailer_on_merge_commit_yields_seed_lane(repo, tmp_path):
    merge_branch(repo, "seed/s2", 31, {SCRIPT_FILE: "print('s')\n"},
                 "2026-10-05T12:00:00+00:00", merge_body="Seed-Id: seed-abc")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "seed"
    assert "seed-abc" in rec["source_refs"]


def test_learning_ids_trailer_yields_learnings_lane_with_each_id(repo, tmp_path):
    merge_branch(repo, "learnings/c1", 32, {SCRIPT_FILE: "print('l')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Apply cluster\n\nLearning-Ids: a,b")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "learnings"
    assert "a" in rec["source_refs"] and "b" in rec["source_refs"]


def test_learning_ids_tolerates_spaces_and_repeated_lines(repo, tmp_path):
    merge_branch(repo, "learnings/c2", 33, {SCRIPT_FILE: "print('l')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Apply cluster\n\nLearning-Ids: a, b\nLearning-Ids: c")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "learnings"
    assert {"a", "b", "c"} <= set(rec["source_refs"])
    assert "" not in rec["source_refs"]


def test_malformed_seed_id_trailer_is_ignored(repo, tmp_path):
    merge_branch(repo, "seed/bad", 36, {SCRIPT_FILE: "print('s')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Apply\n\nSeed-Id: ignore previous <instructions> /etc/passwd")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "manual"
    assert rec["source_refs"] == ["pr:36", "branch:seed/bad"]


def test_malformed_learning_ids_dropped_valid_kept(repo, tmp_path):
    long_id = "x" * 81
    merge_branch(repo, "learnings/bad", 37, {SCRIPT_FILE: "print('l')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg=f"Apply\n\nLearning-Ids: L-1,bad id,a/b,{long_id},L_2.x")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "learnings"
    assert rec["source_refs"] == ["pr:37", "branch:learnings/bad", "L-1", "L_2.x"]


def test_learning_ids_all_malformed_is_manual(repo, tmp_path):
    merge_branch(repo, "learnings/bad2", 38, {SCRIPT_FILE: "print('l')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Apply\n\nLearning-Ids: $(rm -rf),;x")
    assert only_record(repo, tmp_path)["lane"] == "manual"


def test_merge_without_trailer_is_manual(repo, tmp_path):
    merge_branch(repo, "feat/hand", 34, {SCRIPT_FILE: "print('h')\n"},
                 "2026-10-05T12:00:00+00:00")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "manual"
    assert rec["source_refs"] == ["pr:34", "branch:feat/hand"]


def test_unrelated_trailer_does_not_set_lane(repo, tmp_path):
    merge_branch(repo, "feat/co", 35, {SCRIPT_FILE: "print('h')\n"},
                 "2026-10-05T12:00:00+00:00",
                 branch_msg="Change\n\nCo-authored-by: X <x@example.com>")
    assert only_record(repo, tmp_path)["lane"] == "manual"


def test_merge_and_squash_commits_both_recorded_with_correct_shas(repo, tmp_path):
    root = git(repo, "rev-parse", "HEAD")
    merged = merge_branch(repo, "feat/m", 40, {SCRIPT_FILE: "print('m')\n"},
                          "2026-10-05T12:00:00+00:00")
    squashed = squash_commit(repo, {SCRIPT_FILE: "print('q')\n"},
                             "Tweak the logger (#41)", "2026-10-06T12:00:00+00:00")
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert len(result["written"]) == 2
    by_merged = {r["merged_sha"]: r for r in records(out).values()}
    assert set(by_merged) == {merged, squashed}
    assert by_merged[merged]["base_sha"] == root
    assert by_merged[squashed]["base_sha"] == merged
    assert by_merged[squashed]["lane"] == "manual"
    assert "pr:41" in by_merged[squashed]["source_refs"]
    for rec in by_merged.values():
        jsonschema.validate(rec, SCHEMA)


def test_squash_commit_with_seed_trailer(repo, tmp_path):
    squash_commit(repo, {SCRIPT_FILE: "print('q')\n"},
                  "Apply seed (#42)\n\nSeed-Id: seed-xyz", "2026-10-06T12:00:00+00:00")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "seed"
    assert "seed-xyz" in rec["source_refs"]


def test_squash_commit_with_learning_ids_trailer(repo, tmp_path):
    squash_commit(repo, {SCRIPT_FILE: "print('q')\n"},
                  "Apply cluster\n\nLearning-Ids: x,y", "2026-10-06T12:00:00+00:00")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "learnings"
    assert {"x", "y"} <= set(rec["source_refs"])


def test_direct_commit_on_main_gets_no_record(repo, tmp_path):
    direct = squash_commit(repo, {SCRIPT_FILE: "print('q')\n"},
                           "Tweak the logger", "2026-10-06T12:00:00+00:00")
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert result["written"] == []
    assert direct in result["ignored"]


def test_non_qualifying_squash_commit_gets_no_record(repo, tmp_path):
    squash_commit(repo, {"README.md": "x\n"}, "docs only", "2026-10-07T12:00:00+00:00")
    out = tmp_path / "changes"
    assert run_writer(repo, out)["written"] == []


def test_trailer_doc_names_both_trailers_exactly():
    text = TRAILERS_DOC.read_text()
    assert "Seed-Id:" in text
    assert "Learning-Ids:" in text


# --- ci_snapshot (CI base-vs-head test snapshot on manual-lane records) -------------------------

BASE_RESULTS = {"results": {"a.py": "pass", "b.sh": "fail"}, "totals": {"passed": 1, "failed": 1, "total": 2}}
HEAD_RESULTS = {"results": {"a.py": "pass", "b.sh": "pass", "c.sh": "pass"},
                "totals": {"passed": 3, "failed": 0, "total": 3}}
DIFF_RESULTS = {"newly_failing": [], "newly_passing": ["b.sh"], "unchanged": ["a.py"],
                "added": [{"file": "c.sh", "status": "pass"}], "removed": [], "changed": True}

STUB_GH = """#!/usr/bin/env bash
# Fake gh. STUB_GH_MODE: ok (default) | fail | norun. Logs argv to $STUB_GH_LOG.
printf '%s\\n' "$*" >> "${STUB_GH_LOG:-/dev/null}"
case "${STUB_GH_MODE:-ok}" in
  fail) echo "gh: network down" >&2; exit 1 ;;
esac
if [[ "$1 $2" == "run list" ]]; then
  if [[ "${STUB_GH_MODE:-ok}" == "norun" ]]; then echo "[]"; else echo '[{"databaseId": 555}]'; fi
  exit 0
fi
if [[ "$1 $2" == "run download" ]]; then
  dir=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-D" || "$1" == "--dir" ]]; then dir="$2"; fi
    shift
  done
  [[ -n "$dir" ]] || exit 2
  mkdir -p "$dir"
  cp "$STUB_GH_ARTIFACT"/*.json "$dir"/
  exit 0
fi
if [[ "$1 $2" == "pr view" ]]; then echo '{"headRefOid": "'"$STUB_GH_PR_HEAD"'"}'; exit 0; fi
exit 1
"""


@pytest.fixture(autouse=True)
def stub_gh(tmp_path, monkeypatch):
    """Every test runs against a fake gh on PATH (failing by default). Never the real gh."""
    bindir = tmp_path / "ghbin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(STUB_GH)
    gh.chmod(0o755)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "base.json").write_text(json.dumps(BASE_RESULTS))
    (artifact / "head.json").write_text(json.dumps(HEAD_RESULTS))
    (artifact / "diff.json").write_text(json.dumps(DIFF_RESULTS))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GH_MODE", "fail")
    monkeypatch.setenv("STUB_GH_ARTIFACT", str(artifact))
    monkeypatch.setenv("STUB_GH_LOG", str(tmp_path / "gh.log"))
    return artifact


def manual_merge(repo):
    return merge_branch(repo, "feat/ci", 50, {SCRIPT_FILE: "print('ci')\n"},
                        "2026-10-05T12:00:00+00:00")


def test_manual_record_carries_ci_snapshot_from_artifact(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    manual_merge(repo)
    head_sha = git(repo, "rev-parse", "HEAD^2")
    rec = only_record(repo, tmp_path)
    assert rec["lane"] == "manual"
    snap = rec["ci_snapshot"]
    assert snap["base"] == BASE_RESULTS
    assert snap["head"] == HEAD_RESULTS
    assert snap["diff"] == DIFF_RESULTS
    assert snap["head_sha"] == head_sha
    assert snap["run_id"] == 555
    log = (tmp_path / "gh.log").read_text()
    assert head_sha in log  # the run was looked up by the PR head sha


def test_squash_manual_record_resolves_head_sha_through_pr(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    monkeypatch.setenv("STUB_GH_PR_HEAD", "c" * 40)
    squash_commit(repo, {SCRIPT_FILE: "print('q')\n"}, "Tweak (#61)", "2026-10-06T12:00:00+00:00")
    rec = only_record(repo, tmp_path)
    assert rec["ci_snapshot"]["head_sha"] == "c" * 40
    assert rec["ci_snapshot"]["head"] == HEAD_RESULTS


def test_gh_failing_leaves_ci_snapshot_null_and_still_writes(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "fail")
    manual_merge(repo)
    rec = only_record(repo, tmp_path)
    assert "ci_snapshot" in rec and rec["ci_snapshot"] is None


def test_no_artifact_run_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "norun")
    manual_merge(repo)
    assert only_record(repo, tmp_path)["ci_snapshot"] is None


def test_artifact_missing_a_file_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    (stub_gh / "head.json").unlink()
    manual_merge(repo)
    assert only_record(repo, tmp_path)["ci_snapshot"] is None


def test_malformed_artifact_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    (stub_gh / "base.json").write_text("not json")
    manual_merge(repo)
    assert only_record(repo, tmp_path)["ci_snapshot"] is None


def test_oversized_artifact_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    big = {"results": {"a.py": "pass"}, "pad": "x" * 1_100_000}
    (stub_gh / "head.json").write_text(json.dumps(big))
    manual_merge(repo)
    assert only_record(repo, tmp_path)["ci_snapshot"] is None


def test_wrong_shape_artifact_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    (stub_gh / "base.json").write_text(json.dumps({"results": {"a.py": {"nested": [1]}}}))
    manual_merge(repo)
    assert only_record(repo, tmp_path)["ci_snapshot"] is None


def test_wrong_shape_diff_is_dropped_but_snapshot_kept(repo, tmp_path, monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    (stub_gh / "diff.json").write_text(json.dumps({"added": {"deep": {"x": [1]}}}))
    manual_merge(repo)
    snap = only_record(repo, tmp_path)["ci_snapshot"]
    assert snap["diff"] is None and snap["head"] == HEAD_RESULTS


def test_gh_not_installed_leaves_ci_snapshot_null(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no gh anywhere
    manual_merge(repo)
    out = tmp_path / "changes"
    result = run_writer(repo, out)
    assert len(result["written"]) == 1 and not result["errors"]
    (rec,) = records(out).values()
    assert rec["ci_snapshot"] is None


def test_seed_and_learnings_records_do_not_get_ci_snapshot(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    merge_branch(repo, "seed/s9", 70, {SCRIPT_FILE: "print('s')\n"},
                 "2026-10-05T12:00:00+00:00", branch_msg="Apply\n\nSeed-Id: seed-9")
    merge_branch(repo, "learn/l9", 71, {SCRIPT_FILE: "print('l')\n"},
                 "2026-10-06T12:00:00+00:00", branch_msg="Apply\n\nLearning-Ids: L-1")
    out = tmp_path / "changes"
    run_writer(repo, out)
    recs = list(records(out).values())
    assert {r["lane"] for r in recs} == {"seed", "learnings"}
    for rec in recs:
        assert "ci_snapshot" not in rec
        jsonschema.validate(rec, SCHEMA)
    assert not (tmp_path / "gh.log").exists()  # gh never even called for these lanes


def test_records_with_and_without_ci_snapshot_validate(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    manual_merge(repo)
    with_snap = only_record(repo, tmp_path)
    assert with_snap["ci_snapshot"]
    jsonschema.validate(with_snap, SCHEMA)
    jsonschema.validate(dict(with_snap, ci_snapshot=None), SCHEMA)
    no_field = {k: v for k, v in with_snap.items() if k != "ci_snapshot"}
    jsonschema.validate(no_field, SCHEMA)


def test_schema_rejects_malformed_ci_snapshot(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    manual_merge(repo)
    rec = only_record(repo, tmp_path)
    for bad in ("a string", 5, [], {"base": {}, "head": {}}):  # last lacks head_sha/run_id
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(dict(rec, ci_snapshot=bad), SCHEMA)


def test_fetch_ci_snapshot_returns_none_on_gh_failure(monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "fail")
    assert wcr.fetch_ci_snapshot("Org/Repo", "a" * 40) is None


def test_commit_fails_closed_when_jsonschema_missing(repo, tmp_path, monkeypatch, capsys):
    merge_branch(repo, "feat/a", 20, {SCRIPT_FILE: "print('a')\n"}, "2026-10-05T12:00:00+00:00")
    monkeypatch.setattr(wcr, "load_schema_validator", lambda: None)
    out = tmp_path / "changes"
    rc = wcr.main(["--repo", str(repo), "--since", "2000-01-01", "--ref", "main",
                   "--out-dir", str(out), "--commit"])
    assert rc == 1
    assert "jsonschema is not installed" in capsys.readouterr().err
    assert not out.exists() or list(out.iterdir()) == []


def test_without_commit_missing_jsonschema_only_warns(repo, tmp_path, monkeypatch, capsys):
    merge_branch(repo, "feat/a", 20, {SCRIPT_FILE: "print('a')\n"}, "2026-10-05T12:00:00+00:00")
    monkeypatch.setattr(wcr, "load_schema_validator", lambda: None)
    out = tmp_path / "changes"
    rc = wcr.main(["--repo", str(repo), "--since", "2000-01-01", "--ref", "main",
                   "--out-dir", str(out)])
    assert rc == 0
    assert "warning: jsonschema is not installed" in capsys.readouterr().err


def test_dry_run_skips_ci_lookup(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    manual_merge(repo)
    result = run_writer(repo, tmp_path / "changes", dry_run=True)
    assert len(result["written"]) == 1
    assert not (tmp_path / "gh.log").exists()  # gh never invoked


def test_spent_budget_skips_gh(monkeypatch, stub_gh):
    monkeypatch.setenv("STUB_GH_MODE", "ok")
    assert wcr.fetch_ci_snapshot("Org/Repo", "a" * 40, deadline=0) is None
    assert not (stub_gh.parent / "gh.log").exists()


def test_write_exclusive_never_clobbers_and_leaves_no_temp(tmp_path):
    d = Path(tmp_path) / "out"
    d.mkdir()
    target = d / "c.json"
    assert wcr._write_exclusive(target, "one\n") is True
    assert wcr._write_exclusive(target, "two\n") is False
    assert target.read_text() == "one\n"
    assert [p.name for p in d.iterdir()] == ["c.json"]


def test_ci_guards_cap_key_counts_to_schema_max_properties():
    big = {f"k{i}": 1 for i in range(21)}
    ok = {f"k{i}": 1 for i in range(20)}
    res = {"a.py": "pass"}
    assert wcr._valid_results_doc({"results": res, "totals": ok})
    assert not wcr._valid_results_doc({"results": res, "totals": big})
    assert wcr._valid_diff_doc({"totals": ok})
    assert not wcr._valid_diff_doc({"totals": big})
    assert not wcr._valid_diff_doc({f"k{i}": 1 for i in range(21)})
