#!/usr/bin/env python3
"""Static smoke test for the shared Claude Code config. No model, no tokens.

What: three deterministic checks over <root>/claude-code-shared:
  1. settings.json parses as JSON.
  2. Every configured hook command exits 0 on stub stdin.
  3. Every skills/*/SKILL.md has valid frontmatter (opening and closing '---',
     non-empty name and description).
When: CI (smoke job beside the test job) on every PR, and locally before a push.
It replaces a headless `claude -p` boot as the gate's smoke layer.

Usage: smoke-test.py [root]   (root defaults to the repo containing this script)

Every failure prints one line: `FAIL: <check>: <path>: <detail>`. All checks run
even after a failure. Exit 0 when clean, 1 when anything failed.

Hook commands reference $HOME/.claude-code-shared. They run with HOME pointed at
a throwaway dir whose .claude-code-shared symlinks to <root>/claude-code-shared,
so the commands run verbatim against the tree under test and never touch the
real home dir.
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

HOOK_TIMEOUT_SECONDS = 30


def default_root():
    return pathlib.Path(__file__).resolve().parents[3]


def fail(check, path, detail):
    print(f"FAIL: {check}: {path}: {detail}")
    return 1


def check_settings(settings_path):
    """Return (failures, parsed settings or None)."""
    try:
        text = settings_path.read_text()
    except OSError as exc:
        return fail("settings", settings_path, f"cannot read: {exc}"), None
    try:
        return 0, json.loads(text)
    except json.JSONDecodeError as exc:
        return fail("settings", settings_path, f"invalid JSON: {exc}"), None


def iter_hook_commands(settings):
    """Yield (event, matcher, command) for every command hook."""
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            matcher = group.get("matcher", "") if isinstance(group, dict) else ""
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                if isinstance(hook, dict) and hook.get("type", "command") == "command" \
                        and isinstance(hook.get("command"), str):
                    yield event, matcher, hook["command"]


def stub_payload(event, matcher, cwd):
    tool = re.split(r"[|().*]+", matcher)[0] if matcher else ""
    tool = tool if re.fullmatch(r"[A-Za-z_]+", tool or "") else "Bash"
    return json.dumps({
        "hook_event_name": event,
        "session_id": "smoke-test",
        "transcript_path": "",
        "cwd": cwd,
        "tool_name": tool,
        "tool_input": {"command": "echo smoke", "file_path": "smoke.txt"},
        "prompt": "smoke test",
    })


def check_hooks(settings, shared_dir, settings_path):
    failures = 0
    seen = set()
    with tempfile.TemporaryDirectory(prefix="smoke-home-") as home:
        os.symlink(shared_dir, os.path.join(home, ".claude-code-shared"))
        work = os.path.join(home, "work")
        os.mkdir(work)
        env = dict(os.environ, HOME=home, CAPTURE_IDEA_DISABLE="1")
        for event, matcher, command in iter_hook_commands(settings):
            key = (event, matcher, command)
            if key in seen:
                continue
            seen.add(key)
            where = f"{settings_path} [{event}]"
            try:
                proc = subprocess.run(
                    ["bash", "-c", command], input=stub_payload(event, matcher, work),
                    capture_output=True, text=True, env=env, cwd=work,
                    timeout=HOOK_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                failures += fail("hook", where, f"timed out after {HOOK_TIMEOUT_SECONDS}s: {command}")
                continue
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
                failures += fail("hook", where,
                                 f"exit {proc.returncode}: {command} {tail[0]}".rstrip())
    return failures


def frontmatter_problem(text):
    """Return a problem string, or None when the frontmatter is valid."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "missing frontmatter (file must start with ---)"
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), None)
    if end is None:
        return "unclosed frontmatter (no closing ---)"
    keys = {}
    for ln in lines[1:end]:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    for required in ("name", "description"):
        if required not in keys:
            return f"frontmatter missing {required}"
        if not keys[required]:
            # block scalars (> or |) put the value on following lines
            idx = next(i for i, ln in enumerate(lines[1:end], 1)
                       if ln.startswith(required + ":"))
            nxt = lines[idx + 1] if idx + 1 < end else ""
            if not nxt.startswith((" ", "\t")):
                return f"frontmatter {required} is empty"
    return None


def _git_ignored(path):
    """True when git reports the path as ignored (e.g. skills/synced). False outside a repo."""
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=str(path.parent),
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def check_skills(skills_dir):
    failures = 0
    if not skills_dir.is_dir():
        return fail("skill", skills_dir, "skills directory not found")
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or _git_ignored(entry):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            failures += fail("skill", entry, "no SKILL.md in skill directory")
            continue
        try:
            problem = frontmatter_problem(skill_md.read_text())
        except (OSError, UnicodeDecodeError) as exc:
            problem = f"cannot read: {exc}"
        if problem:
            failures += fail("skill", skill_md, problem)
    return failures


def main(argv):
    root = pathlib.Path(argv[1]).resolve() if len(argv) > 1 else default_root()
    shared = root / "claude-code-shared"
    settings_path = shared / "settings.json"

    failures, settings = check_settings(settings_path)
    if settings is not None:
        failures += check_hooks(settings, shared, settings_path)
    failures += check_skills(shared / "skills")

    if failures:
        print(f"smoke-test: {failures} failure(s)")
        return 1
    print("smoke-test: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
