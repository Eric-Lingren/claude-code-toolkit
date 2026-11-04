import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"
spec = importlib.util.spec_from_file_location("lane_select", d / "lane_select.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
_n = [0]


def row(target, days_old, **kw):
    _n[0] += 1
    ts = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = {"id": f"r{_n[0]}", "timestamp": ts, "improves": target, "confidence": "confirmed"}
    r.update(kw)
    return r


def sel(rows, open_targets=()):
    return s.select(rows, now=NOW, open_prs=lambda: list(open_targets))


def names(res):
    return [t for t, _ in res]


def test_config_values():
    config = s.config
    assert (config.OPEN_PR_CAP, config.MIN_COUNT, config.MIN_AGE_DAYS, config.GROUP_THRESHOLD) == (5, 3, 7, 3)


def test_candidate_and_null_target_never_returned():
    rows = [row("a", 30, confidence="candidate"), row("a", 30, confidence="candidate"),
            row("a", 30, confidence="candidate"), row(None, 30), row(None, 30), row(None, 30)]
    assert sel(rows) == []


def test_three_confirmed_eligible():
    assert names(sel([row("a", 1), row("a", 1), row("a", 2)])) == ["a"]


def test_two_young_not_eligible():
    assert sel([row("a", 1), row("a", 6)]) == []


def test_one_old_eligible():
    assert names(sel([row("a", 8)])) == ["a"]


def test_open_pr_target_skipped():
    rows = [row("a", 9), row("b", 9)]
    assert names(sel(rows, ["a"])) == ["b"]


def test_cap_returns_nothing():
    assert sel([row("z", 9)], ["a", "b", "c", "d", "e"]) == []


def test_ordering_oldest_then_biggest():
    rows = [row("new", 8), row("new", 8), row("new", 8),
            row("old", 20),
            row("tie", 8), row("tie", 8), row("tie", 8), row("tie", 8)]
    assert names(sel(rows)) == ["old", "tie", "new"]


def test_rejected_clustered_and_other_statuses_excluded():
    rows = [row("a", 30, status="rejected"), row("a", 30, status="clustered:12"),
            row("a", 30, status="applied"), row("a", 30, status="stale"), row("a", 30, status="invalid")]
    assert sel(rows) == []
    assert names(sel(rows + [row("a", 30, status="captured")])) == ["a"]


def test_selection_limited_to_remaining_slots():
    rows = [row(t, 10) for t in "abcdefg"]
    assert len(sel(rows, ["x", "y", "z"])) == 2


def test_load_rows_skips_bad_lines(tmp_path):
    p = tmp_path / "u.jsonl"
    p.write_text('{"improves":"a"}\nnot json\n\n{"improves":"b"}\n')
    assert [r["improves"] for r in s.load_rows(p)] == ["a", "b"]
