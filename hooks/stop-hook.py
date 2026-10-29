#!/usr/bin/env python3
"""stop-hook.py — learning-eval safety net.

Fires on the Stop event. Reads the last assistant turn from the transcript and
checks for skill-done/learning-eval HTML-comment markers.

No skill-done marker: tells the cmux skill row the skill is waiting on the user.

Outcomes per detected skill completion (skill-done present):
  - ran:    both markers present → log it, exit silently
  - nudged: skill-done present, learning-eval absent, not yet nudged this session
            → log it, output a continue:true JSON nudge

Per-session state stored at HOOK_SESSION_DIR/cc-nudged-<session_id>.json (or
/tmp/cc-nudged-<session_id>.json by default) to prevent double-nudges.

Audit log appended to HOOK_AUDIT_DIR/hook-audit.jsonl (default:
~/.dotfiles/claude-code-shared/learnings/audit/hook-audit.jsonl).

Environment overrides (for testing):
  HOOK_AUDIT_DIR    — override the audit log directory
  HOOK_SESSION_DIR  — override the per-session state directory (default /tmp)
"""

import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

SHARED = pathlib.Path(__file__).resolve().parents[1]
_audit_dir_override = os.environ.get("HOOK_AUDIT_DIR")
_session_dir_override = os.environ.get("HOOK_SESSION_DIR")

AUDIT_DIR = pathlib.Path(_audit_dir_override) if _audit_dir_override else SHARED / "learnings" / "audit"
AUDIT_LOG = AUDIT_DIR / "hook-audit.jsonl"
SESSION_DIR = pathlib.Path(_session_dir_override) if _session_dir_override else pathlib.Path("/tmp")

SKILL_DONE_RE = re.compile(r"<!-- skill-done:\s*([^\s>]+)\s*-->")
LEARNING_EVAL_RE = re.compile(r"<!-- learning-eval:\s*([^\s>]+)\s*-->")

SKILL_STATUS_SH = SHARED / "scripts" / "cmux" / "skill-status.sh"
_MARKER_LINE_RE = re.compile(
    r"(?:<!--\s*(?:skill-done|learning-eval):\s*[^\s>]+\s*-->\s*){1,2}"
)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def marker_lines(text):
    """Yield lines that consist solely of markers, outside fences and backticks."""
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or "`" in line:
            continue
        if _MARKER_LINE_RE.fullmatch(line.strip()):
            yield line


def find_markers(text):
    """Return (done_slugs, eval_slugs) from real marker lines only."""
    block = "\n".join(marker_lines(text))
    return SKILL_DONE_RE.findall(block), set(LEARNING_EVAL_RE.findall(block))


def set_status(*args):
    try:
        subprocess.run(
            [str(SKILL_STATUS_SH), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        pass


def get_session_id(data):
    return (
        os.environ.get("CLAUDE_SESSION_ID")
        or data.get("session_id")
        or "default"
    )


def get_state_path(session_id):
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return SESSION_DIR / f"cc-nudged-{safe_id}.json"


def load_nudged(session_id):
    path = get_state_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_nudged(session_id, state):
    path = get_state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def get_last_assistant_text(transcript_path):
    """Extract the text of the final assistant turn from transcript JSONL.

    Claude Code writes each content block of a message as its own line, with
    the payload nested under "message". Collect text from the trailing run of
    assistant lines, stopping at the previous user line.
    """
    try:
        lines = pathlib.Path(transcript_path).read_text().strip().splitlines()
    except Exception:
        return ""

    parts = []
    for line in reversed(lines):
        try:
            turn = json.loads(line)
        except Exception:
            continue
        msg = turn.get("message") if isinstance(turn.get("message"), dict) else turn
        role = msg.get("role") or turn.get("type")
        if role == "assistant":
            parts.append(_text_of(msg.get("content", "")))
        elif role == "user" and parts:
            break
    return "\n".join(reversed(parts))


_NOTIFY_STATUS_RE = re.compile(r"<status>\w+</status>")
_NOTIFY_ID_RE = re.compile(r"<(?:tool-use-id|task-id)>(\S+?)</(?:tool-use-id|task-id)>")
_AGENT_ID_RE = re.compile(r"agentId: (\S+)")


def _finished_ids(text):
    """tool-use and task ids from a <task-notification> that carries a final status."""
    if "<task-notification>" not in text or not _NOTIFY_STATUS_RE.search(text):
        return set()
    return set(_NOTIFY_ID_RE.findall(text))


def has_pending_background(transcript_path):
    """True if a background agent launched this session has not reported back yet.

    Mirrors Claude Code's own pendingBackgroundAgentCount, which it only writes
    after Stop hooks run. Launches are async Agent results ("Async agent launched
    successfully", with an agentId). An agent counts as finished once Claude has
    consumed its <task-notification>: popped by a queue dequeue, dropped by a
    queue remove, or delivered as a user row. A notification still sitting in the
    queue means Claude is about to resume, so the agent still counts as pending.
    """
    try:
        lines = pathlib.Path(transcript_path).read_text().splitlines()
    except Exception:
        return False

    launched = []  # (tool_use_id, agent_id)
    finished, queue = set(), []
    for line in lines:
        if '"queue-operation"' not in line and "Async agent launched" not in line \
                and "<task-notification>" not in line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("type") == "queue-operation":
            op, content = row.get("operation"), str(row.get("content") or "")
            if op == "enqueue":
                queue.append(content)
            elif op == "dequeue" and queue:
                finished |= _finished_ids(queue.pop(0))
            elif op == "remove":
                finished |= _finished_ids(content)
                if content in queue:
                    queue.remove(content)
            continue
        msg = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, str):
            if content.lstrip().startswith("<task-notification>"):
                finished |= _finished_ids(content)
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                text = _text_of(block.get("content", ""))
                if text.startswith("Async agent launched"):
                    agent = _AGENT_ID_RE.search(text)
                    launched.append((block.get("tool_use_id"), agent.group(1) if agent else None))
            elif block.get("type") == "text" and block.get("text", "").lstrip().startswith("<task-notification>"):
                finished |= _finished_ids(block["text"])
    return any(tid not in finished and aid not in finished for tid, aid in launched)


def append_audit(ts, skill, outcome):
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": ts, "skill": skill, "outcome": outcome}, ensure_ascii=False) + "\n"
    with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def main():
    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}

    transcript_path = data.get("transcript_path", "")
    session_id = get_session_id(data)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    last_text = get_last_assistant_text(transcript_path) if transcript_path else ""

    done_slugs, eval_slugs = find_markers(last_text)

    if not done_slugs:
        # Turn ended without a skill-done marker: a running skill is waiting on the
        # user, unless the turn only paused for background agents to report back.
        if not (transcript_path and has_pending_background(transcript_path)):
            set_status("wait")
        sys.exit(0)

    nudged_state = load_nudged(session_id)
    nudge_targets = []

    for slug in done_slugs:
        set_status("done", slug)
        if slug in eval_slugs:
            append_audit(ts, slug, "ran")
        elif nudged_state.get(slug):
            # Already nudged this session — record as ran to avoid re-nudging
            append_audit(ts, slug, "ran")
        else:
            append_audit(ts, slug, "nudged")
            nudge_targets.append(slug)
            nudged_state[slug] = True

    if nudge_targets:
        save_nudged(session_id, nudged_state)
        slugs_str = ", ".join(nudge_targets)
        print(json.dumps({
            "continue": True,
            "decision": "block",
            "reason": (
                f"The learning-eval tail was skipped for skill(s): {slugs_str}. "
                f"Please run the learning capture tail now: read and execute "
                f"~/.dotfiles/claude-code-shared/resources/learning-capture.md "
                f"for each skipped slug."
            ),
        }))

    sys.exit(0)


if __name__ == "__main__":
    main()
