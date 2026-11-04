"""Tests for scripts/attribution/ci-snapshot-diff.py and the informational test-snapshot workflow."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SHARED = Path(__file__).resolve().parents[2]
REPO = SHARED.parent
SCRIPT = SHARED / "scripts" / "attribution" / "ci-snapshot-diff.py"
POST_SCRIPT = SHARED / "scripts" / "attribution" / "ci-post-snapshot-comment.sh"
SNAPSHOT_WF = REPO / ".github" / "workflows" / "test-snapshot.yml"
TEST_WF = REPO / ".github" / "workflows" / "test.yml"

spec = importlib.util.spec_from_file_location("ci_snapshot_diff", SCRIPT)
csd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csd)


def snapshot(results):
    passed = sum(1 for s in results.values() if s == "pass")
    return {"results": results,
            "totals": {"passed": passed, "failed": len(results) - passed, "total": len(results)}}


def write(tmp_path, name, results):
    p = tmp_path / name
    p.write_text(json.dumps(snapshot(results)))
    return p


def run_cli(tmp_path, base, head):
    out = tmp_path / "comment.md"
    args = [sys.executable, str(SCRIPT),
            "--base", str(write(tmp_path, "base.json", base)),
            "--head", str(write(tmp_path, "head.json", head)),
            "--comment-out", str(out)]
    return subprocess.run(args, capture_output=True, text=True), out


BASE = {"a.sh": "pass", "b.sh": "pass", "c.sh": "fail", "d.sh": "fail", "gone.sh": "pass"}
HEAD = {"a.sh": "pass", "b.sh": "fail", "c.sh": "pass", "d.sh": "fail", "new.sh": "pass", "newbad.sh": "fail"}


def test_reports_newly_failing_newly_passing_and_unchanged():
    diff = csd.compare(BASE, HEAD)
    assert diff["newly_failing"] == ["b.sh"]
    assert diff["newly_passing"] == ["c.sh"]
    assert diff["unchanged"] == ["a.sh", "d.sh"]
    assert diff["added"] == [{"file": "new.sh", "status": "pass"}, {"file": "newbad.sh", "status": "fail"}]
    assert diff["removed"] == [{"file": "gone.sh", "status": "pass"}]
    assert diff["passing_set_changed"] is True


def test_cli_prints_json_and_writes_comment_when_changed(tmp_path):
    proc, out = run_cli(tmp_path, BASE, HEAD)
    assert proc.returncode == 0, proc.stderr
    diff = json.loads(proc.stdout)
    assert diff["newly_failing"] == ["b.sh"] and diff["newly_passing"] == ["c.sh"]
    text = out.read_text()
    assert csd.COMMENT_MARKER in text
    assert "`b.sh`" in text and "`c.sh`" in text and "`new.sh`" in text and "`gone.sh`" in text
    assert "newbad.sh" not in text


def test_identical_results_produce_no_comment_text(tmp_path):
    assert csd.build_comment(csd.compare(BASE, dict(BASE))) == ""
    proc, out = run_cli(tmp_path, BASE, dict(BASE))
    assert proc.returncode == 0
    assert not out.exists()
    diff = json.loads(proc.stdout)
    assert diff["passing_set_changed"] is False
    assert diff["unchanged"] == sorted(BASE)


def test_failing_file_staying_failing_does_not_trigger_comment():
    base = {"a.sh": "pass", "x.sh": "fail"}
    head = {"a.sh": "pass", "x.sh": "fail", "y.sh": "fail"}
    assert csd.build_comment(csd.compare(base, head)) == ""


def test_bad_input_exits_2(tmp_path):
    good = write(tmp_path, "good.json", BASE)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--base", str(good), "--head", str(bad)],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    missing = subprocess.run([sys.executable, str(SCRIPT), "--base", str(good),
                              "--head", str(tmp_path / "nope.json")], capture_output=True, text=True)
    assert missing.returncode == 2
    noargs = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert noargs.returncode == 2


def load_yaml(path):
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    if shutil.which("ruby"):
        proc = subprocess.run(
            ["ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.safe_load(File.read(ARGV[0])))", str(path)],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    pytest.skip("no YAML parser available (need PyYAML or ruby)")


def test_snapshot_workflow_parses_and_steps_are_continue_on_error():
    wf = load_yaml(SNAPSHOT_WF)
    jobs = wf["jobs"]
    assert jobs, "workflow has no jobs"
    for name, job in jobs.items():
        assert job.get("continue-on-error") is True, f"job {name} must be continue-on-error"
        assert job["steps"]
        for step in job["steps"]:
            assert step.get("continue-on-error") is True, f"step {step.get('name')!r} must be continue-on-error"
    steps = " ".join(str(s.get("run", "")) + str(s.get("uses", "")) for j in jobs.values() for s in j["steps"])
    assert "RUN_ALL_TESTS_JSON" in steps
    assert "ci-snapshot-diff.py" in steps
    assert "upload-artifact" in steps
    assert "ci-post-snapshot-comment.sh" in steps


def test_existing_test_workflow_run_all_tests_step_unchanged():
    wf = load_yaml(TEST_WF)
    steps = wf["jobs"]["test"]["steps"]
    run_steps = [s for s in steps if s.get("run", "").strip() == "bash run-all-tests.sh"]
    assert len(run_steps) == 1
    step = run_steps[0]
    assert step["name"] == "Run all tests"
    assert "continue-on-error" not in step
    assert "continue-on-error" not in wf["jobs"]["test"]
    assert "RUN_ALL_TESTS_JSON" not in TEST_WF.read_text()


def _run_post_with_fake_gh(tmp_path, comment_file, existing_id):
    """Run the post script with a stub gh on PATH; returns (proc, logged gh invocations)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" | tr "\\n" " " >> "$GH_LOG"; echo >> "$GH_LOG"\n'
        'case "$*" in *"--paginate"*) printf "%s" "$GH_EXISTING";; esac\n'
    )
    gh.chmod(0o755)
    import os
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GH_LOG": str(log),
           "GH_EXISTING": existing_id}
    proc = subprocess.run(["bash", str(POST_SCRIPT), "o/r", "1", str(comment_file)],
                          capture_output=True, text=True, env=env)
    return proc, (log.read_text().splitlines() if log.exists() else [])


def test_post_comment_script_skips_when_no_comment_file_and_no_existing_comment(tmp_path):
    proc, calls = _run_post_with_fake_gh(tmp_path, tmp_path / "none.md", "")
    assert proc.returncode == 0
    assert "nothing to post" in proc.stdout
    assert not any("PATCH" in c or "POST" in c for c in calls)
    usage = subprocess.run(["bash", str(POST_SCRIPT)], capture_output=True, text=True)
    assert usage.returncode == 2


def run_post_with_stub_gh(tmp_path, existing_id):
    """Run the post script with a stub gh on PATH. The stub logs every call and answers the
    marker-lookup (GET comments) with existing_id ('' means no marker comment on the PR)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    stub = bindir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$GH_STUB_LOG"\n'
        'case "$*" in\n'
        '  *"-X PATCH"*|*"-X POST"*) ;;\n'
        '  *) printf "%s" "$GH_STUB_EXISTING" ;;\n'
        "esac\n")
    stub.chmod(0o755)
    comment = tmp_path / "comment.md"
    comment.write_text(csd.COMMENT_MARKER + "\nbody text\n")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
           "GH_STUB_LOG": str(log), "GH_STUB_EXISTING": existing_id}
    proc = subprocess.run(["bash", str(POST_SCRIPT), "o/r", "7", str(comment)],
                          capture_output=True, text=True, env=env)
    # The comment body is multi-line, so keep only the lines that start a gh call.
    lines = log.read_text().splitlines() if log.exists() else []
    calls = [ln for ln in lines if ln.startswith("api ")]
    return proc, calls


def test_post_comment_script_patches_existing_marker_comment(tmp_path):
    proc, calls = run_post_with_stub_gh(tmp_path, "4242")
    assert proc.returncode == 0, proc.stderr
    assert "updated comment 4242" in proc.stdout
    assert len(calls) == 2
    assert "repos/o/r/issues/7/comments" in calls[0] and "--paginate" in calls[0]
    assert csd.COMMENT_MARKER in calls[0]
    assert calls[1].startswith("api -X PATCH repos/o/r/issues/comments/4242")
    assert not any("-X POST" in c for c in calls)


def test_post_comment_script_posts_new_comment_when_no_marker(tmp_path):
    proc, calls = run_post_with_stub_gh(tmp_path, "")
    assert proc.returncode == 0, proc.stderr
    assert "posted new comment" in proc.stdout
    assert len(calls) == 2
    assert calls[1].startswith("api -X POST repos/o/r/issues/7/comments")
    assert not any("-X PATCH" in c for c in calls)


def test_post_comment_script_patches_stale_comment_when_no_comment_file(tmp_path):
    proc, calls = _run_post_with_fake_gh(tmp_path, tmp_path / "none.md", "4242")
    assert proc.returncode == 0
    patches = [c for c in calls if "PATCH" in c]
    assert len(patches) == 1
    assert "issues/comments/4242" in patches[0]
    assert "unchanged" in patches[0]
    assert "<!-- test-snapshot-diff -->" in patches[0]
    assert not any("-X POST" in c for c in calls)


def _snapshot_steps():
    return load_yaml(SNAPSHOT_WF)["jobs"]["snapshot"]["steps"]


def _step_running(steps, needle):
    matches = [s for s in steps if needle in str(s.get("run", ""))]
    assert len(matches) == 1, f"expected exactly one step running {needle}"
    return matches[0]


def test_comment_step_is_gated_on_compare_step_success():
    """A crashed compare step writes no comment file; the post script reads that as 'nothing
    changed' and would PATCH an earlier 'Newly failing' comment. So the comment step must only
    run when the compare step succeeded. Every step is continue-on-error, so the gate has to use
    `outcome` (the real result): the default success() and `conclusion` both report success for
    a failed continue-on-error step."""
    steps = _snapshot_steps()
    compare = _step_running(steps, "ci-snapshot-diff.py")
    compare_id = compare.get("id")
    assert compare_id, "compare step needs an id so later steps can reference it"
    comment = _step_running(steps, "ci-post-snapshot-comment.sh")
    cond = " ".join(str(comment.get("if", "")).split())
    assert cond == f"steps.{compare_id}.outcome == 'success'", cond
    assert comment.get("continue-on-error") is True


def test_comment_script_runs_from_base_checkout_not_pr_head():
    """The comment step holds a write token, so it must not execute PR-head code."""
    comment = _step_running(_snapshot_steps(), "ci-post-snapshot-comment.sh")
    run = comment["run"]
    assert "$RUNNER_TEMP/base/claude-code-shared/scripts/attribution/ci-post-snapshot-comment.sh" in run
    assert "bash claude-code-shared/scripts/attribution/ci-post-snapshot-comment.sh" not in run


def test_post_script_without_gate_would_patch_existing_comment(tmp_path):
    """The script alone, given the missing comment file a crashed compare step leaves behind,
    would PATCH an existing comment. That is why the gate asserted in
    test_comment_step_is_gated_on_compare_step_success is needed."""
    ungated = tmp_path / "ungated"
    ungated.mkdir()
    _, calls = _run_post_with_fake_gh(ungated, ungated / "comment.md", "4242")
    assert any("PATCH" in c for c in calls)


def test_workflow_uses_attribution_paths():
    text = (SHARED.parent / ".github" / "workflows" / "test-snapshot.yml").read_text()
    assert "claude-code-shared/scripts/attribution/ci-snapshot-diff.py" in text
    assert "claude-code-shared/scripts/attribution/ci-post-snapshot-comment.sh" in text
    assert "scripts/bank" not in text
    assert (SHARED / "scripts" / "attribution" / "ci-post-snapshot-comment.sh").is_file()


def test_weekly_report_default_writer_path_is_an_executable():
    import re
    text = (SHARED / "scripts" / "usage" / "weekly-usage-report.sh").read_text()
    m = re.search(r'CC_CHANGE_RECORDS_WRITER:-\$HOME/\.dotfiles/claude-code-shared/([^}"]+)\}', text)
    assert m, "default writer path not found"
    writer = SHARED / m.group(1)
    assert writer.is_file(), writer
    assert os.access(writer, os.X_OK), writer
