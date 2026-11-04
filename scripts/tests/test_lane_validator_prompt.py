"""Validator prompt from SKILL.md must be fully substituted by headless_run.fill."""
import importlib.util
import re
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"
spec = importlib.util.spec_from_file_location("headless_run", d / "headless_run.py")
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)


def test_validator_prompt_fully_filled():
    template = hr.prompt_from_skill("Pass this prompt to the Haiku agent")
    keys = ("id", "improves", "improves_type", "problem", "lesson", "fix")
    values = {k: "X" for k in keys}
    values["path_candidates"] = "/p/a.md"
    filled = hr.fill(template, values)
    assert "/p/a.md" in filled
    assert not re.findall(r"\{[a-z_]+(?:\s[^{}\n]*)?\}", filled)
