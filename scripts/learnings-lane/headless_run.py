#!/usr/bin/env python3
# headless_run.py: driver for `/improve-skill-learnings --headless <target>`
# What: runs the Step 4 validator and Step 6 drafter of improve-skill-learnings/SKILL.md (prompts are read
#   out of that file, never copied) over every root-cause cluster of one target, with no pickers, no
#   per-diff prompt, and one commit per cluster carrying a "Learning-Ids: id1,id2" trailer. Contract drift
#   (a contracts/*.md edit touching a field the JSON schema counterpart still defines) marks the cluster
#   needs-grill and commits nothing for it. Clusters flagged by cluster_flags.py are needs-grill too.
#   NEVER calls update-learning.py and NEVER writes unified-learnings.jsonl: stale / invalid / retarget /
#   needs-grill verdicts go to the run file (run-file-contract.json, checked by validate_run_file.py).
# Model calls: CLAUDE_BIN (default `claude`) is invoked as `$CLAUDE_BIN -p PROMPT [--model M] <config.CLAUDE_RESTRICT_ARGS>` (read-only tools); the clusterer
#   gets the request JSON on stdin. Tests swap CLAUDE_BIN for a stub.
# Failures: a nonzero exit or timeout (CLAUDE_CALL_TIMEOUT_S, default 600) raises ClaudeCallError, as does
#   validator output that stays malformed after a retry; the run aborts with no run file instead of writing stale.
# CLI: headless_run.py --target T --run-file OUT.json [--learnings PATH] [--repo DIR] [--events PATH]
import argparse
import html
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_d = Path(__file__).resolve().parent
SKILL_MD = _d.parent.parent / "skills" / "improve-skill-learnings" / "SKILL.md"
DEFAULT_LEARNINGS = _d.parent.parent / "learnings" / "unified-learnings.jsonl"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


config = _load("config")
cluster_target = _load("cluster_target")
cluster_flags = _load("cluster_flags")
lane_select = _load("lane_select")
validate_run_file = _load("validate_run_file")
ClaudeCallError = cluster_target.ClaudeCallError
VALIDATE_ATTEMPTS = 2
# Bounded pool for per-learning claude calls (validate, draft); LANE_CLAUDE_WORKERS overrides.
CLAUDE_WORKERS = max(1, int(os.environ.get("LANE_CLAUDE_WORKERS", "4")))


def pmap(fn, items):
    """Ordered map over items on a small worker pool; first exception propagates."""
    items = list(items)
    if len(items) < 2 or CLAUDE_WORKERS < 2:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(CLAUDE_WORKERS, len(items))) as ex:
        return list(ex.map(fn, items))

TYPE_DIRS = ("skill", "agent", "process", "contract")


def shared_root():
    return Path(os.environ.get("LANE_SHARED_ROOT", "~/.dotfiles/claude-code-shared")).expanduser()


def claude_bin():
    return os.environ.get("CLAUDE_BIN", "claude")


def prompt_from_skill(anchor):
    """First fenced block after the anchor text in SKILL.md."""
    text = SKILL_MD.read_text()
    a = text.index(anchor)
    m = re.search(r"^(`{3,4})[^\n]*\n(.*?)^\1\s*$", text[a:], re.S | re.M)
    return m.group(2)


def fill(template, values):
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def type_paths(slug, improves_type):
    r = shared_root()
    paths = {
        "skill": [r / "skills" / slug / "SKILL.md"],
        "agent": sorted(r.glob(f"agents/**/{slug}.md")) or [r / "agents" / f"{slug}.md"],
        "process": [r / "resources" / f"{slug}.md", r / "resources" / f"{slug}.json"],
        "contract": [r / "contracts" / f"{slug}.json", r / "contracts" / f"{slug}.md"],
    }
    order = [improves_type] + [t for t in TYPE_DIRS if t != improves_type] if improves_type in paths else list(TYPE_DIRS)
    return [str(p) for t in order for p in paths[t]]


def call_claude(prompt, model=None, stdin=None):
    cmd = [claude_bin(), "-p", prompt] + (["--model", model] if model else []) + config.CLAUDE_RESTRICT_ARGS
    try:
        p = cluster_target.procgroup.run_killpg(cmd, input=stdin, text=True, timeout=cluster_target.call_timeout())
    except subprocess.TimeoutExpired:
        raise ClaudeCallError(f"claude -p timed out after {cluster_target.call_timeout()}s")
    if p.returncode:
        raise ClaudeCallError(f"claude -p exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
    return p.stdout


def parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    return {}


def validate(row):
    slug = row.get("improves") or row.get("reported_by")
    cands = type_paths(slug, row.get("improves_type")) if slug else []
    prompt = fill(prompt_from_skill("Pass this prompt to the Haiku agent"),
                  {"id": row["id"], "improves": row.get("improves"), "improves_type": row.get("improves_type"),
                   "problem": row.get("problem"), "lesson": row.get("lesson"), "fix": row.get("fix"),
                   "path_candidates": "\n".join(cands)})
    # Malformed validator output is a failed call, not a verdict: retry, then raise. Never map it to stale.
    for _ in range(VALIDATE_ATTEMPTS):
        v = parse_json(call_claude(prompt, "haiku"))
        if v.get("id") == row["id"] and "verdict" in v:
            return v
    raise ClaudeCallError(f"malformed validator output for {row['id']} after {VALIDATE_ATTEMPTS} attempts")


def contained_target(row, file_path, repo):
    """Resolved path when file_path is a candidate for the row and inside repo; else None."""
    if not file_path or not isinstance(file_path, str):
        return None
    slug = row.get("improves") or row.get("reported_by")
    cands = {Path(c).expanduser().resolve() for c in (type_paths(slug, row.get("improves_type")) if slug else [])}
    fp = Path(file_path).expanduser().resolve()
    if fp not in cands or not fp.is_relative_to(Path(repo).resolve()):
        return None
    return fp


def draft(row, verdict):
    fp = Path(verdict["file_path"]).expanduser()
    prompt = fill(prompt_from_skill("Read the target file (resolved"),
                  {"file_path": fp, "id": row["id"], "problem": row.get("problem"),
                   "lesson": row.get("lesson"), "fix": row.get("fix")})
    prompt = prompt.replace("{full content of file_path}", fp.read_text())
    if verdict.get("verdict") == "partial":
        prompt += (f"\n\nExisting coverage is at line {verdict.get('existing_line')}. Extend that text. "
                   f"Add only: {verdict.get('missing')}. Do not duplicate what is already there.")
    out = call_claude(prompt)
    out = re.sub(r"^```[a-z]*\n|\n```\s*$", "", out.strip())
    return out


def parse_hunks(diff):
    hunks, cur = [], None
    for line in diff.splitlines():
        if line.startswith("@@"):
            cur = []
            hunks.append(cur)
        elif cur is not None and line[:1] in (" ", "-", "+"):
            cur.append(line)
        elif cur is not None and line == "":
            cur.append(" ")
    return hunks


def _hunk_text(h):
    return ("\n".join(l[1:] for l in h if l[0] in " -"),
            "\n".join(l[1:] for l in h if l[0] in " +"))


def apply_diff(content, diff):
    """Apply hunks by literal old/new text; None when a hunk does not anchor.

    Raw text is matched first so legitimate entities (&amp; etc.) survive; the
    unescaped hunk is only a fallback for model-escaped diffs.
    """
    for h in parse_hunks(diff):
        old, new = _hunk_text(h)
        if old not in content:
            old, new = _hunk_text([html.unescape(l) for l in h])
            if old not in content:
                return None
        content = content.replace(old, new, 1)
    return content


def changed_lines(diff):
    return [l for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def contract_drift(file_path, diff):
    """True when a contracts/*.md edit touches a backticked field the JSON schema counterpart still defines."""
    p = Path(file_path)
    if p.suffix != ".md" or p.parent.name != "contracts":
        return False
    fields = set()
    for l in changed_lines(diff):
        fields.update(re.findall(r"`([A-Za-z_][\w.]*)`", l))
    if not fields:
        return False
    for cand in (p.with_suffix(".json"), p.with_name(p.stem + "-schema.json")):
        if cand.exists():
            schema = cand.read_text()
            if any(f'"{f}"' in schema for f in fields):
                return True
    return False


def load_rows(path, target):
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if lane_select.is_eligible_row(e) and e["improves"] == target:
                rows.append(e)
    except FileNotFoundError:
        pass
    return rows


def git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def run_cluster(cluster, by_id, repo):
    """Returns (cluster_entry, verdict_entries)."""
    cid, members = cluster["id"], cluster["learning_ids"]
    verdicts, ready = [], []

    def v(lid, verdict, reason, **kw):
        verdicts.append(dict({"learning_id": lid, "cluster_id": cid, "verdict": verdict, "reason": reason}, **kw))

    if cluster.get("needs_grill"):
        reasons = cluster["flag_reasons"]
        for lid in members:
            v(lid, "needs-grill", ",".join(reasons))
        return {"id": cid, "status": "needs-grill", "learning_ids": members, "reasons": reasons}, verdicts

    for lid, res in zip(members, pmap(lambda i: validate(by_id[i]), members)):
        row = by_id[lid]
        kind, reason, fp = res.get("verdict"), res.get("reason") or "", res.get("file_path")
        if kind in ("valid", "partial") and fp:
            safe = contained_target(row, fp, repo)
            if safe is None:
                v(lid, "draft-failed", "file_path_outside_worktree_or_candidates", file_path=str(fp))
                continue
            res["file_path"] = str(safe)
            ready.append((row, res))
        elif kind == "misrouted" and res.get("suggested_improves"):
            v(lid, "retarget", reason or "misrouted", file_path=fp,
              suggested_improves=res["suggested_improves"], suggested_improves_type=res.get("suggested_improves_type"))
        elif kind == "invalid" or kind == "no_applicable_target":
            v(lid, "invalid", reason or kind, file_path=fp)
        else:
            v(lid, "stale", reason or "stale", file_path=fp)

    drafts = [(row, res, diff) for (row, res), diff in zip(ready, pmap(lambda rr: draft(*rr), ready))]
    if any(contract_drift(res["file_path"], diff) for _, res, diff in drafts):
        for row, res, _ in drafts:
            v(row["id"], "needs-grill", "contract-drift", file_path=res["file_path"])
        return {"id": cid, "status": "needs-grill", "learning_ids": members, "reasons": ["contract-drift"]}, verdicts

    contents, applied, touched = {}, [], []
    for row, res, diff in drafts:
        fp = res["file_path"]
        cur = contents.get(fp, Path(fp).expanduser().read_text())
        new = apply_diff(cur, diff)
        if new is None or new == cur:
            v(row["id"], "draft-failed", "diff_did_not_apply", file_path=fp)
            continue
        contents[fp] = new
        applied.append(row["id"])
        if fp not in touched:
            touched.append(fp)
        v(row["id"], "applied", "ok", file_path=fp)
    if not applied:
        return {"id": cid, "status": "no-change", "learning_ids": members}, verdicts
    for fp in touched:
        Path(fp).expanduser().write_text(contents[fp])
    git(repo, "add", "--", *[str(Path(f).expanduser()) for f in touched])
    subject = f"improve({cluster['target']}): {cluster['root_cause']}"[:50].rstrip()
    git(repo, "commit", "-q", "-m", subject, "-m", f"Learning-Ids: {','.join(applied)}")
    sha = git(repo, "rev-parse", "HEAD")
    return {"id": cid, "status": "committed", "learning_ids": members, "applied_ids": applied, "commit": sha}, verdicts


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)


def run(target, learnings, repo, run_file, events):
    rows = load_rows(learnings, target)
    doc = {"version": "1.0", "target": target, "verdicts": [], "clusters": []}
    if rows:
        agent = cluster_target.shell_agent(shlex.join(
            [claude_bin(), "-p", "ROLE: clusterer. Follow the learnings-clusterer agent contract "
             "(cluster-contract.json). The request JSON is on stdin. Return only the JSON.",
             *config.CLAUDE_RESTRICT_ARGS]))
        out = cluster_target.cluster_target(target, rows, agent, events)
        clusters = cluster_flags.flag_clusters(out["clusters"], rows) if out else []
        by_id = {r["id"]: r for r in rows}
        for c in clusters:
            entry, vs = run_cluster(c, by_id, repo)
            # Carried for the kick's cross-target promotion and PR body; the run-file check ignores extras.
            entry.update(target=c["target"], root_cause=c["root_cause"], fix=c["fix"])
            doc["clusters"].append(entry)
            doc["verdicts"].extend(vs)
            # Incremental: finished clusters survive a kill. Final run_file is only written on success.
            write_json(str(run_file) + ".partial", doc)
    errs = validate_run_file.errors(doc)
    if errs:
        raise RuntimeError("run file invalid: " + "; ".join(errs))
    write_json(run_file, doc)
    Path(str(run_file) + ".partial").unlink(missing_ok=True)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True)
    ap.add_argument("--run-file", required=True)
    ap.add_argument("--learnings", default=str(DEFAULT_LEARNINGS))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--events")
    a = ap.parse_args(argv)
    events = a.events or str(Path(a.run_file).with_name("lane-events.jsonl"))
    doc = run(a.target, a.learnings, a.repo, a.run_file, events)
    for c in doc["clusters"]:
        print(f"{c['id']}: {c['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
