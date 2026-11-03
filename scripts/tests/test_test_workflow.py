"""Guard: the CI test workflow triggers on every pull_request and runs run-all-tests.sh.

Text-based on purpose: PyYAML is not a dependency of the suite.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "test.yml"


def _text():
    return WORKFLOW.read_text()


def _top_level_block(name):
    """Return the indented lines under a top-level `name:` key."""
    lines = _text().splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(name)}:", line):
            inside = True
            rest = line.split(":", 1)[1].strip()
            if rest:
                out.append(rest)
            continue
        if inside:
            if line and not line[0].isspace() and not line.startswith("#"):
                break
            out.append(line)
    return "\n".join(out)


def test_workflow_file_exists():
    assert WORKFLOW.is_file()


def test_triggers_on_pull_request():
    assert re.search(r"^\s+pull_request:", _top_level_block("on"), re.M)


def test_pull_request_trigger_has_no_filters():
    block = _top_level_block("on")
    pr = block.split("pull_request:", 1)[1]
    for key in ("paths", "paths-ignore", "branches", "branches-ignore"):
        assert not re.search(rf"^\s+{key}:", pr, re.M), f"pull_request has {key} filter"


def test_invokes_run_all_tests():
    assert re.search(r"^\s+run:\s*(bash\s+)?\.?/?run-all-tests\.sh\s*$", _text(), re.M)


def test_permissions_read_only():
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", _text(), re.M)


def test_concurrency_cancels_superseded_runs():
    block = _top_level_block("concurrency")
    assert "group:" in block
    assert re.search(r"cancel-in-progress:\s*true", block)


def test_actions_are_pinned_to_a_version():
    uses = re.findall(r"^\s+uses:\s*(\S+)", _text(), re.M)
    assert uses
    assert "actions/checkout@v4" in uses
    for u in uses:
        assert re.search(r"@v?\d", u), f"{u} is not pinned"


def test_pytest_is_pinned():
    assert re.search(r"pytest==\d+\.\d+", _text())
