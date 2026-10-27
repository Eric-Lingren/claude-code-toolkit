#!/usr/bin/env python3
"""Publish the shareable part of claude-code-shared to the public toolkit repo.

dotfiles (private) stays the source of truth. This script copies only the files
that publish.json allows, scans every copied file for leaks, and refuses to
publish if anything is found.

Selection is default deny: a file is published only if it matches an `include`
pattern and no `exclude` pattern. Only files git knows about (tracked, or
untracked but not ignored) are considered.

Scan checks, all case-insensitive:
  - `banned` regexes from the manifest (employer names, private orgs)
  - absolute home paths (/Users/<name>/, /home/<name>/)
  - email addresses
  - common secret shapes (GitHub, Anthropic, OpenAI, AWS, Slack, Linear,
    Notion tokens, private key blocks)
  - gitleaks over the output folder, when gitleaks is installed
A manifest `allow` entry ({"file": glob, "pattern": regex}) silences one known
false positive.

Usage:
  publish-toolkit.py --list                    # print the files that would publish
  publish-toolkit.py --out <dir>               # dry run: copy + scan into <dir>
  publish-toolkit.py --target <clone>          # copy + scan into a clone of the public repo
  publish-toolkit.py --target <clone> --commit [--push]

Exit codes: 0 clean, 1 usage or git error, 2 scan found leaks (nothing committed).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BUILTIN_CHECKS = {
    "home path": r"/(?:Users|home)/[A-Za-z0-9._-]+/",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "github token": r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    "anthropic key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai key": r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}",
    "aws key": r"\bAKIA[0-9A-Z]{16}\b",
    "slack token": r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    "linear key": r"\blin_api_[A-Za-z0-9]{20,}",
    "notion token": r"\b(?:secret_|ntn_)[A-Za-z0-9]{32,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


def matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        pattern += "*"
    return fnmatch.fnmatchcase(path, pattern)


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    for key in ("include", "exclude", "banned"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"manifest {path} needs a list for '{key}'")
    data.setdefault("allow", [])
    return data


def candidate_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return sorted(p for p in out.splitlines() if (root / p).is_file())


def select(files: list[str], manifest: dict) -> list[str]:
    return [
        f for f in files
        if any(matches(f, p) for p in manifest["include"])
        and not any(matches(f, p) for p in manifest["exclude"])
    ]


def scan_text(rel: str, text: str, manifest: dict) -> list[tuple[str, int, str, str]]:
    checks = {f"banned: {b}": b for b in manifest["banned"]} | BUILTIN_CHECKS
    compiled = {name: re.compile(rx, re.IGNORECASE) for name, rx in checks.items()}
    allows = [
        re.compile(a["pattern"], re.IGNORECASE)
        for a in manifest["allow"] if matches(rel, a["file"])
    ]
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in compiled.items():
            for m in rx.finditer(line):
                if any(a.search(m.group(0)) for a in allows):
                    continue
                hits.append((rel, n, name, m.group(0)))
    return hits


def scan(root: Path, files: list[str], manifest: dict) -> list[tuple[str, int, str, str]]:
    hits = []
    for rel in files:
        raw = (root / rel).read_bytes()
        if b"\0" in raw[:8192]:
            continue  # binary
        hits += scan_text(rel, raw.decode("utf-8", errors="replace"), manifest)
    return hits


def gitleaks(dest: Path) -> list[tuple[str, int, str, str]]:
    if not shutil.which("gitleaks"):
        print("note: gitleaks not installed, built-in secret checks only", file=sys.stderr)
        return []
    report = dest.parent / f".{dest.name}-gitleaks.json"
    subprocess.run(
        ["gitleaks", "dir", str(dest), "--no-banner", "--exit-code", "0",
         "--report-format", "json", "--report-path", str(report)],
        capture_output=True, check=True,
    )
    found = json.loads(report.read_text() or "[]")
    report.unlink(missing_ok=True)
    return [(str(Path(f["File"]).relative_to(dest)), f["StartLine"], f"gitleaks: {f['RuleID']}", "<redacted>") for f in found]


def copy_into(root: Path, files: list[str], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in dest.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)


def git(dest: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=dest, capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="claude-code-shared folder (default: this repo)")
    ap.add_argument("--manifest", type=Path, help="manifest path (default: <root>/publish.json)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="print selected files and exit")
    mode.add_argument("--out", type=Path, help="dry run: copy and scan into this folder")
    mode.add_argument("--target", type=Path, help="local clone of the public repo")
    ap.add_argument("--commit", action="store_true", help="with --target: commit when the scan is clean")
    ap.add_argument("--push", action="store_true", help="with --commit: push after committing")
    a = ap.parse_args(argv)

    if (a.commit or a.push) and not a.target:
        ap.error("--commit and --push need --target")
    if a.push and not a.commit:
        ap.error("--push needs --commit")
    if a.target and not (a.target / ".git").exists():
        ap.error(f"--target {a.target} is not a git clone")

    root = a.root.resolve()
    try:
        manifest = load_manifest(a.manifest or root / "publish.json")
        files = select(candidate_files(root), manifest)
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if a.list:
        print("\n".join(files))
        return 0

    dest = (a.out or a.target).resolve()
    if dest == root or root in dest.parents:
        ap.error("output folder must be outside the source folder")
    copy_into(root, files, dest)
    hits = scan(root, files, manifest) + gitleaks(dest)
    print(f"{len(files)} files copied to {dest}")

    if hits:
        print(f"\nBLOCKED: {len(hits)} leak(s) found. Nothing committed.\n", file=sys.stderr)
        for rel, n, name, text in hits:
            print(f"  {rel}:{n}  [{name}]  {text[:80]}", file=sys.stderr)
        return 2

    print("scan clean")
    if not a.commit:
        return 0

    git(dest, "add", "-A")
    if not git(dest, "status", "--porcelain"):
        print("no changes to publish")
        return 0
    sha = git(root, "rev-parse", "--short", "HEAD")
    git(dest, "commit", "-m", f"Sync from dotfiles @ {sha}")
    print(f"committed: Sync from dotfiles @ {sha}")
    if a.push:
        git(dest, "push")
        print("pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
