#!/usr/bin/env python3
"""kick.py: manual entry point for one learnings-lane pass.

What: one pass, in order: (1) write-back of PR outcomes (lane_writeback, incl. escape_point);
  (2) eligibility (lane_select); (3) per eligible target, in its own git worktree, a headless run
  (headless_run: clustering, flagging, one commit per cluster) with a timeout, at most
  MAX_CONCURRENT_TARGETS at once; (4) the run file's verdicts applied on the main checkout
  (verdict_apply), then the gate (lane_gate checks + gate_runner layers) and the PR (pr_opener);
  (5) lesson-grouping route (lesson_groups) and cross-target promotion (cross_cluster); (6) one
  learnings push to main (learnings_push); (7) a digest: PRs opened, needs-grill, blocked, dropped
  learnings, null-target group counts, lane health (usage/correction-rate.py render_lane_digest).
  A timeout or crash on one target is logged to the events file and the other targets carry on.
  Lane worktrees never write unified-learnings.jsonl; a diff touching it is blocked by lane_gate.
Boot check: at the start of each non-dry kick, a real headless `$CLAUDE_BIN -p` (default `claude`) is
  booted from a worktree cut from current main. Failure logs a boot_failed event (main sha, error),
  distinct from target_failed/target_timeout, and the digest points at change-revert.sh for the suspect
  PR; the pass then carries on. Future fixture: when the static smoke test passed and boot_failed
  followed, the failing case becomes a test fixture (documented here, not built yet).
--dry-run: no gh calls, no worktrees, no commits, no learnings writes, no push. It lists the
  eligible targets (the open-PR cap is not checked, since that needs gh) and the digest counts.
Test hooks: every external effect is a field of Deps (claude via run_target, gh, git push, issue
  creation, learnings push). Tests pass stubs; defaults hit the real tools. CLAUDE_BIN and
  LANE_SHARED_ROOT are honoured by headless_run in the subprocess.
CLI: kick.py [--repo PATH] [--learnings-dir DIR] [--base BRANCH] [--dry-run] [--run-dir DIR]
"""
import argparse
import dataclasses
import importlib.util
import json
import os
import subprocess
import threading
import sys
import tempfile
import functools
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
SHARED_SUBDIR = "claude-code-shared"


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


config = _load("lane_config", HERE / "config.py")
lane_select = _load("kick_lane_select", HERE / "lane_select.py")
lane_writeback = _load("kick_lane_writeback", HERE / "lane_writeback.py")
verdict_apply = _load("kick_verdict_apply", HERE / "verdict_apply.py")
cluster_flags = _load("kick_cluster_flags", HERE / "cluster_flags.py")
cross_cluster = _load("kick_cross_cluster", HERE / "cross_cluster.py")
lesson_groups = _load("kick_lesson_groups", HERE / "lesson_groups.py")
gate_runner = _load("kick_gate_runner", HERE / "gate_runner.py")
lane_gate = _load("kick_lane_gate", HERE / "lane_gate.py")
pr_opener = _load("kick_pr_opener", HERE / "pr_opener.py")
learnings_push = _load("kick_learnings_push", HERE / "learnings_push.py")
procgroup = _load("kick_procgroup", HERE / "procgroup.py")
correction_rate = _load("kick_correction_rate", SHARED / "usage" / "correction-rate.py")

DEFAULT_EVENTS = lane_select.UNIFIED.parent / "lane-events.jsonl"


# --- default (real) effects ------------------------------------------------

_WT_LOCK = threading.Lock()  # git worktree add on one repo is not safe to run concurrently


def _git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def make_run_target(learnings_path, events):
    """Real headless run: headless_run.py as a subprocess in the worktree, killed after timeout_s."""
    def run_target(target, worktree, run_file, timeout_s):
        env = dict(os.environ, LANE_SHARED_ROOT=str(Path(worktree) / SHARED_SUBDIR))
        cmd = [sys.executable, str(HERE / "headless_run.py"), "--target", target, "--run-file", str(run_file),
               "--learnings", str(learnings_path), "--repo", str(worktree), "--events", str(events)]
        p = procgroup.run_killpg(cmd, cwd=worktree, env=env, text=True, timeout=timeout_s)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout).strip()[-500:])
    return run_target


def default_push_branch(worktree, branch):
    # Lane-owned branch, rebuilt from base each run (worktree add -B). A prior merged PR leaves the remote
    # branch behind (delete_branch_on_merge=false), so a plain push is non-fast-forward. select() guarantees
    # no open PR owns this target, so overwriting is safe.
    _git(worktree, "push", "-u", "--force", "origin", branch)


def default_gate_layers(target, worktree):
    return [("smoke", gate_runner.smoke_layer(root=worktree))]


@dataclass
class Deps:
    pr_state: Callable = lane_writeback.gh_pr_state
    open_prs: Callable = lane_select.gh_open_prs
    run_target: Optional[Callable] = None  # (target, worktree, run_file, timeout_s); None = real subprocess
    gate_layers: Callable = default_gate_layers  # (target, worktree) -> [(name, fn)]
    gh: Callable = pr_opener.default_gh
    push_branch: Callable = default_push_branch  # (worktree, branch)
    create_lesson_issue: Callable = lesson_groups.gh_create_issue
    create_cross_issue: Callable = cross_cluster.gh_create_issue
    find_cross_issue: Callable = cross_cluster.gh_find_issue
    push_learnings: Callable = learnings_push.push_learnings  # (repo) -> {"status","message"}
    boot: Optional[Callable] = None  # (repo, main_sha, run_dir); raises on failure; None = real claude -p
    timeout_s: Optional[float] = None  # default: TARGET_TIMEOUT_MINUTES
    max_concurrent: Optional[int] = None  # default: MAX_CONCURRENT_TARGETS


def default_boot(repo, main_sha, run_dir):
    """Boot a real headless claude -p (CLAUDE_BIN) from a throwaway worktree at main_sha."""
    wt = Path(run_dir) / "wt" / "_boot"
    wt.parent.mkdir(parents=True, exist_ok=True)
    with _WT_LOCK:
        _git(repo, "worktree", "add", "--detach", str(wt), main_sha)
    try:
        p = procgroup.run_killpg([os.environ.get("CLAUDE_BIN", "claude"), "-p", "reply with the word ok",
                                  *config.CLAUDE_RESTRICT_ARGS], cwd=wt,
                                 text=True, timeout=config.BOOT_TIMEOUT_SECONDS)
        if p.returncode:
            raise RuntimeError(f"claude -p exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)


def boot_check(repo, base, run_dir, deps, events):
    """Returns None on a good boot, else a boot_failed dict (also logged). Never raises."""
    try:
        sha = _git(repo, "rev-parse", base)
    except Exception as e:  # noqa: BLE001
        sha = None
        err = f"rev-parse {base}: {e}"
    else:
        try:
            (deps.boot or default_boot)(repo, sha, run_dir)
            return None
        except subprocess.TimeoutExpired:
            err = f"boot timed out after {config.BOOT_TIMEOUT_SECONDS}s"
        except Exception as e:  # noqa: BLE001
            err = str(e)
    ev = {"event": "boot_failed", "main_sha": sha, "error": err}
    log_event(events, ev)
    return {"main_sha": sha, "error": err}


# --- helpers ---------------------------------------------------------------

def log_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))) + "\n")


def _quotes(row):
    out = []
    for e in row.get("evidence") or []:
        q = e.get("quote") if isinstance(e, dict) else e
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out


def pr_details(doc, by_id):
    details = {}
    for c in doc["clusters"]:
        rows = [by_id[i] for i in c["learning_ids"] if i in by_id]
        details[c["id"]] = {
            "problem": c.get("root_cause") or "; ".join(r.get("problem", "") for r in rows),
            "anchor_quotes": [q for r in rows for q in _quotes(r)],
            "fix_rationale": c.get("fix", ""),
            "diff_summary": f"commit {c['commit'][:10]}" if c.get("commit") else "no commit",
        }
    return details


def gate_for(target, worktree, base_sha, deps):
    """lane_gate checks first, then gate_runner layers. Returns a gate dict for pr_opener."""
    files = _git(worktree, "diff", "--name-only", f"{base_sha}...HEAD").splitlines()
    shas = _git(worktree, "rev-list", f"{base_sha}..HEAD").split()
    msgs = [_git(worktree, "log", "-1", "--format=%B", s) for s in shas]
    ev = lane_gate.evaluate(files, msgs)
    if ev["verdict"] == "fail":
        return {"verdict": "blocked", "draft": True, "labels": ["blocked"], "failing_layer": ev["failing_check"],
                "pr_body": f"Lane check `{ev['failing_check']}` failed.", "layers": []}
    if ev["verdict"] == "needs-grill":
        return {"verdict": "needs-grill", "draft": True, "labels": ["needs-grill"],
                "failing_layer": ev["failing_check"], "pr_body": f"Lane check `{ev['failing_check']}` needs a grill.",
                "layers": []}
    return gate_runner.run_gate(deps.gate_layers(target, worktree))


def _prepare(target, base_sha, repo, run_dir, deps, run_target, timeout_s, events):
    """Worker half: worktree + headless run. Returns (worktree, run_file); raises on timeout/failure."""
    slug = target.replace("/", "_")
    worktree = Path(run_dir) / "wt" / slug
    branch = config.LANE_BRANCH_PREFIX + target
    worktree.parent.mkdir(parents=True, exist_ok=True)
    with _WT_LOCK:
        _git(repo, "worktree", "add", "-B", branch, str(worktree), base_sha)
    run_file = Path(run_dir) / f"{slug}.run.json"
    run_target(target, worktree, run_file, timeout_s)
    return worktree, run_file


# --- the pass ---------------------------------------------------------------

def _bind_gh_cwd(deps, repo):
    """Run the default gh callables in repo so they hit the same remote as the git calls."""
    defaults = {"pr_state": lane_writeback.gh_pr_state, "open_prs": lane_select.gh_open_prs,
                "gh": pr_opener.default_gh, "create_lesson_issue": lesson_groups.gh_create_issue,
                "create_cross_issue": cross_cluster.gh_create_issue,
                "find_cross_issue": cross_cluster.gh_find_issue}
    bound = {k: functools.partial(fn, cwd=repo) for k, fn in defaults.items() if getattr(deps, k) is fn}
    return dataclasses.replace(deps, **bound) if bound else deps


ATTEMPTS_FILE = "lane-target-attempts.json"


def _load_attempts(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cooling_targets(path, now=None):
    """Targets whose last no-commit run is within NO_COMMIT_COOLDOWN_DAYS. Like lesson-groups-filed.json, a state file."""
    now = now or time.time()
    limit = config.NO_COMMIT_COOLDOWN_DAYS * 86400
    return {t for t, ts in _load_attempts(path).items() if isinstance(ts, (int, float)) and now - ts < limit}


def record_attempt(path, target, now=None):
    data = _load_attempts(path)
    data[target] = now or time.time()
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def kick(repo, learnings_dir=None, deps=None, dry_run=False, base="main", run_dir=None, events=None):
    """One lane pass. Returns the digest dict (also under digest["lines"])."""
    deps = deps or Deps()
    repo = str(Path(repo).resolve())
    deps = _bind_gh_cwd(deps, repo)
    learnings_dir = Path(learnings_dir or lane_select.UNIFIED.parent)
    learnings_path = learnings_dir / "unified-learnings.jsonl"
    events = events or learnings_dir / "lane-events.jsonl"
    timeout_s = deps.timeout_s if deps.timeout_s is not None else config.TARGET_TIMEOUT_MINUTES * 60
    workers = deps.max_concurrent or config.MAX_CONCURRENT_TARGETS
    run_dir = Path(run_dir or tempfile.mkdtemp(prefix="lane-kick-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    d = {"dry_run": dry_run, "opened": [], "blocked": [], "needs_grill": [], "dropped": [], "retargeted": [],
         "failed": [], "timeouts": [], "targets": [], "writeback": {}, "lessons": {}, "cross": {}, "push": None,
         "errors": [], "boot_failed": None}
    all_clusters = []
    log_event(events, {"event": "kick_started", "dry_run": dry_run})

    # 0. after-merge boot check (no worktrees in a dry run)
    if not dry_run:
        d["boot_failed"] = boot_check(repo, base, run_dir, deps, events)

    # 1. write-back
    if not dry_run:
        try:
            d["writeback"] = lane_writeback.writeback(pr_state=deps.pr_state, learnings_dir=learnings_dir, repo=repo)
            d["errors"] += d["writeback"]["errors"]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            d["errors"].append(f"writeback: gh failed: {e}; skipped this pass")

    # 2. eligibility
    rows = lane_select.load_rows(learnings_path)
    open_prs = (lambda: []) if dry_run else deps.open_prs
    attempts_path = learnings_dir / ATTEMPTS_FILE
    try:
        selected = lane_select.select(rows, open_prs=open_prs, skip=cooling_targets(attempts_path))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        d["errors"].append(f"select: gh failed: {e}; no targets this pass")
        selected = []
    d["targets"] = [t for t, _ in selected]

    # 3-4. headless run per target (worktree, timeout, concurrency), then verdicts + gate + PR
    if not dry_run and selected:
        base_sha = _git(repo, "rev-parse", base)
        run_target = deps.run_target or make_run_target(learnings_path, events)
        by_id = {r["id"]: r for r in rows if r.get("id")}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_prepare, t, base_sha, repo, run_dir, deps, run_target, timeout_s, events): t
                    for t, _ in selected}
            for fut in as_completed(futs):
                target = futs[fut]
                try:
                    worktree, run_file = fut.result()
                except subprocess.TimeoutExpired:
                    d["timeouts"].append(target)
                    log_event(events, {"event": "target_timeout", "target": target, "timeout_s": timeout_s})
                    _cleanup(repo, run_dir, target)
                    continue
                except Exception as e:  # noqa: BLE001 - one target must not stop the rest
                    d["failed"].append({"target": target, "error": str(e)})
                    log_event(events, {"event": "target_failed", "target": target, "error": str(e)})
                    _cleanup(repo, run_dir, target)
                    continue
                try:
                    _finish_target(target, worktree, run_file, by_id, base, base_sha, learnings_dir, deps, d,
                                   all_clusters, events, attempts_path)
                except Exception as e:  # noqa: BLE001
                    d["failed"].append({"target": target, "error": str(e)})
                    log_event(events, {"event": "target_failed", "target": target, "error": str(e)})
                finally:
                    _cleanup(repo, run_dir, target)

    # 5. lesson-grouping route + cross-target promotion
    rows = lane_select.load_rows(learnings_path)
    state = learnings_dir / "lesson-groups-filed.json"
    filed_ids = lesson_groups.load_state(state)
    res = lesson_groups.route(rows, filed_ids, (lambda p: None) if dry_run else deps.create_lesson_issue)
    if not dry_run:
        lesson_groups.save_state(state, filed_ids)
    d["errors"] += res["errors"]
    d["lessons"] = {"filed": [p["group_id"] for p in res["filed"]], "pending": res["pending"],
                    "pending_groups": res["pending_groups"]}
    if not dry_run and all_clusters:
        d["cross"] = cross_cluster.promote(all_clusters, rows, create_issue=lambda p: deps.create_cross_issue(p),
                                           find_issue=lambda p: deps.find_cross_issue(p),
                                           learnings_dir=learnings_dir)
        d["errors"] += d["cross"]["errors"]

    # 6. one learnings push, last
    if not dry_run:
        d["push"] = deps.push_learnings(repo)

    # 7. digest
    d["lines"] = digest_lines(d, learnings_path)
    log_event(events, {"event": "kick_done", "opened": len(d["opened"]), "blocked": len(d["blocked"]),
                       "needs_grill": len(d["needs_grill"]), "dropped": len(d["dropped"]),
                       "push": (d["push"] or {}).get("status")})
    return d


def _finish_target(target, worktree, run_file, by_id, base, base_sha, learnings_dir, deps, d, all_clusters, events,
                   attempts_path=None):
    doc = json.loads(Path(run_file).read_text())
    all_clusters.extend(c for c in doc["clusters"] if c.get("root_cause"))
    flagged = [dict(c, needs_grill=True, flag_reasons=c["reasons"]) for c in doc["clusters"]
               if c["status"] == "needs-grill"]
    d["needs_grill"] += cluster_flags.digest_lines(flagged)
    def apply():
        applied = verdict_apply.apply_verdicts(run_file, learnings_dir)
        d["dropped"] += applied["dropped"]
        d["retargeted"] += applied["retargeted"]
        d["errors"] += applied["errors"]

    if not any(c["status"] == "committed" for c in doc["clusters"]):
        apply()
        if attempts_path:
            record_attempt(attempts_path, target)
            log_event(events, {"event": "no_commit_cooldown", "target": target,
                               "days": config.NO_COMMIT_COOLDOWN_DAYS})
        return
    branch = config.LANE_BRANCH_PREFIX + target
    gate = gate_for(target, worktree, base_sha, deps)
    deps.push_branch(worktree, branch)
    # Verdicts mutate learnings, so they apply only once a PR exists for the pushed branch.
    try:
        url = pr_opener.find_open_pr(deps.gh, branch)
        if url:
            log_event(events, {"event": "pr_exists", "target": target, "url": url})
        else:
            url = pr_opener.open_pr(target, doc, pr_details(doc, by_id), gate, gh=deps.gh, base=base)
    except pr_opener.PrRefused as e:
        d["errors"].append(f"{target}: PR refused: {e}; branch {branch} pushed without a PR, verdicts not applied")
        log_event(events, {"event": "pr_refused", "target": target, "reason": str(e), "pending_branch": branch})
        return
    except Exception as e:  # noqa: BLE001 - gh failure
        log_event(events, {"event": "pr_failed", "target": target, "error": str(e), "pending_branch": branch})
        raise RuntimeError(f"{e}; branch {branch} pushed without a PR, verdicts not applied") from e
    apply()
    d["opened"].append({"target": target, "url": url, "verdict": gate["verdict"]})
    if gate["verdict"] == "blocked":
        d["blocked"].append(f"blocked: {target} at {gate['failing_layer']}")
    elif gate["verdict"] == "needs-grill":
        d["needs_grill"].append(f"needs-grill: {target} PR ({gate['failing_layer']})")
    log_event(events, {"event": "pr_opened", "target": target, "url": url, "verdict": gate["verdict"]})


def _cleanup(repo, run_dir, target):
    wt = Path(run_dir) / "wt" / target.replace("/", "_")
    if wt.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)


def digest_lines(d, learnings_path):
    tag = " (dry run)" if d["dry_run"] else ""
    lines = [f"learnings lane kick{tag}"]
    if d["dry_run"]:
        lines.append(f"would run: {len(d['targets'])} target(s): {', '.join(d['targets']) or 'none'}")
    bf = d.get("boot_failed")
    if bf:
        lines.append(f"BOOT FAILED on main {bf['main_sha']}: {bf['error']}")
        lines.append("  suspect: the most recent merged PR; revert with claude-code-shared/scripts/attribution/"
                     "change-revert.sh")
    lines.append(f"PRs opened: {len(d['opened'])}")
    lines += [f"  {p['target']}: {p['url']} ({p['verdict']})" for p in d["opened"]]
    lines.append(f"needs-grill: {len(d['needs_grill'])}")
    lines += [f"  {l}" for l in d["needs_grill"]]
    lines.append(f"blocked: {len(d['blocked'])}")
    lines += [f"  {l}" for l in d["blocked"]]
    lines.append(f"dropped learnings: {len(d['dropped'])}")
    lines += [f"  {x['id']} ({x['verdict']}): {x['reason']}" for x in d["dropped"]]
    lines.append(f"timeouts: {len(d['timeouts'])}" + "".join(f"\n  {t}" for t in d["timeouts"]))
    lines.append(f"failed targets: {len(d['failed'])}" + "".join(f"\n  {f['target']}: {f['error']}" for f in d["failed"]))
    ls = d["lessons"]
    lines.append(f"null-target groups: {len(ls.get('filed', []))} filed, {ls.get('pending_groups', 0)} pending "
                 f"({ls.get('pending', 0)} learnings)")
    push = d.get("push")
    if push:
        lines.append(f"learnings push: {push['status']} ({push['message']})")
        if push["status"] in ("refused", "failed"):
            lines.append(f"warning: learnings push {push['status']}: {push['message']}")
    lines += [f"warning: {e}" for e in d["errors"]]
    try:
        metrics = correction_rate.lane_metrics(lane_select.load_rows(learnings_path))
        lines += correction_rate.render_lane_digest(metrics)
    except FileNotFoundError:
        pass
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None, help="main checkout (default: git toplevel of cwd)")
    ap.add_argument("--learnings-dir", default=None)
    ap.add_argument("--base", default="main")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    repo = a.repo or subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                    text=True).stdout.strip()
    d = kick(repo, a.learnings_dir, dry_run=a.dry_run, base=a.base, run_dir=a.run_dir)
    print("\n".join(d["lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
