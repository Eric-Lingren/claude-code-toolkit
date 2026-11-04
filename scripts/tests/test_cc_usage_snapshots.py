"""Tests for cc-usage-benchmark snapshot persistence.

Characterization tests (read/write, trend, digest, drift) pin the behaviour of
the local snapshot file. They address the file through mod.SNAPSHOT_PATH, so
they hold both before and after the file became per-host.
"""

import importlib.util
import json
import pathlib
from collections import Counter

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "usage" / "cc-usage-benchmark.py")

EXPECTED = {"tdd": "sonnet", "build-code": "sonnet", "standup": "haiku"}


def load_module(name="cc_usage_benchmark"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.setattr(m, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(m, "SNAPSHOT_PATH",
                        str(tmp_path / pathlib.Path(m.SNAPSHOT_PATH).name))
    monkeypatch.setattr(m, "current_iso_week", lambda: "2026-W46")
    return m


def per_week(**weeks):
    """per_week["2026-W44"] = {"tdd": Counter(...)} from kwargs W44=..."""
    return {f"2026-{k}": v for k, v in weeks.items()}


PER_WEEK = per_week(
    W43={"tdd": Counter(sonnet=4), "standup": Counter(haiku=2)},
    W44={"tdd": Counter(sonnet=3, opus=1), "standup": Counter(haiku=1, sonnet=2)},
    W45={"tdd": Counter(sonnet=2, opus=2), "build-code": Counter(sonnet=5),
         "standup": Counter(haiku=3, sonnet=1)},
)


def trend_weeks():
    def w(opus, sonnet, haiku, tok):
        return {"turns": Counter(opus=opus, sonnet=sonnet, haiku=haiku),
                "tok": {"sonnet": {"in": tok, "out": tok, "cache_w": 0, "cache_r": 0}}}
    return {"2026-W44": w(10, 60, 30, 1_000_000),
            "2026-W45": w(20, 50, 30, 2_000_000)}


# ---------------------------------------------------------------- read/write

def test_load_snapshot_missing_file_is_empty(mod):
    assert mod.load_snapshot() == {}


def test_load_snapshot_corrupt_file_is_empty(mod):
    pathlib.Path(mod.SNAPSHOT_PATH).write_text("{not json")
    assert mod.load_snapshot() == {}


def test_write_snapshot_backfills_every_week_and_round_trips(mod):
    snap = mod.write_snapshot(EXPECTED, PER_WEEK)
    assert sorted(snap) == ["2026-W43", "2026-W44", "2026-W45"]
    on_disk = json.loads(pathlib.Path(mod.SNAPSHOT_PATH).read_text())
    assert on_disk == snap
    assert mod.load_snapshot() == snap
    w45 = on_disk["2026-W45"]["adherence"]
    assert w45["n"] == 13 and w45["match"] == 10  # sonnet 2+5 + haiku 3
    assert w45["skills"]["tdd"] == {"exp": "sonnet", "mix": {"sonnet": 2, "opus": 2}}


def test_write_snapshot_keeps_weeks_not_in_logs(mod):
    mod.write_snapshot(EXPECTED, per_week(W43={"tdd": Counter(sonnet=4)}))
    snap = mod.write_snapshot(EXPECTED, per_week(W44={"tdd": Counter(sonnet=1)}))
    assert sorted(snap) == ["2026-W43", "2026-W44"]


def test_write_snapshot_is_idempotent(mod):
    first = mod.write_snapshot(EXPECTED, PER_WEEK)
    text1 = pathlib.Path(mod.SNAPSHOT_PATH).read_text()
    second = mod.write_snapshot(EXPECTED, PER_WEEK)
    text2 = pathlib.Path(mod.SNAPSHOT_PATH).read_text()
    assert first == second
    assert text1 == text2


def test_write_snapshot_rerun_overwrites_week_entry(mod):
    mod.write_snapshot(EXPECTED, per_week(W44={"tdd": Counter(sonnet=1)}))
    snap = mod.write_snapshot(EXPECTED, per_week(W44={"tdd": Counter(sonnet=7)}))
    assert snap["2026-W44"]["adherence"]["skills"]["tdd"]["mix"] == {"sonnet": 7}


# ------------------------------------------------------- trend/digest/drift

def test_drift_weeks_counts_consecutive_off_tier_weeks(mod):
    snap = mod.write_snapshot(EXPECTED, PER_WEEK)
    assert mod.drift_weeks(snap, "tdd", "2026-W45") == 2
    assert mod.drift_weeks(snap, "tdd", "2026-W43") == 0
    assert mod.drift_weeks(snap, "standup", "2026-W45") == 2
    assert mod.drift_weeks(snap, "build-code", "2026-W45") == 0
    assert mod.drift_weeks(snap, "build-code", "2026-W44") == 0


def test_trend_view_output(mod, capsys):
    mod.print_trend(trend_weeks())
    out = capsys.readouterr().out
    assert "WEEKLY MODEL TREND" in out
    assert "2026-W44" in out and "2026-W45" in out
    assert out == GOLDEN_TREND


def digest_out(mod, monkeypatch, capsys, weeks=PER_WEEK):
    monkeypatch.setattr(mod, "compute_trend", lambda roots: trend_weeks())
    monkeypatch.setattr(mod, "compute_adherence",
                        lambda roots, cfg: (EXPECTED, weeks))
    mod.digest_report({}, "ignored.json")
    return capsys.readouterr().out


def test_digest_view_output(mod, monkeypatch, capsys):
    assert digest_out(mod, monkeypatch, capsys) == GOLDEN_DIGEST


def test_digest_reads_existing_local_snapshot_history(mod, monkeypatch, capsys):
    # W43 exists only in the local file, not in the logs: it must feed drift.
    old = {"2026-W43": {"adherence": {
        "overall_pct": 0.0, "match": 0, "n": 3,
        "skills": {"standup": {"exp": "haiku", "mix": {"sonnet": 3}}}}}}
    pathlib.Path(mod.SNAPSHOT_PATH).write_text(json.dumps(old))
    logs = {k: v for k, v in PER_WEEK.items() if k != "2026-W43"}
    out = digest_out(mod, monkeypatch, capsys, weeks=logs)
    assert "standup" in out and "3wk" in out


def test_digest_with_no_complete_week(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "compute_trend", lambda roots: {})
    monkeypatch.setattr(mod, "compute_adherence", lambda roots, cfg: ({}, {}))
    mod.digest_report({}, "ignored.json")
    assert "no complete week of data yet" in capsys.readouterr().out


GOLDEN_TREND = (
    "================================================================\n"
    "WEEKLY MODEL TREND (assistant turns + est cost)\n"
    "================================================================\n"
    "  week       turns  opus%   son%   hai%      est$\n"
    "  2026-W44     100    10%    60%    30% $      18\n"
    "  2026-W45     100    20%    50%    30% $      36\n\n"
)
GOLDEN_DIGEST = (
    "================================================================\n"
    "CC USAGE DIGEST -- 2026-W45 (vs 2026-W44, last complete weeks)\n"
    "================================================================\n"
    "  spend    $     36   ^100%\n"
    "  opus%        20%   ^10pt\n"
    "  adher.       77%   ^20pt   (10/13)\n\n"
    "  DRIFT (act):\n"
    "  tdd                        exp sonnet -> opus:2        2/4  2wk  "
    "-> re-tier up or add model override\n"
    "  standup                    exp haiku  -> sonnet:1      1/4  2wk  "
    "-> T1 ran pricier -> check launch path / hook\n\n"
)


# ------------------------------------------------------------- per-host files

@pytest.fixture
def host_mod(tmp_path, monkeypatch):
    """Module loaded fresh with host and report dir pinned via the environment."""
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("CC_USAGE_HOST", "alpha")
    return load_module("cc_usage_benchmark_host")


def test_write_snapshot_writes_host_file_and_never_touches_other_hosts(host_mod, tmp_path):
    other = tmp_path / "snapshots-beta.json"
    other.write_text('{"sentinel": true}')
    host_mod.write_snapshot(EXPECTED, PER_WEEK)
    assert (tmp_path / "snapshots-alpha.json").exists()
    assert not (tmp_path / "snapshots.json").exists()
    assert other.read_text() == '{"sentinel": true}'


def test_local_view_reads_only_this_hosts_file(host_mod, tmp_path):
    (tmp_path / "snapshots-beta.json").write_text(json.dumps(
        {"2026-W40": {"adherence": {"skills": {}}}}))
    assert host_mod.load_snapshot() == {}
    host_mod.write_snapshot(EXPECTED, PER_WEEK)
    assert "2026-W40" not in host_mod.load_snapshot()


def test_write_snapshot_same_week_is_idempotent_per_host(host_mod, tmp_path):
    host_mod.write_snapshot(EXPECTED, PER_WEEK)
    first = (tmp_path / "snapshots-alpha.json").read_text()
    host_mod.write_snapshot(EXPECTED, PER_WEEK)
    assert (tmp_path / "snapshots-alpha.json").read_text() == first
    assert [p.name for p in tmp_path.glob("snapshots*.json")] == ["snapshots-alpha.json"]


def test_host_defaults_to_short_hostname(monkeypatch, tmp_path):
    monkeypatch.delenv("CC_USAGE_HOST", raising=False)
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(tmp_path / "id" / "host-id"))
    m = load_module("cc_usage_benchmark_default_host")
    import subprocess
    want = subprocess.run(["hostname", "-s"], capture_output=True,
                          text=True).stdout.strip()
    assert m.HOST == want
    assert m.SNAPSHOT_PATH == str(tmp_path / f"snapshots-{want}.json")


def test_first_run_persists_current_hostname_as_machine_id(monkeypatch, tmp_path):
    monkeypatch.delenv("CC_USAGE_HOST", raising=False)
    id_file = tmp_path / "id" / "host-id"
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(id_file))
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    m = load_module("cc_usage_benchmark_persist")
    host = m.HOST  # resolved lazily on first access, not at import
    assert id_file.read_text().strip() == host


def test_machine_id_survives_hostname_rename(monkeypatch, tmp_path):
    """A renamed host keeps writing the same snapshot file, so run counts are
    not doubled by two overlapping files."""
    monkeypatch.delenv("CC_USAGE_HOST", raising=False)
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(tmp_path / "host-id"))
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    old = load_module("cc_usage_benchmark_before_rename")
    old.write_snapshot(EXPECTED, PER_WEEK)

    new_name = "Renamed-Mac-9999"
    renamed = load_module("cc_usage_benchmark_after_rename")
    monkeypatch.setattr(renamed, "_live_hostname", lambda: new_name)
    assert renamed._short_hostname() == old.HOST
    assert renamed._short_hostname() != new_name
    renamed.write_snapshot(EXPECTED, PER_WEEK)

    assert len(list(tmp_path.glob("snapshots-*.json"))) == 1
    counts = renamed.load_run_counts()
    assert counts["tdd"]["2026-W45"] == 4  # sonnet 2 + opus 2, once


def test_env_override_beats_persisted_machine_id(monkeypatch, tmp_path):
    id_file = tmp_path / "host-id"
    id_file.write_text("saved-id\n")
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(id_file))
    monkeypatch.setenv("CC_USAGE_HOST", "pinned")
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    assert load_module("cc_usage_benchmark_pinned").HOST == "pinned"
    monkeypatch.delenv("CC_USAGE_HOST")
    assert load_module("cc_usage_benchmark_saved").HOST == "saved-id"


def test_unwritable_id_file_falls_back_to_live_hostname(monkeypatch, tmp_path):
    monkeypatch.delenv("CC_USAGE_HOST", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so makedirs under it fails")
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(blocker / "sub" / "host-id"))
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    m = load_module("cc_usage_benchmark_unwritable")
    assert m.HOST


def test_default_report_dir_matches_weekly_run(monkeypatch):
    monkeypatch.delenv("CC_USAGE_REPORT_DIR", raising=False)
    monkeypatch.setenv("CC_USAGE_HOST", "alpha")
    m = load_module("cc_usage_benchmark_default_dir")
    weekly = (SCRIPT.parent / "weekly-usage-report.sh").read_text()
    assert "${CC_USAGE_REPORT_DIR:-$HOME/.dotfiles/claude-code-shared/usage-reports}" in weekly
    assert m.REPORT_DIR == str(pathlib.Path.home()
                               / ".dotfiles/claude-code-shared/usage-reports")


def snap_file(path, weeks):
    """weeks: {"2026-W44": {"tdd": {"sonnet": 3, "opus": 1}}}"""
    path.write_text(json.dumps({
        wk: {"adherence": {"skills": {s: {"exp": "sonnet", "mix": mix}
                                      for s, mix in skills.items()}}}
        for wk, skills in weeks.items()}))


def test_run_counts_sums_mix_across_host_files(host_mod, tmp_path):
    snap_file(tmp_path / "snapshots-alpha.json", {
        "2026-W44": {"tdd": {"sonnet": 3, "opus": 1}, "standup": {"haiku": 2}},
        "2026-W45": {"tdd": {"sonnet": 1}}})
    snap_file(tmp_path / "snapshots-beta.json", {
        "2026-W44": {"tdd": {"sonnet": 5}},
        "2026-W46": {"tdd": {"sonnet": 2}}})
    counts = host_mod.load_run_counts()
    assert counts == {
        "tdd": {"2026-W44": 9, "2026-W45": 1, "2026-W46": 2},
        "standup": {"2026-W44": 2},
    }


def test_run_counts_ignores_missing_host_and_bad_files(host_mod, tmp_path):
    snap_file(tmp_path / "snapshots-alpha.json", {"2026-W44": {"tdd": {"sonnet": 3}}})
    (tmp_path / "snapshots-gamma.json").write_text("{broken")
    # snapshots-beta.json never written: a machine that never reports adds nothing.
    assert host_mod.load_run_counts() == {"tdd": {"2026-W44": 3}}


def test_run_counts_empty_dir(host_mod):
    assert host_mod.load_run_counts() == {}


def test_run_counts_accepts_explicit_dir(host_mod, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    snap_file(other / "snapshots-zeta.json", {"2026-W44": {"tdd": {"opus": 4}}})
    assert host_mod.load_run_counts(str(other)) == {"tdd": {"2026-W44": 4}}


def test_import_does_not_resolve_host_or_write_host_id(monkeypatch, tmp_path):
    """exec_module (used by correction-rate.py) must be side-effect free."""
    monkeypatch.delenv("CC_USAGE_HOST", raising=False)
    id_file = tmp_path / "id" / "host-id"
    monkeypatch.setenv("CC_USAGE_HOST_FILE", str(id_file))
    monkeypatch.setenv("CC_USAGE_REPORT_DIR", str(tmp_path))
    m = load_module("cc_usage_benchmark_lazy")
    assert "HOST" not in vars(m) and "SNAPSHOT_PATH" not in vars(m)
    assert not id_file.exists()
    m.write_snapshot(EXPECTED, PER_WEEK)
    assert id_file.exists()
