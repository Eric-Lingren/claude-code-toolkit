#!/usr/bin/env python3
"""baseline-cache.py — manage baseline PNG cache for browser verification.

Cache key: (spec-hash, base-sha, viewport, role-name)
Cache dir: ~/.cache/claude-browser-verify/

Usage:
  baseline-cache.py get --spec-hash <hash> --base-sha <sha> \
                        --viewport <WxH> --role <role>
  baseline-cache.py put --spec-hash <hash> --base-sha <sha> \
                        --viewport <WxH> --role <role> --run-dir <dir>

get: prints the path of each cached PNG (one per line), exits 1 on miss.
put: copies PNGs and result.json from --run-dir into the cache entry,
     prints "cache-entry: <path>" on success.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

CACHE_ROOT = Path.home() / ".cache" / "claude-browser-verify"


def cache_entry_dir(spec_hash: str, base_sha: str, viewport: str, role: str) -> Path:
    """Return the cache directory for this key (not guaranteed to exist)."""
    # Normalize viewport separators: "1440,900" or "1440 900" -> "1440x900"
    vp = viewport.replace(",", "x").replace(" ", "")
    # Use first 12 chars of SHA to keep paths short
    return CACHE_ROOT / spec_hash[:12] / base_sha[:12] / vp / role


def cmd_get(args: argparse.Namespace) -> None:
    entry = cache_entry_dir(args.spec_hash, args.base_sha, args.viewport, args.role)
    if not entry.exists():
        print(f"miss: {entry}", file=sys.stderr)
        sys.exit(1)

    pngs = sorted(entry.glob("*.png"))
    if not pngs:
        print(f"miss: no PNGs in {entry}", file=sys.stderr)
        sys.exit(1)

    for png in pngs:
        print(str(png))


def cmd_put(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"error: --run-dir does not exist or is not a directory: {run_dir}", file=sys.stderr)
        sys.exit(1)

    entry = cache_entry_dir(args.spec_hash, args.base_sha, args.viewport, args.role)
    entry.mkdir(parents=True, exist_ok=True)

    pngs = sorted(run_dir.glob("*.png"))
    if not pngs:
        print(f"warning: no PNGs found in {run_dir}", file=sys.stderr)

    for png in pngs:
        dest = entry / png.name
        shutil.copy2(png, dest)

    # Copy result.json if present
    result_json = run_dir / "result.json"
    if result_json.exists():
        shutil.copy2(result_json, entry / "result.json")

    # Write a manifest of what was cached
    cached_files = sorted(str(p.name) for p in entry.glob("*"))
    manifest = {
        "spec_hash": args.spec_hash,
        "base_sha": args.base_sha,
        "viewport": args.viewport,
        "role": args.role,
        "files": cached_files,
    }
    (entry / "cache-manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"cache-entry: {entry}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manage baseline PNG cache for browser verification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("command", choices=["get", "put"], help="Cache operation")
    p.add_argument("--spec-hash", required=True, metavar="HASH",
                   help="SHA256 prefix of the check spec JSON (12+ chars)")
    p.add_argument("--base-sha", required=True, metavar="SHA",
                   help="Git SHA of the base commit")
    p.add_argument("--viewport", required=True, metavar="WxH",
                   help="Viewport string, e.g. 1440x900")
    p.add_argument("--role", required=True, metavar="ROLE",
                   help="Auth role name, e.g. admin, user, default")
    p.add_argument("--run-dir", metavar="DIR",
                   help="Directory containing PNGs to cache (required for put)")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "get":
        cmd_get(args)
    elif args.command == "put":
        if not args.run_dir:
            parser.error("--run-dir is required for the put command")
        cmd_put(args)


if __name__ == "__main__":
    main()
