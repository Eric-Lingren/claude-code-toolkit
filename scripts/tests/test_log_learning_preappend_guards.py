"""log-learning.py: failures before the append never lose the learning."""

import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import types

import pytest

SHARED = pathlib.Path(__file__).resolve().parents[2]
LOG_LEARNING = SHARED / "scripts" / "learning" / "log-learning.py"

LEARNING = {
    "type": "attribution",
    "reported_by": "debug",
    "improves": "debug",
    "improves_type": "skill",
    "cause": "general_best_practice",
    "cause_label": None,
    "problem": "Skipped the repro step.",
    "why_missed": "Step was optional.",
    "lesson": "Always repro first.",
    "fix": "Make repro mandatory.",
    "evidence": [{"source": "artifact", "ref": "docs/x.json", "quote": "q"}],
    "confidence": "candidate",
    "escape_point": {"path": "skills/debug/SKILL.md", "section": "## Instrument"},
}


@pytest.fixture
def ll():
    spec = importlib.util.spec_from_file_location("log_learning_guards_mod", LOG_LEARNING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(ll, monkeypatch, tmp_path):
    out = tmp_path / "out"
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("LOG_LEARNING_DOTFILES_DIR", str(repo))
    monkeypatch.setattr(ll, "LEARNINGS_DIR", out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(LEARNING)))
    ll.main()
    return [json.loads(l) for l in (out / "unified-learnings.jsonl").read_text().splitlines()]


def test_validate_escape_point_raising_drops_it_and_appends(ll, monkeypatch, tmp_path, capsys):
    def boom(_):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(ll, "_validate_escape_point", boom)
    monkeypatch.setattr(ll, "_attribution_tag", lambda e: None)
    rows = _run(ll, monkeypatch, tmp_path)
    assert len(rows) == 1
    assert "escape_point" not in rows[0]
    err = capsys.readouterr().err
    assert "WARNING" in err and "escape_point" in err


def test_malformed_diff_return_downgrades_and_appends(ll, monkeypatch, tmp_path, capsys):
    tag = {"kind": "regression_of", "ref": "chg-1"}
    monkeypatch.setattr(ll, "_validate_escape_point", lambda p: p)
    monkeypatch.setattr(ll, "_attribution_tag", lambda e: dict(tag))

    helper = types.SimpleNamespace(load_change_diff=lambda *a, **k: {})  # no section_edited
    tagger = types.SimpleNamespace(load_changes=lambda d: [{"change_id": "chg-1"}])
    monkeypatch.setattr(ll, "_load_script",
                        lambda f, m: helper if f == "load-change-diff.py" else tagger)

    rows = _run(ll, monkeypatch, tmp_path)
    assert len(rows) == 1
    assert rows[0]["attribution_tag"] == {"kind": "new_defect", "ref": None}
    assert "WARNING: regression_of" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [["regression_of"], "regression_of", 7])
def test_non_dict_tagger_return_omits_tag_and_appends(ll, monkeypatch, tmp_path, capsys, bad):
    monkeypatch.setattr(ll, "_validate_escape_point", lambda p: p)
    monkeypatch.setattr(ll, "_attribution_tag", lambda e: bad)
    rows = _run(ll, monkeypatch, tmp_path)
    assert len(rows) == 1
    assert "attribution_tag" not in rows[0]
    assert "WARNING" in capsys.readouterr().err
