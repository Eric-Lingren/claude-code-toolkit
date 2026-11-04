"""Characterization of the interactive flow in improve-skill-learnings/SKILL.md.

These must pass before and after the headless mode is added: the human flow is unchanged.
"""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent / "skills" / "improve-skill-learnings" / "SKILL.md"
TEXT = SKILL.read_text()


def section(start, end):
    a = TEXT.index(start)
    return TEXT[a:TEXT.index(end, a)]


def test_target_picker_and_run_all_offer():
    s = section("## Step 2:", "## Step 3:")
    assert "Ask the user which target" in s
    assert "Run all (N learnings)" in s
    assert "TOTAL ≤ 10" in s
    assert "at most 4 options" in s


def test_pick_one_loop_and_target_run_all():
    s = section("## Step 5:", "## Step 6:")
    assert "Pick a learning to apply (number), or \"done\" to stop:" in s
    assert "Run all <N> for <target>" in s
    assert "Each diff still gets its own y/n." in s


def test_per_diff_apply_prompt_still_present():
    s = section("## Step 6:", "## Step 7:")
    assert "Apply? [y/n]" in s
    assert "Wait for response." in s
    assert "Print \"Skipped.\"" in s


def test_contract_drift_ask_present():
    s = section("## Step 6:", "## Step 7:")
    assert "type drift" in s
    assert "Ask whether to extend the change to those files." in s


def test_interactive_marks_learnings_via_update_learning():
    assert "update-learning.py \\\n  --id <id> --status applied" in TEXT
    assert "--status <stale|invalid>" in TEXT
    assert "--improves-type <suggested_improves_type>" in TEXT


def test_lazy_validation_and_drafter_rules_present():
    assert "Spawn ONE Haiku validation agent" in TEXT
    assert "Always spawn the drafting agent." in TEXT
    assert "You are a learning validation agent." in TEXT
    assert "You are drafting a unified diff to apply ONE learning to a target file." in TEXT


def test_one_commit_per_learning_in_interactive_mode():
    s = section("## Step 7:", "## Step 8:")
    assert 'git commit -m "improve(<target>): apply learning <short problem summary>"' in s
    assert "Auto-loop" in s
