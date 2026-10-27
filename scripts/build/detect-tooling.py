#!/usr/bin/env python3
"""
detect-tooling.py: Scan a repo and emit a JSON tooling manifest.

Usage: python detect-tooling.py <repo_root>

Output: JSON array, one entry per detected workspace, each with resolved
lint / format / typecheck / test / e2e commands (or null when absent).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "out", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------

def detect_workspaces(root: Path) -> list[Path]:
    """Return all workspace paths within root (including root itself)."""
    candidates = []

    root_has_marker = (root / "package.json").exists() or (root / "pyproject.toml").exists()
    if root_has_marker:
        candidates.append(root)

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _SKIP_DIRS or child.name.startswith("."):
            continue
        if (child / "package.json").exists() or (child / "pyproject.toml").exists():
            candidates.append(child)

    # Always return at least root so callers always get a manifest entry.
    if not candidates:
        candidates.append(root)

    return candidates


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(ws: Path) -> str:
    has_js = (ws / "package.json").exists()
    has_py = (
        (ws / "pyproject.toml").exists()
        or (ws / "setup.py").exists()
        or (ws / "requirements.txt").exists()
    )
    if has_js and has_py:
        return "js+py"
    if has_js:
        return "js"
    if has_py:
        return "py"
    return "unknown"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_pkg(ws: Path) -> dict:
    p = ws / "package.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _read_pyproject_text(ws: Path) -> str:
    p = ws / "pyproject.toml"
    return p.read_text() if p.exists() else ""


def _has_dep(pkg: dict, name: str) -> bool:
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        if name in pkg.get(key, {}):
            return True
    return False


def _has_file(ws: Path, *names: str) -> bool:
    return any((ws / n).exists() for n in names)


# ---------------------------------------------------------------------------
# Command resolvers
# ---------------------------------------------------------------------------

def resolve_lint(ws: Path, pkg: dict, lang: str) -> str | None:
    scripts = pkg.get("scripts", {})

    if "lint" in scripts:
        cmd = scripts["lint"]
        if "eslint" in cmd and "-f json" not in cmd and "--format json" not in cmd:
            return cmd.rstrip() + " -f json"
        return cmd

    has_biome = _has_file(ws, "biome.json", "biome.jsonc")
    has_eslint = _has_file(
        ws,
        "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
        ".eslintrc", ".eslintrc.js", ".eslintrc.json",
        ".eslintrc.yaml", ".eslintrc.yml",
    ) or _has_dep(pkg, "eslint")

    if has_biome:
        return "biome check --reporter=json ."
    if has_eslint:
        return "eslint . -f json"

    if lang in ("py", "js+py"):
        pp = _read_pyproject_text(ws)
        if "[tool.ruff]" in pp or _has_file(ws, "ruff.toml"):
            return "ruff check --output-format=json ."

    return None


def resolve_format(ws: Path, pkg: dict, lang: str) -> str | None:
    scripts = pkg.get("scripts", {})

    if "format" in scripts:
        return scripts["format"]

    has_biome = _has_file(ws, "biome.json", "biome.jsonc")
    has_prettier = _has_file(
        ws,
        ".prettierrc", ".prettierrc.js", ".prettierrc.json",
        ".prettierrc.yaml", ".prettierrc.yml", "prettier.config.js",
        "prettier.config.mjs",
    ) or _has_dep(pkg, "prettier")

    if has_biome:
        return "biome format --reporter=json ."
    if has_prettier:
        return "prettier --check ."

    if lang in ("py", "js+py"):
        pp = _read_pyproject_text(ws)
        if "[tool.ruff]" in pp or _has_file(ws, "ruff.toml"):
            return "ruff format --check ."

    return None


def resolve_typecheck(ws: Path, pkg: dict, lang: str) -> str | None:
    scripts = pkg.get("scripts", {})

    for key in ("typecheck", "type-check", "tsc", "type:check"):
        if key in scripts:
            return scripts[key]

    if _has_file(ws, "tsconfig.json") or _has_dep(pkg, "typescript"):
        return "tsc --noEmit"

    if lang in ("py", "js+py"):
        pp = _read_pyproject_text(ws)
        if "[tool.mypy]" in pp or _has_file(ws, "mypy.ini", ".mypy.ini"):
            return "mypy ."

    return None


def resolve_test(ws: Path, pkg: dict, lang: str) -> str | None:
    scripts = pkg.get("scripts", {})

    if "test" in scripts:
        cmd = scripts["test"]
        if "vitest" in cmd and "--reporter=json" not in cmd:
            return cmd.rstrip() + " --reporter=json"
        if "jest" in cmd and "--json" not in cmd:
            return cmd.rstrip() + " --json"
        return cmd

    has_vitest = _has_dep(pkg, "vitest") or _has_file(ws, "vitest.config.ts", "vitest.config.js", "vitest.config.mjs")
    has_jest = _has_dep(pkg, "jest") or _has_file(ws, "jest.config.js", "jest.config.ts", "jest.config.mjs")

    if has_vitest:
        return "vitest run --reporter=json"
    if has_jest:
        return "jest --json"

    if lang in ("py", "js+py"):
        pp = _read_pyproject_text(ws)
        if (
            "[tool.pytest" in pp
            or _has_file(ws, "pytest.ini", "conftest.py", "setup.cfg")
            or _has_dep(pkg, "pytest")
        ):
            return "pytest --tb=short -q"

    return None


def resolve_test_affected(ws: Path, pkg: dict, lang: str) -> str | None:
    """Return a test command template for running only affected tests.

    The {files} placeholder is substituted by the caller with a space-separated
    list of touched source files. --maxWorkers=75% is baked into every non-null
    template to cap CPU usage on local runs.

    Returns None when the framework has no native affected-test selection
    (e.g. pytest), signalling the caller to fall back to a capped full suite.
    """
    scripts = pkg.get("scripts", {})

    has_vitest = (
        _has_dep(pkg, "vitest")
        or _has_file(ws, "vitest.config.ts", "vitest.config.js", "vitest.config.mjs")
    )
    has_jest = (
        _has_dep(pkg, "jest")
        or _has_file(ws, "jest.config.js", "jest.config.ts", "jest.config.mjs")
    )

    if "test" in scripts:
        cmd = scripts["test"]
        if "vitest" in cmd:
            return "vitest related {files} --reporter=json --maxWorkers=75%"
        if "jest" in cmd:
            return "jest --findRelatedTests {files} --json --maxWorkers=75%"
    elif has_vitest:
        return "vitest related {files} --reporter=json --maxWorkers=75%"
    elif has_jest:
        return "jest --findRelatedTests {files} --json --maxWorkers=75%"

    if lang in ("py", "js+py"):
        pp = _read_pyproject_text(ws)
        if (
            "[tool.pytest" in pp
            or _has_file(ws, "pytest.ini", "conftest.py", "setup.cfg")
            or _has_dep(pkg, "pytest")
        ):
            # pytest has no native affected-test selection; caller falls back to
            # a capped full suite.
            return None

    return None


def resolve_e2e(ws: Path, pkg: dict, lang: str) -> str | None:
    scripts = pkg.get("scripts", {})

    for key in ("e2e", "test:e2e", "test-e2e"):
        if key in scripts:
            return scripts[key]

    has_pw = _has_file(
        ws, "playwright.config.ts", "playwright.config.js", "playwright.config.mjs"
    ) or _has_dep(pkg, "@playwright/test")

    if has_pw:
        return "playwright test --reporter=json"

    return None


# ---------------------------------------------------------------------------
# Manifest assembly
# ---------------------------------------------------------------------------

def analyze_workspace(ws: Path, root: Path) -> dict:
    pkg = _read_pkg(ws)
    lang = detect_language(ws)
    rel = str(ws.relative_to(root)) if ws != root else "."

    return {
        "workspace": rel,
        "language": lang,
        "lint": resolve_lint(ws, pkg, lang),
        "format": resolve_format(ws, pkg, lang),
        "typecheck": resolve_typecheck(ws, pkg, lang),
        "test": resolve_test(ws, pkg, lang),
        "test_affected": resolve_test_affected(ws, pkg, lang),
        "e2e": resolve_e2e(ws, pkg, lang),
    }


def scan(root: Path) -> list[dict]:
    """Public API: scan root, return manifest as list of dicts."""
    workspaces = detect_workspaces(root)
    return [analyze_workspace(ws, root) for ws in workspaces]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: detect-tooling.py <repo_root>", file=sys.stderr)
        sys.exit(1)

    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(scan(root), indent=2))


if __name__ == "__main__":
    main()
