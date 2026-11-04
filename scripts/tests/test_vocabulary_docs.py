"""Vocabulary docs: gen-agents-readme output, CONTEXT.md terms, ADR shape."""

import re
import subprocess
import sys
import pathlib

SHARED = pathlib.Path(__file__).resolve().parents[2]
GEN = SHARED / "scripts" / "registration" / "gen-agents-readme.py"
ADR = SHARED / "docs" / "adr"


def test_gen_agents_readme_says_groups_and_matches_committed(tmp_path):
    out = tmp_path / "README.md"
    subprocess.run([sys.executable, str(GEN), "--out", str(out)], check=True, capture_output=True)
    text = out.read_text()
    assert re.search(r"\d+ agents across \d+ groups\.", text)
    assert "lane" not in text.lower()
    assert (SHARED / "agents" / "README.md").read_bytes() == out.read_bytes()


def _context_entry(term):
    text = (SHARED / "CONTEXT.md").read_text()
    m = re.search(rf"^- \*\*{term}\*\*:(.*?)(?=^- \*\*|^## |\Z)", text, re.S | re.M)
    assert m, f"CONTEXT.md does not define {term}"
    return m.group(1)


def test_context_defines_core_terms():
    for term in ("Seed bank", "Change record", "Attribution spine", "Lane"):
        _context_entry(term)


def test_lane_definition_says_agent_folders_are_groups():
    entry = _context_entry("Lane")
    assert "agent folders" in entry and "groups" in entry


def test_adr_0006_does_not_use_lane():
    text = next(ADR.glob("0006-*.md")).read_text()
    assert "lane" not in text.lower()


def test_adr_0007_has_same_headings_as_other_adrs():
    adr7 = next(ADR.glob("0007-*.md")).read_text()
    assert adr7.startswith("---\nstatus: accepted\n---")
    assert re.search(r"^# \S", adr7, re.M)
    known = set()
    for f in ADR.glob("000[1-6]-*.md"):
        known |= set(re.findall(r"^## .+$", f.read_text(), re.M))
    assert set(re.findall(r"^## .+$", adr7, re.M)) <= known
