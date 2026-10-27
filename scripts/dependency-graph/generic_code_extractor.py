#!/usr/bin/env python3
"""generic_code_extractor.py -- generic-code edge extractor for the scanner.

This is a *scanner extractor* (see scanner.py), not the analysis core. It
walks an arbitrary code directory (JS/TS and Python at minimum) and emits a
{nodes, edges, metrics} JSON document shaped exactly per the input contract
documented at the top of dependency-graph-findings.py. It does not compute
any findings itself -- that is dependency-graph-findings.py's job.

Unlike claude_tooling_extractor.py (which is claude-code-shared-shaped: skill/
agent/script/resource nodes wired by registry.json, subagent_type= spawns,
etc.), this extractor has no notion of the claude-code-shared vocabulary. It
targets a plain code repo.

## What becomes a node

One node per discovered source file:
  - JS/TS: .js, .jsx, .ts, .tsx
  - Python: .py

type = "file". cluster = the file's containing directory relative to root
(or "root" for top-level files). is_shared is omitted (no shared-bucket
concept for generic code).

Noise directories are skipped: node_modules, .git, __pycache__, dist, build,
venv, .venv, and any dot-directory.

## What becomes an edge

Import/require-style edges, resolved only when they point at an actual
on-disk file within the walked directory (edges to unresolved bare/package/
stdlib imports are silently skipped -- they don't correspond to a node here):

- JS/TS: `import ... from '<path>'`, bare `import '<path>'`,
  `require('<path>')`, and `export ... from '<path>'` re-exports. Only
  relative paths (`./` or `../`) are resolved, trying the literal path, each
  of .js/.jsx/.ts/.tsx appended, and index.js/index.jsx/index.ts/index.tsx
  under a directory path.
- Python: `import <module>` and `from <module> import ...`, including
  relative imports (`from . import x`, `from .foo import x`). Dotted module
  paths are mapped to on-disk paths relative to the importing file's package
  (for relative imports) or relative to the walked root (for absolute
  imports), trying `<path>.py` and `<path>/__init__.py`.

All edges use kind "import".

## Usage

  python3 generic_code_extractor.py [base_dir]
  python3 generic_code_extractor.py --out graph.json
  python3 scanner.py --extractor generic-code [root]

Default base_dir: current working directory.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", "venv", ".venv"}

JS_TS_EXTS = (".js", ".jsx", ".ts", ".tsx")
JS_TS_INDEX_NAMES = ("index.js", "index.jsx", "index.ts", "index.tsx")
PY_EXT = ".py"

# JS/TS import/require patterns. Capture the quoted module path.
JS_IMPORT_FROM_RE = re.compile(r'''import\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]''')
JS_REQUIRE_RE = re.compile(r'''require\(\s*['"]([^'"]+)['"]\s*\)''')
JS_EXPORT_FROM_RE = re.compile(r'''export\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]''')

# Python import patterns.
PY_IMPORT_RE = re.compile(r'^\s*import\s+([A-Za-z0-9_.]+(?:\s*,\s*[A-Za-z0-9_.]+)*)', re.MULTILINE)
PY_FROM_IMPORT_RE = re.compile(r'^\s*from\s+(\.*[A-Za-z0-9_.]*)\s+import\s+', re.MULTILINE)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_source_files(base_dir):
    """Return a sorted list of repo-relative paths to every JS/TS/Python
    source file under base_dir, skipping noise directories."""
    out = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fname in files:
            if fname.endswith(JS_TS_EXTS) or fname.endswith(PY_EXT):
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, base_dir)
                out.append(rel)
    return sorted(out)


def cluster_for(relpath):
    parent = os.path.dirname(relpath)
    return parent if parent else "root"


# --------------------------------------------------------------------------
# JS/TS import resolution
# --------------------------------------------------------------------------

def extract_js_ts_specifiers(text):
    specs = []
    for pattern in (JS_IMPORT_FROM_RE, JS_REQUIRE_RE, JS_EXPORT_FROM_RE):
        for m in pattern.finditer(text):
            specs.append(m.group(1))
    return specs


def resolve_js_ts_specifier(specifier, from_relpath, file_set):
    """Resolve a relative JS/TS import specifier to a known on-disk relpath,
    or None if it doesn't resolve (bare/package imports, or a path that
    doesn't exist in this walked tree)."""
    if not (specifier.startswith("./") or specifier.startswith("../")):
        return None  # bare/package import -- not resolvable to a node here

    from_dir = os.path.dirname(from_relpath)
    candidate_base = os.path.normpath(os.path.join(from_dir, specifier))

    # Try the literal path, then with each JS/TS extension appended, then as
    # a directory with an index file.
    probes = [candidate_base]
    probes += [candidate_base + ext for ext in JS_TS_EXTS]
    probes += [os.path.join(candidate_base, idx) for idx in JS_TS_INDEX_NAMES]

    for probe in probes:
        norm = os.path.normpath(probe)
        if norm in file_set:
            return norm
    return None


# --------------------------------------------------------------------------
# Python import resolution
# --------------------------------------------------------------------------

def _py_module_to_candidates(module_path):
    """Given a dotted module path with no leading dots (e.g. 'pkg.sub.mod'),
    return candidate relpaths (posix-style, no extension normalization
    applied yet)."""
    if not module_path:
        return []
    parts = module_path.split(".")
    base = os.path.join(*parts)
    return [base + ".py", os.path.join(base, "__init__.py")]


def extract_py_specifiers(text):
    """Return a list of (module_path, level) tuples. level=0 means an
    absolute import (resolved relative to the walked root); level>0 means a
    relative import with that many leading dots (resolved relative to the
    importing file's containing package)."""
    out = []
    for m in PY_IMPORT_RE.finditer(text):
        modules = m.group(1).split(",")
        for mod in modules:
            mod = mod.strip()
            if mod:
                out.append((mod, 0))
    for m in PY_FROM_IMPORT_RE.finditer(text):
        raw = m.group(1)
        level = 0
        while level < len(raw) and raw[level] == ".":
            level += 1
        rest = raw[level:]
        out.append((rest, level))
    return out


def resolve_py_specifier(module_path, level, from_relpath, file_set):
    """Resolve a Python import to a known on-disk relpath, or None if it
    doesn't resolve (stdlib/third-party imports, or a path that doesn't
    exist in this walked tree)."""
    if level == 0:
        candidates = _py_module_to_candidates(module_path)
    else:
        # Relative import: climb `level` package directories up from the
        # importing file's own directory, then resolve module_path under
        # that.
        from_dir = os.path.dirname(from_relpath)
        base_dir = from_dir
        for _ in range(level - 1):
            base_dir = os.path.dirname(base_dir)
        if module_path:
            sub = _py_module_to_candidates(module_path)
            candidates = [os.path.join(base_dir, s) if base_dir else s for s in sub]
        else:
            # `from . import x` / `from .. import x` -- import of the
            # package itself; resolve to its __init__.py.
            candidates = [os.path.join(base_dir, "__init__.py")] if base_dir else ["__init__.py"]

    for cand in candidates:
        norm = os.path.normpath(cand)
        if norm in file_set:
            return norm
    return None


# --------------------------------------------------------------------------
# Churn (best-effort, optional)
# --------------------------------------------------------------------------

def compute_churn(base_dir):
    """Best-effort commit-count-per-file churn signal via one `git log` call.
    Returns {relpath: commit_count}, or {} if git is unavailable, the
    directory isn't a git repo, or the call fails for any reason."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=", "--name-only"],
            cwd=base_dir, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return {}
        counts = Counter(line.strip() for line in proc.stdout.splitlines() if line.strip())
        return dict(counts)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------

def build_graph(base_dir):
    base_dir = os.path.abspath(base_dir)
    files = discover_source_files(base_dir)
    file_set = set(files)

    nodes = {}
    edges = []

    for relpath in files:
        node_id = f"file:{relpath}"
        nodes[node_id] = {
            "id": node_id,
            "path": relpath,
            "type": "file",
            "cluster": cluster_for(relpath),
        }

    def add_edge(src, dst):
        if src in nodes and dst in nodes and src != dst:
            edges.append({"from": src, "to": dst, "kind": "import"})

    for relpath in files:
        full_path = os.path.join(base_dir, relpath)
        try:
            with open(full_path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        src_id = f"file:{relpath}"

        try:
            if relpath.endswith(JS_TS_EXTS):
                for specifier in extract_js_ts_specifiers(text):
                    target = resolve_js_ts_specifier(specifier, relpath, file_set)
                    if target is not None:
                        add_edge(src_id, f"file:{target}")
            elif relpath.endswith(PY_EXT):
                for module_path, level in extract_py_specifiers(text):
                    target = resolve_py_specifier(module_path, level, relpath, file_set)
                    if target is not None:
                        add_edge(src_id, f"file:{target}")
        except Exception:
            # A single malformed/unusual file should never abort the walk.
            continue

    # -- metrics --
    fan_out = defaultdict(int)
    fan_in = defaultdict(int)
    for e in edges:
        fan_out[e["from"]] += 1
        fan_in[e["to"]] += 1

    churn = compute_churn(base_dir)

    metrics = {}
    for node_id, node in nodes.items():
        m = {
            "fan_in": fan_in.get(node_id, 0),
            "fan_out": fan_out.get(node_id, 0),
            "dir_depth": node["path"].count(os.sep) + 1,
            "orphan": fan_in.get(node_id, 0) == 0 and fan_out.get(node_id, 0) == 0,
        }
        c = churn.get(node["path"])
        if c is not None:
            m["churn"] = c
        metrics[node_id] = m

    ordered_nodes = [nodes[k] for k in sorted(nodes.keys())]
    return {"nodes": ordered_nodes, "edges": edges, "metrics": metrics}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "base_dir", nargs="?", default=os.getcwd(),
        help="path to a code directory to scan (default: current working directory)",
    )
    parser.add_argument("--out", help="write graph JSON here instead of stdout")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    graph = build_graph(args.base_dir)
    out_text = json.dumps(graph, indent=2) + "\n"

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_text)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
