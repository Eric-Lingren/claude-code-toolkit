#!/usr/bin/env python3
"""Build each account's Claude Code settings.json from a public base plus a private overlay.

The dotfiles repo is public, so private settings (employer details in autoMode,
personal model picks) can't live in the tracked file. This script merges:

  base     claude-code-shared/settings.json        tracked, public-safe
  overlay  local/claude-settings.json              gitignored, private

and writes the result as a real file to ~/.claude, ~/.cco and ~/.cch
settings.json. Those used to be symlinks to the tracked file, which forced
skip-worktree hacks that blocked checkout, pull and merge.

Claude Code also writes to the live files at runtime (/model, /config, auto
mode setup). Before regenerating, any change in a live file since the last
run is folded into the overlay, so nothing is lost and every account gets it.
Removals of base entries can't be captured and are reported instead.

Merge rules: objects merge by key, scalars from the overlay win, arrays are a
union. Hook groups with the same matcher merge their hook lists.

Runs from the dotfiles git hooks (post-checkout/merge/rewrite), the SessionStart
hook, a PostToolUse hook when either source file is edited, and install.sh.
Silent unless it changes something.

Usage: sync-settings.py
"""

import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
DOTFILES = Path(os.environ.get("DOTFILES", HOME / ".dotfiles"))
BASE = DOTFILES / "claude-code-shared" / "settings.json"
LOCAL = DOTFILES / "local"
OVERLAY = LOCAL / "claude-settings.json"
SNAPSHOT = LOCAL / "claude-settings.generated.json"
LOCK = LOCAL / ".claude-settings.lock"
ACCOUNTS = [".claude", ".cco", ".cch"]

MISSING = object()


def log(msg):
    print(f"sync-settings: {msg}", file=sys.stderr)


def load(path):
    with open(path) as f:
        return json.load(f)


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path, text, mode=0o644):
    """Replace path (even a symlink) with a real file holding text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def is_hook_group(x):
    return isinstance(x, dict) and "hooks" in x


def find_group(items, group):
    for i, item in enumerate(items):
        if is_hook_group(item) and item.get("matcher") == group.get("matcher"):
            return i
    return None


def merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base[k], v) if k in base else v
        return out
    if isinstance(base, list) and isinstance(over, list):
        out = list(base)
        for item in over:
            if item in out:
                continue
            i = find_group(out, item) if is_hook_group(item) else None
            if i is None:
                out.append(item)
            else:
                out[i] = merge(out[i], item)
        return out
    return over


def diff(new, old, path, removed):
    """What `new` adds or changes relative to `old`, shaped so merge(old, d) == new."""
    if isinstance(new, dict) and isinstance(old, dict):
        out = {}
        for k, v in new.items():
            if k not in old:
                out[k] = v
                continue
            d = diff(v, old[k], f"{path}.{k}", removed)
            if d is not MISSING:
                out[k] = d
        removed.extend(f"{path}.{k}" for k in old if k not in new)
        return out or MISSING
    if isinstance(new, list) and isinstance(old, list):
        out = []
        for item in new:
            if item in old:
                continue
            i = find_group(old, item) if is_hook_group(item) else None
            if i is None:
                out.append(item)
                continue
            d = diff(item, old[i], f"{path}[{item.get('matcher')}]", removed)
            if d is not MISSING:
                d.pop("matcher", None)
                group = {"matcher": item["matcher"]} if "matcher" in item else {}
                out.append({**group, **d})
        removed.extend(f"{path}: {json.dumps(x)[:80]}" for x in old if x not in new and not is_hook_group(x))
        return out or MISSING
    return MISSING if new == old else new


def capture_drift(targets, snapshot, overlay):
    """Fold runtime edits from live files into the overlay. Returns the new overlay."""
    for target in targets:
        if target.is_symlink() or not target.exists():
            continue
        try:
            live = load(target)
        except (OSError, json.JSONDecodeError) as e:
            log(f"{target} unreadable ({e}); it will be regenerated")
            continue
        if snapshot is None:
            # First run over an old real file: keep a copy, don't guess.
            backup = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d%H%M%S')}")
            target.rename(backup)
            log(f"{target} was not generated by this script; moved to {backup.name}")
            continue
        removed = []
        delta = diff(live, snapshot, "", removed)
        if delta is not MISSING:
            overlay = merge(overlay, delta)
            log(f"captured runtime edits from {target} into {OVERLAY}: {', '.join(sorted(delta))}")
        for r in removed:
            log(f"{target} removed {r.lstrip('.')}; removals aren't kept, edit {BASE} or {OVERLAY} instead")
    return overlay


def main():
    if not BASE.exists():
        log(f"base settings missing at {BASE}; nothing to do")
        return 0
    LOCAL.mkdir(parents=True, exist_ok=True)
    targets = [HOME / a / "settings.json" for a in ACCOUNTS]

    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        base = load(BASE)
        overlay = load(OVERLAY) if OVERLAY.exists() else {}
        snapshot = load(SNAPSHOT) if SNAPSHOT.exists() else None

        new_overlay = capture_drift(targets, snapshot, overlay)
        if new_overlay != overlay or not OVERLAY.exists():
            write_atomic(OVERLAY, dump(new_overlay), 0o600)

        text = dump(merge(base, new_overlay))
        for target in targets:
            current = None if target.is_symlink() or not target.exists() else target.read_text()
            if current != text:
                write_atomic(target, text, 0o600)
                log(f"wrote {target}")
        if not SNAPSHOT.exists() or SNAPSHOT.read_text() != text:
            write_atomic(SNAPSHOT, text, 0o600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
