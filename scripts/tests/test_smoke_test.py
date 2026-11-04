"""Tests for the static smoke test (shared/smoke-test.py).

The script is deterministic: no model, no tokens. Each test builds a fixture
tree (<root>/claude-code-shared/{settings.json,hooks,skills}) and runs the CLI.
"""

import json
import pathlib
import subprocess
import sys

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "shared" / "smoke-test.py")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

GOOD_SKILL = "---\nname: demo\ndescription: A demo skill.\n---\n\nBody.\n"
OK_HOOK = "#!/bin/bash\ncat >/dev/null\nexit 0\n"


def run(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)],
                          capture_output=True, text=True, timeout=120)


def hook_cmd(name):
    return f'bash "$HOME/.claude-code-shared/hooks/{name}"'


def make_tree(tmp_path, hooks=None, skills=None, settings=None):
    shared = tmp_path / "claude-code-shared"
    (shared / "hooks").mkdir(parents=True)
    (shared / "skills").mkdir()
    hooks = {"ok.sh": OK_HOOK} if hooks is None else hooks
    for name, body in hooks.items():
        (shared / "hooks" / name).write_text(body)
    skills = {"demo": GOOD_SKILL} if skills is None else skills
    for name, body in skills.items():
        (shared / "skills" / name).mkdir()
        (shared / "skills" / name / "SKILL.md").write_text(body)
    if settings is None:
        settings = json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": hook_cmd(n)} for n in hooks]}]}})
    (shared / "settings.json").write_text(settings)
    return tmp_path


def test_clean_tree_passes(tmp_path):
    r = run(make_tree(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL" not in r.stdout


def test_broken_settings_json_fails_with_path(tmp_path):
    root = make_tree(tmp_path, settings="{not json")
    r = run(root)
    assert r.returncode == 1
    assert "FAIL" in r.stdout
    assert "settings.json" in r.stdout
    assert str(root / "claude-code-shared" / "settings.json") in r.stdout


def test_hook_nonzero_on_stub_stdin_fails_with_path(tmp_path):
    root = make_tree(tmp_path, hooks={"bad.sh": "#!/bin/bash\nexit 3\n"})
    r = run(root)
    assert r.returncode == 1
    assert "FAIL" in r.stdout
    assert "bad.sh" in r.stdout


def test_missing_hook_script_fails(tmp_path):
    root = make_tree(tmp_path, hooks={"ok.sh": OK_HOOK})
    settings = json.loads((root / "claude-code-shared" / "settings.json").read_text())
    settings["hooks"]["Stop"][0]["hooks"].append(
        {"type": "command", "command": hook_cmd("gone.sh")})
    (root / "claude-code-shared" / "settings.json").write_text(json.dumps(settings))
    r = run(root)
    assert r.returncode == 1
    assert "gone.sh" in r.stdout


def test_hook_receives_stub_json_on_stdin(tmp_path):
    body = ('#!/bin/bash\ninput="$(cat)"\n'
            'echo "$input" | python3 -c "import json,sys; json.load(sys.stdin)" || exit 4\n'
            '[ -n "$input" ] || exit 5\nexit 0\n')
    r = run(make_tree(tmp_path, hooks={"json.sh": body}))
    assert r.returncode == 0, r.stdout + r.stderr


def test_skill_missing_frontmatter_fails_with_path(tmp_path):
    root = make_tree(tmp_path, skills={"demo": GOOD_SKILL, "nofm": "# no frontmatter\n"})
    r = run(root)
    assert r.returncode == 1
    assert str(root / "claude-code-shared" / "skills" / "nofm" / "SKILL.md") in r.stdout


def test_skill_frontmatter_missing_description_fails(tmp_path):
    root = make_tree(tmp_path, skills={"demo": "---\nname: demo\n---\nBody\n"})
    r = run(root)
    assert r.returncode == 1
    assert "description" in r.stdout


def test_skill_unclosed_frontmatter_fails(tmp_path):
    root = make_tree(tmp_path, skills={"demo": "---\nname: demo\ndescription: x\nBody\n"})
    r = run(root)
    assert r.returncode == 1
    assert "SKILL.md" in r.stdout


def test_skill_dir_without_skill_md_fails(tmp_path):
    root = make_tree(tmp_path)
    (root / "claude-code-shared" / "skills" / "empty").mkdir()
    r = run(root)
    assert r.returncode == 1
    assert "empty" in r.stdout


def test_all_failures_reported_not_just_first(tmp_path):
    root = make_tree(tmp_path, hooks={"bad.sh": "#!/bin/bash\nexit 1\n"},
                     skills={"nofm": "nothing\n"})
    r = run(root)
    assert r.returncode == 1
    assert "bad.sh" in r.stdout and "nofm" in r.stdout


def test_current_repo_passes():
    r = run(REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_hidden_skill_dirs_are_not_skills(tmp_path):
    (tmp_path / "skills" / ".trash").mkdir(parents=True)
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    sys.path.insert(0, str(SCRIPT.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("smoke_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.check_skills(tmp_path / "skills") == 0
