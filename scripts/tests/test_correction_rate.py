"""Tests for the per-skill correction-rate metric (usage/correction-rate.py).

The script filename has a hyphen, so it is loaded through importlib.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "usage" / "correction-rate.py")


def load_module(name="correction_rate"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return load_module()


def learning(ts, improves, **extra):
    return {"type": "attribution", "timestamp": ts, "improves": improves, **extra}


def snap_file(path, weeks):
    """weeks: {"2026-W44": {"tdd": {"sonnet": 3, "opus": 1}}}"""
    path.write_text(json.dumps({
        wk: {"adherence": {"skills": {s: {"exp": "sonnet", "mix": mix}
                                      for s, mix in skills.items()}}}
        for wk, skills in weeks.items()}))


# 2026-11-02 is Monday of ISO week 2026-W45; 2026-10-26 is Monday of W44.
W45_DAY = "2026-11-03T10:00:00Z"
W44_DAY = "2026-10-27T10:00:00Z"
W43_DAY = "2026-10-20T10:00:00Z"
W42_DAY = "2026-10-13T10:00:00Z"
W41_DAY = "2026-10-06T10:00:00Z"


def test_iso_week_of_timestamp(mod):
    assert mod.iso_week(W45_DAY) == "2026-W45"
    assert mod.iso_week("2026-11-02T00:00:00Z") == "2026-W45"
    assert mod.iso_week("2026-11-01T23:59:59Z") == "2026-W44"
    assert mod.iso_week("garbage") is None
    assert mod.iso_week(None) is None


def test_rate_is_learnings_over_summed_runs_across_two_hosts(mod, tmp_path):
    snap_file(tmp_path / "snapshots-alpha.json",
              {"2026-W45": {"tdd": {"sonnet": 4, "opus": 2}}})
    snap_file(tmp_path / "snapshots-beta.json",
              {"2026-W45": {"tdd": {"sonnet": 6}}})
    runs = mod.load_run_counts(str(tmp_path))
    assert runs == {"tdd": {"2026-W45": 12}}
    learnings = [learning(W45_DAY, "tdd"), learning(W45_DAY, "tdd"),
                 learning(W45_DAY, "tdd"), learning(W45_DAY, "other")]
    out = mod.compute(learnings, runs, "2026-W45")
    tdd = out["skills"]["tdd"]
    assert tdd["learnings"] == 3
    assert tdd["runs"] == 12
    assert tdd["rate"] == pytest.approx(3 / 12)
    assert tdd["window_weeks"] == 1
    assert tdd["alarmable"] is True


def test_exactly_ten_runs_is_alarmable_in_one_week(mod):
    out = mod.compute([learning(W45_DAY, "tdd")], {"tdd": {"2026-W45": 10}},
                      "2026-W45")
    assert out["skills"]["tdd"]["alarmable"] is True
    assert out["skills"]["tdd"]["window_weeks"] == 1


def test_under_ten_runs_widens_window_until_ten(mod):
    runs = {"tdd": {"2026-W45": 4, "2026-W44": 3, "2026-W43": 4, "2026-W42": 50}}
    learnings = [learning(W45_DAY, "tdd"), learning(W43_DAY, "tdd"),
                 learning(W42_DAY, "tdd")]
    t = mod.compute(learnings, runs, "2026-W45")["skills"]["tdd"]
    # W45 (4) -> +W44 (7) -> +W43 (11): stops at 3 weeks, W42 never counted.
    assert t["window_weeks"] == 3
    assert t["runs"] == 11
    assert t["learnings"] == 2
    assert t["rate"] == pytest.approx(2 / 11)
    assert t["alarmable"] is True


def test_still_under_ten_after_four_weeks_stays_non_alarmable(mod):
    runs = {"tdd": {"2026-W45": 1, "2026-W44": 1, "2026-W43": 1, "2026-W42": 1,
                    "2026-W41": 500}}
    learnings = [learning(d, "tdd") for d in
                 (W45_DAY, W44_DAY, W43_DAY, W42_DAY, W41_DAY)]
    t = mod.compute(learnings, runs, "2026-W45")["skills"]["tdd"]
    assert t["window_weeks"] == 4
    assert t["runs"] == 4
    assert t["learnings"] == 4
    assert t["rate"] == pytest.approx(1.0)
    assert t["alarmable"] is False


def test_learnings_without_improves_are_excluded_and_counted(mod):
    learnings = [learning(W45_DAY, "tdd"), learning(W45_DAY, None),
                 learning(W44_DAY, ""), {"timestamp": W45_DAY}]
    out = mod.compute(learnings, {"tdd": {"2026-W45": 10}}, "2026-W45")
    assert out["unattributed"] == 3
    assert out["skills"]["tdd"]["learnings"] == 1
    assert list(out["skills"]) == ["tdd"]


def test_skill_with_learnings_but_no_runs_has_no_rate(mod):
    out = mod.compute([learning(W45_DAY, "ghost")], {}, "2026-W45")
    ghost = out["skills"]["ghost"]
    assert ghost["runs"] == 0
    assert ghost["rate"] is None
    assert ghost["alarmable"] is False


def test_skill_with_runs_but_no_learnings_has_zero_rate(mod):
    out = mod.compute([], {"tdd": {"2026-W45": 12}}, "2026-W45")
    assert out["skills"]["tdd"]["rate"] == 0
    assert out["skills"]["tdd"]["alarmable"] is True


def run_cli(report_dir, learnings_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--week", "2026-W45",
         "--report-dir", str(report_dir), "--learnings", str(learnings_path)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_hook_audit_data_does_not_affect_result(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    snap_file(report_dir / "snapshots-alpha.json",
              {"2026-W45": {"tdd": {"sonnet": 10}}})
    lj = tmp_path / "l.jsonl"
    lj.write_text(json.dumps(learning(W45_DAY, "tdd")) + "\n")
    before = run_cli(report_dir, lj)
    assert before["skills"]["tdd"]["learnings"] == 1
    # Drop a hook-audit.jsonl next to both inputs; the CLI must ignore it.
    audit_line = json.dumps(
        {"timestamp": W45_DAY, "skill": "tdd", "improves": "tdd", "event": "blocked"})
    for d in (report_dir, tmp_path):
        (d / "hook-audit.jsonl").write_text("\n".join([audit_line] * 50))
    after = run_cli(report_dir, lj)
    assert before == after
    # No entry point takes an audit-log argument.
    helptext = " ".join(subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True).stdout.lower().split())
    assert "audit" not in helptext.replace("hook-audit.jsonl is not used", "")


def test_learnings_outside_window_or_after_week_are_ignored(mod):
    learnings = [learning(W45_DAY, "tdd"), learning("2026-11-10T10:00:00Z", "tdd"),
                 learning("2026-01-05T10:00:00Z", "tdd")]
    t = mod.compute(learnings, {"tdd": {"2026-W45": 10}}, "2026-W45")["skills"]["tdd"]
    assert t["learnings"] == 1


def test_week_arithmetic_crosses_year_boundary(mod):
    assert mod.weeks_back("2027-W01", 3) == ["2027-W01", "2026-W53", "2026-W52"]


def test_load_learnings_skips_bad_lines(mod, tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps({"improves": "tdd"}) + "\n{bad\n\n")
    assert mod.load_learnings(str(p)) == [{"improves": "tdd"}]
    assert mod.load_learnings(str(tmp_path / "missing.jsonl")) == []


def test_cli_prints_json(tmp_path):
    snap_file(tmp_path / "snapshots-alpha.json", {"2026-W45": {"tdd": {"sonnet": 10}}})
    lj = tmp_path / "l.jsonl"
    lj.write_text(json.dumps(learning(W45_DAY, "tdd")) + "\n"
                  + json.dumps(learning(W45_DAY, None)) + "\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--week", "2026-W45",
         "--report-dir", str(tmp_path), "--learnings", str(lj)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["week"] == "2026-W45"
    assert out["unattributed"] == 1
    assert out["skills"]["tdd"]["rate"] == pytest.approx(0.1)


def lane_row(status, **extra):
    return {"id": "x", "improves": "skill-x", "status": status, **extra}


def test_lane_rates_three_merged_one_rejected(mod):
    rows = [lane_row("applied", merged_sha="a")] * 3 + [lane_row("rejected")]
    m = mod.lane_metrics(rows)
    assert m["merge_rate"] == 0.75
    assert m["rejected_rate"] == 0.25
    assert m["by_target"]["skill-x"]["merge_rate"] == 0.75


def test_lane_changes_requested_zero_when_none(mod):
    m = mod.lane_metrics([lane_row("applied", merged_sha="a"), lane_row("captured")])
    assert m["changes_requested"] == 0
    assert m["changes_requested_rate"] == 0


def test_lane_empty_and_non_lane_applied_ignored(mod):
    m = mod.lane_metrics([lane_row("applied")])
    assert m["resolved"] == 0 and m["merge_rate"] == 0


def test_lane_digest_lines_print_rates(mod):
    rows = [lane_row("applied", merged_sha="a")] * 3 + [lane_row("rejected")]
    text = "\n".join(mod.render_lane_digest(mod.lane_metrics(rows)))
    assert "merge rate: 0.75" in text
    assert "rejected rate: 0.25" in text
    assert "changes-requested rate: 0.00" in text
