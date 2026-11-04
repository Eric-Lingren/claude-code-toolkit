"""Tests for the median-output-tokens-per-skill trend line in cc-usage-benchmark.

Part 1 (characterization) pins the existing report sections against a fixture
transcript tree. Part 2 covers the new alert-free token trend.
"""

import importlib.util
import json
import pathlib

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "usage" / "cc-usage-benchmark.py")
WEEKLY = SCRIPT.parent / "weekly-usage-report.sh"

CONFIG = {
    "tiers": {"T1": {"model": "haiku"}, "T2": {"model": "sonnet"}},
    "skills": {"tdd": "T2", "standup": "T1"},
}


def load_module(name="cc_usage_benchmark_tokens"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.setattr(m, "REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(m, "SNAPSHOT_PATH",
                        str(tmp_path / "reports" / "snapshots-test.json"))
    monkeypatch.setattr(m, "SKILLS_DIR", str(tmp_path / "no-skills"))
    monkeypatch.setattr(m, "current_iso_week", lambda: "2026-W46")
    return m


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "model-tiers.json"
    p.write_text(json.dumps(CONFIG))
    return str(p)


def user(ts, skill):
    return {"type": "user", "timestamp": ts,
            "message": {"content": f"<command-name>/{skill}</command-name>"}}


def asst(ts, mid, out, model="claude-sonnet-4"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"output_tokens": out},
                        "content": [{"type": "text", "text": "x"}]}}


def run(day, skill, out, n):
    """One skill invocation on `day` (YYYY-MM-DD) with a single assistant turn.

    The turn is written twice under one message id, the way a multi-block
    assistant message lands in a transcript: output_tokens must count once.
    """
    t0, t1, t2 = (f"{day}T10:{n:02d}:00Z", f"{day}T10:{n:02d}:10Z",
                  f"{day}T10:{n:02d}:11Z")
    return [user(t0, skill), asst(t1, f"m{n}", out), asst(t2, f"m{n}", out)]


W44, W45 = "2026-10-27", "2026-11-03"

# (day, skill, output_tokens). tdd W44 has 3 runs (odd), tdd W45 has 2 (even).
PLAN = [(W44, "tdd", 100), (W44, "tdd", 300), (W44, "tdd", 200),
        (W44, "standup", 50),
        (W45, "tdd", 100), (W45, "tdd", 400)]


def fixture_records(plan=PLAN):
    recs = []
    for n, (day, skill, out) in enumerate(plan):
        recs += run(day, skill, out, n)
    return recs


def write_root(tmp_path, plan=PLAN):
    proj = tmp_path / "projects" / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "s1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in fixture_records(plan)) + "\n")
    return str(tmp_path / "projects")


@pytest.fixture
def root(tmp_path):
    return write_root(tmp_path)


# ------------------------------------------------ characterization (part 1)

def test_latency_view_output_is_unchanged(mod, root, config_path, capsys):
    mod.latency_report({"t": root}, config_path)
    assert capsys.readouterr().out == GOLDEN_LATENCY


def test_default_report_output_is_unchanged(mod, root, capsys):
    S = mod.new_stats()
    mod.scan_root(root, S)
    mod.report("PROFILE: T", S, detailed=True)
    assert capsys.readouterr().out == GOLDEN_REPORT


def test_weekly_report_keeps_existing_sections_in_order():
    text = WEEKLY.read_text()
    pos = [text.index(f'echo "{h}"')
           for h in ("WEEKLY TREND", "TIER ADHERENCE", "SKILL LATENCY")]
    assert pos == sorted(pos)
    for flag in ("--digest", "--trend", "--adherence", "--latency --min-n 3"):
        assert flag in text


# ------------------------------------------------ token trend (part 2)

def test_median_out_tokens_odd_and_even_counts(mod, root, config_path):
    trend = mod.compute_token_trend({"t": root}, config_path)
    # tdd W44: runs 100, 300, 200 (odd) -> middle value.
    assert trend["tdd"]["2026-W44"] == 200
    # tdd W45: runs 100, 400 (even) -> mean of the two middle values.
    assert trend["tdd"]["2026-W45"] == 250
    assert trend["standup"] == {"2026-W44": 50}


def test_median_ignores_duplicate_blocks_of_one_message(mod, tmp_path, config_path):
    # run() writes every assistant message twice; counting both would double.
    root = write_root(tmp_path, [(W44, "tdd", 100)])
    assert mod.compute_token_trend({"t": root}, config_path) == {
        "tdd": {"2026-W44": 100}}


def test_median_by_skill_week_from_span_data(mod):
    spans = {"a": [{"week": "2026-W44", "out": 10}, {"week": "2026-W44", "out": 30},
                   {"week": "2026-W44", "out": 20}, {"week": "2026-W45", "out": 5},
                   {"week": "2026-W45", "out": 6}],
             "b": [{"week": None, "out": 99}]}
    assert mod.median_out_by_skill_week(spans) == {
        "a": {"2026-W44": 20, "2026-W45": 5.5}}


def test_median_skips_spans_without_usage_data(mod):
    spans = {"a": [{"week": "2026-W44", "out": 0}, {"week": "2026-W44", "out": 10},
                   {"week": "2026-W44", "out": 30}],
             "b": [{"week": "2026-W44", "out": 0}]}
    assert mod.median_out_by_skill_week(spans) == {"a": {"2026-W44": 20}}


def test_token_trend_leaves_out_the_current_partial_week(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "current_iso_week", lambda: "2026-W45")
    mod.print_token_trend({"a": {"2026-W44": 20, "2026-W45": 5}})
    out = capsys.readouterr().out
    assert "2026-W44" in out
    assert "2026-W45" not in out


def test_token_trend_with_only_the_current_week_has_nothing_to_show(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "current_iso_week", lambda: "2026-W45")
    mod.print_token_trend({"a": {"2026-W45": 5}})
    assert "no complete weeks yet" in capsys.readouterr().out


def test_token_trend_view_output(mod, root, config_path, capsys):
    mod.print_token_trend(mod.compute_token_trend({"t": root}, config_path))
    assert capsys.readouterr().out == GOLDEN_TOKENS


def test_token_trend_view_with_no_data(mod, capsys):
    mod.print_token_trend({})
    assert "no skill invocations found" in capsys.readouterr().out


def test_cli_tokens_flag_prints_the_trend(mod, root, config_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["cc-usage-benchmark.py", "--tokens",
                                     "--roots", root, "--config", config_path])
    mod.main()
    assert capsys.readouterr().out == GOLDEN_TOKENS


ALARM_WORDS = ("DRIFT", "ALERT", "ALARM", "WARN", "act)", "->")


def test_token_trend_has_no_alarm_vocabulary(mod, root, config_path, capsys):
    mod.print_token_trend(mod.compute_token_trend({"t": root}, config_path))
    out = capsys.readouterr().out
    assert not any(w.lower() in out.lower() for w in ALARM_WORDS)


def test_trend_never_enters_digest_alert_output(mod, monkeypatch, capsys):
    # Even a huge week-over-week jump must not reach the digest, and the digest
    # must not compute the trend at all.
    def boom(*a, **k):
        raise AssertionError("digest touched the token trend")
    monkeypatch.setattr(mod, "compute_token_trend", boom)
    monkeypatch.setattr(mod, "print_token_trend", boom)
    weeks = {"2026-W44": {"turns": {"opus": 1, "sonnet": 1, "haiku": 1},
                          "tok": {"sonnet": {"in": 1, "out": 1, "cache_w": 0, "cache_r": 0}}},
             "2026-W45": {"turns": {"opus": 1, "sonnet": 1, "haiku": 1},
                          "tok": {"sonnet": {"in": 1, "out": 1, "cache_w": 0, "cache_r": 0}}}}
    monkeypatch.setattr(mod, "compute_trend", lambda roots: weeks)
    monkeypatch.setattr(mod, "compute_adherence", lambda roots, cfg: (
        {"tdd": "sonnet"}, {"2026-W45": {"tdd": __import__("collections").Counter(opus=3)}}))
    mod.digest_report({}, "ignored.json")
    out = capsys.readouterr().out
    assert "DRIFT" in out          # the alert section still renders
    assert "OUTPUT TOKENS" not in out


def test_weekly_report_adds_tokens_section_but_not_to_digest():
    text = WEEKLY.read_text()
    assert text.index('echo "SKILL OUTPUT TOKENS"') > text.index('echo "SKILL LATENCY"')
    assert '"$SCRIPT" --tokens' in text
    digest_line = next(l for l in text.splitlines() if "DIGEST_TEXT=" in l)
    assert "--tokens" not in digest_line


def test_weekly_report_marks_a_failed_token_trend():
    assert '--tokens || echo "(token trend failed; continuing)"' in WEEKLY.read_text()


GOLDEN_LATENCY = (
    '==============================================================================\n'
    'SKILL LATENCY  (work_s drops human idle; each gap capped at 300s)\n'
    '==============================================================================\n'
    '  skill                         n   med_s   p90_s  turns  tools  agents  out_tok\n'
    '  standup                       1     311     311      1      0       0       50\n'
    '  tdd                           5      60      60      1      0       0      200\n'
    '\n'
)
GOLDEN_REPORT = (
    '================================================================\n'
    'PROFILE: T\n'
    '================================================================\n'
    'Date range : 2026-10-27 -> 2026-11-03\n'
    'Sessions   : 1\n'
    'Human reqs : 6  (work=6, continuation/ack=0)\n'
    'Noise dropped: 0  (/exit, local-command-caveat)\n'
    'Asst turns : 12\n'
    'Reqs/session: 6.0\n'
    '\n'
    '--- PROJECTS (sessions) ---\n'
    '     1  proj\n'
    '\n'
    '--- MODEL MIX (assistant turns) ---\n'
    '  100.0%  sonnet     12\n'
    '\n'
    '--- TOKENS + ROUGH COST (USD) ---\n'
    '  sonnet   in=         0 out=    2,300 cacheW=          0 cacheR=           0  $0.03\n'
    '  TOTAL    ~ $0.03\n'
    '\n'
    '--- DEEP THINKING ---\n'
    '    0.0% of assistant turns had a thinking block (0)\n'
    '\n'
    '--- TOP TOOLS ---\n'
    '\n'
    '--- SLASH COMMANDS / SKILLS ---\n'
    '     5  /tdd\n'
    '     1  /standup\n'
    '\n'
    '--- GIT BRANCH WORK-TYPE (by prompt) ---\n'
    '  100.0%  none               6\n'
    '\n'
    '--- REQUEST INTENT (primary bucket, work requests only) ---\n'
    '   83.3%  testing        5\n'
    '   16.7%  research       1\n'
    '\n'
    '--- REQUEST INTENT (all labels, multi-count) ---\n'
    '   83.3%  testing        5\n'
    '   16.7%  research       1\n'
    '\n'
)
GOLDEN_TOKENS = (
    '==============================================================================\n'
    'SKILL OUTPUT TOKENS  (median per run, by ISO week; informational only)\n'
    '==============================================================================\n'
    '  skill                       2026-W44  2026-W45\n'
    '  standup                           50         -\n'
    '  tdd                              200       250\n'
    '\n'
)


def _count_scans(mod, monkeypatch):
    scans = []
    real = mod._scan_latency
    monkeypatch.setattr(mod, "_scan_latency",
                        lambda *a, **k: scans.append(1) or real(*a, **k))
    return scans


def test_latency_then_tokens_scan_transcripts_once(mod, root, config_path, tmp_path,
                                                   capsys, monkeypatch):
    """Separate --latency and --tokens processes share one scan via the spans cache."""
    monkeypatch.setenv("CC_USAGE_SPANS_CACHE", str(tmp_path / "spans.pkl"))
    scans = _count_scans(mod, monkeypatch)
    argv = ["cc-usage-benchmark.py", "--roots", root, "--config", config_path]
    monkeypatch.setattr("sys.argv", argv + ["--latency"])
    mod.main()
    latency_out = capsys.readouterr().out
    monkeypatch.setattr("sys.argv", argv + ["--tokens"])
    mod.main()
    tokens_out = capsys.readouterr().out
    assert len(scans) == 1
    assert tokens_out == GOLDEN_TOKENS
    assert latency_out == GOLDEN_LATENCY


def test_spans_cache_ignored_when_inputs_differ(mod, root, config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("CC_USAGE_SPANS_CACHE", str(tmp_path / "spans.pkl"))
    scans = _count_scans(mod, monkeypatch)
    mod.compute_latency({"a": root}, config_path)
    mod.compute_latency({"a": root}, config_path, idle_cap=60)
    assert len(scans) == 2


def test_weekly_report_shares_one_span_scan():
    assert "CC_USAGE_SPANS_CACHE" in WEEKLY.read_text()
