#!/usr/bin/env python3
"""write-change-records.py — write one change record per merged PR on main.

What: scans merge commits and squash commits on the first-parent line of a branch (default
  main) that touch claude-code-shared/** and writes one record per change to
  attribution/change-records/<change_id>.json, keyed by merged_sha (the merge or squash commit; base_sha
  is its first parent). Direct commits on the line get none: a single-parent commit counts as
  a squash only with a `(#N)` subject suffix or a lane trailer. Of what remains, only changes
  that touch skill, agent, contract, hook or script files
  get a record; changes that only add learning rows, edit config/resources, change tests or
  touch files elsewhere get none. Records follow contracts/change-record-schema.json.
  Lane comes from commit trailers (resources/change-record-trailers.md): `Seed-Id: <id>` is
  lane seed, `Learning-Ids: <id>,<id>` is lane learnings (ids land in source_refs), neither
  is lane manual.
When: on demand, and from the weekly attribution job. Passive: reads git history, writes only
  record files, never touches the repo's refs, index or worktree, so it can never block or
  alter a merge. The one exception is --commit, which commits the new records and pushes them
  (attribution/change_records_commit.py, attribution/change_records_push.py). Idempotent: a merge that already has a record is skipped, and existing
  record files are never rewritten.

Usage:
  write-change-records.py [--repo PATH] [--since WHEN] [--ref REF] [--out-dir DIR]
                          [--prefix DIR/] [--target-repo ORG/REPO] [--dry-run] [--commit]

  --repo         git repo to scan (default: the repo containing the current directory)
  --since        only merges committed after this (git date, default "60 days ago")
  --ref          branch to scan (default main, falling back to HEAD)
  --out-dir      where records go (default: the change-records folder from scripts/shared/change_records_path.py)
  --prefix       directory inside the repo that holds the toolkit (default claude-code-shared/)
  --target-repo  Org/Repo to record (default: parsed from the origin remote, else the dir name)
  --dry-run      report what would be written without creating anything
  --commit       commit the new records, then push them to origin/main when they are the only
                 unpushed work on main (otherwise a "warning:" line; exit code unaffected)

Importable: list_merges, classify_path, derive_sections, build_record, write_change_records.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SHARED / "scripts" / "shared"))

from change_records_path import change_records_path  # noqa: E402
from md_headings import heading_map  # noqa: E402  (one heading parser, shared with validate-escape-point)

SCHEMA_PATH = SHARED / "contracts" / "change-record-schema.json"

DEFAULT_PREFIX = "claude-code-shared/"
DEFAULT_SINCE = "60 days ago"
SCHEMA_VERSION = "1"
RUN_LOOKUP_LIMIT = 3  # recent CI runs inspected per commit
CHANGE_ID_HEX = 12  # leading sha hex chars in a chg- id

# Order decides the record's single `kind` when a merge touches several.
KIND_PRIORITY = ["skill", "agent", "contract", "hook", "script"]

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
PREAMBLE = "(preamble)"
TRAILER_RE = re.compile(r"^(Seed-Id|Learning-Ids):[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
TRAILER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def git(repo, *args, check=True, timeout=None):
    """Run git in repo. timeout=None (the default) is untimed; a caller opts in per call.

    An exceeded timeout raises subprocess.TimeoutExpired.
    """
    # Bytes, decoded by hand: text=True would translate a bare \r to \n and shift line numbers.
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, check=False, timeout=timeout,
    )
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, ["git", *args], proc.stdout, proc.stderr
        )
    return proc


def resolve_ref(repo, ref):
    if ref and git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0:
        return ref
    return "HEAD"


def list_merges(repo, ref, since):
    """Changes on the first-parent line of ref, oldest first.

    Each entry is either a merge commit (hand-made PRs land as `Merge pull request #N ...`) or
    a squash commit (a single-parent commit straight on the line). Both record the commit as
    `sha` and its first parent as `base_sha`. Root commits have no base and are skipped.
    `message` is the full commit message; `parents` lists every parent (merges have 2+).
    """
    args = ["log", "--first-parent", "--reverse",
            "--format=%H%x1f%P%x1f%cI%x1f%s%x1f%B%x1e"]
    if since:
        args.append(f"--since={since}")
    args.append(ref)
    merges = []
    for record in git(repo, *args).stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, merged_at, subject, message = record.split("\x1f", 4)
        parent_list = parents.split()
        if not parent_list:
            continue
        merges.append({
            "sha": sha,
            "base_sha": parent_list[0],
            "parents": parent_list,
            "is_merge": len(parent_list) > 1,
            "merged_at": merged_at,
            "subject": subject,
            "message": message,
        })
    return merges


def classify_path(rel):
    """Classify a path relative to the toolkit dir.

    Returns (kind, unit) where unit is a '<kind>:<name>' string or None, or None when the path
    is not a skill, agent, contract, hook or script file (config, resources, tests, learnings...).
    """
    parts = rel.split("/")
    top = parts[0]
    if top == "skills" and len(parts) >= 3:
        return "skill", f"skill:{parts[1]}"
    if top == "agents" and len(parts) >= 2 and parts[-1] != "registry.json":
        return "agent", f"agent:{Path(parts[-1]).stem}"
    if top == "contracts" and len(parts) == 2:
        return "contract", f"contract:{Path(parts[1]).stem}"
    if top == "hooks" and len(parts) >= 2:
        return "hook", f"hook:{'/'.join(parts[1:])}"
    if top == "scripts" and len(parts) >= 2 and parts[1] != "tests":
        return "script", f"script:{'/'.join(parts[1:])}"
    return None


def changed_line_ranges(diff_text):
    """New-side (start, count) pairs from a -U0 diff."""
    ranges = []
    for line in diff_text.split("\n"):
        m = HUNK_RE.match(line)
        if m:
            ranges.append((int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1))
    return ranges


def derive_sections(repo, base_sha, merged_sha, path):
    """Headings of the markdown sections that the diff hunks for `path` fall under."""
    if not path.endswith(".md"):
        return []
    diff = git(repo, "diff", "-U0", "--no-renames", base_sha, merged_sha, "--", path).stdout
    ranges = changed_line_ranges(diff)
    if not ranges:
        return []
    shown = git(repo, "show", f"{merged_sha}:{path}", check=False)
    if shown.returncode != 0:  # deleted file: nothing on the new side
        return []
    by_line = heading_map(shown.stdout)
    last = len(by_line) - 1
    found = set()
    for start, count in ranges:
        lines = range(start, start + count) if count > 0 else [start]
        for n in lines:
            n = min(max(n, 0), last)
            found.add(by_line[n] if n >= 1 and by_line[n] else PREAMBLE)
    return sorted(found)


def changed_paths(repo, base_sha, merged_sha, prefix):
    out = git(repo, "diff", "--name-status", "--no-renames", "-z",
              base_sha, merged_sha, "--", prefix).stdout
    fields = out.split("\0")
    paths = []
    for i in range(0, len(fields) - 1, 2):
        paths.append((fields[i], fields[i + 1]))
    return paths


def parse_lane_trailers(message):
    """(seed_ids, learning_ids) from `Seed-Id:` / `Learning-Ids:` trailer lines in a message.

    Keys are matched case-insensitively at line start. Learning-Ids is a comma-separated list
    and may repeat. Branch commits are untrusted, so a value that does not match
    TRAILER_ID_RE is dropped (it never sets the lane or reaches source_refs).
    See resources/change-record-trailers.md.
    """
    seed_ids, learning_ids = [], []
    for line in message.splitlines():
        m = TRAILER_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        if key == "seed-id":
            seed_ids.append(value.strip())
        else:
            learning_ids.extend(v.strip() for v in value.split(","))
    return ([s for s in seed_ids if TRAILER_ID_RE.match(s)],
            [i for i in learning_ids if TRAILER_ID_RE.match(i)])


def branch_messages(repo, merge):
    """Messages of the commits a merge brought in (first parent..each other parent)."""
    messages = []
    for parent in merge.get("parents", [])[1:]:
        proc = git(repo, "log", "--format=%B%x1e", f"{merge['base_sha']}..{parent}", check=False)
        messages.extend(m for m in proc.stdout.split("\x1e") if m.strip())
    return messages


def is_pr_change(merge):
    """True for a merge commit, or a squash commit marked by `(#N)` or a lane trailer."""
    if merge["is_merge"] or re.search(r"\(#\d+\)\s*$", merge["subject"]):
        return True
    seed_ids, learning_ids = parse_lane_trailers(merge["message"])
    return bool(seed_ids or learning_ids)


def derive_lane_and_refs(merge, repo=None):
    """Lane and source_refs for a merge or squash commit.

    Seed-Id wins over Learning-Ids; with neither the lane is `manual`. Trailers are read from
    the commit's own message and, for a merge, from the commits it merged in. Seed and
    learning ids go into source_refs verbatim, after the pr:/branch: refs.
    """
    refs = []
    subject = merge["subject"]
    pr_merge = re.match(r"Merge pull request #(\d+) from (\S+)", subject)
    if pr_merge:
        refs.append(f"pr:{pr_merge.group(1)}")
        branch = pr_merge.group(2)
        refs.append(f"branch:{branch.split('/', 1)[1] if '/' in branch else branch}")
    else:
        branch_merge = re.match(r"Merge branch '([^']+)'", subject)
        if branch_merge:
            refs.append(f"branch:{branch_merge.group(1)}")
        else:
            squash = re.search(r"\(#(\d+)\)\s*$", subject)  # squash: "Title (#123)"
            if squash:
                refs.append(f"pr:{squash.group(1)}")

    messages = [merge.get("message", subject)]
    if repo is not None and merge.get("is_merge"):
        messages.extend(branch_messages(repo, merge))
    seed_ids, learning_ids = [], []
    for message in messages:
        message_seed_ids, message_learning_ids = parse_lane_trailers(message)
        seed_ids.extend(message_seed_ids)
        learning_ids.extend(message_learning_ids)

    if seed_ids:
        lane, ids = "seed", seed_ids
    elif learning_ids:
        lane, ids = "learnings", learning_ids
    else:
        return "manual", refs
    for i in ids:
        if i not in refs:
            refs.append(i)
    return lane, refs


def detect_target_repo(repo):
    proc = git(repo, "remote", "get-url", "origin", check=False)
    if proc.returncode == 0:
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", proc.stdout.strip())
        if m:
            return m.group(1)
    return Path(repo).resolve().name


CI_ARTIFACT_MAX_BYTES = 1_000_000  # per file; the artifact comes from PR-head code, so it is untrusted
CI_MAX_RESULTS = 5000
CI_MAX_KEYS = 20  # schema maxProperties on totals and snapshot_diff
CI_STATUSES = ("pass", "fail")


def _read_artifact_json(path):
    """json.loads of a size-capped artifact file; ValueError when over the cap."""
    if path.stat().st_size > CI_ARTIFACT_MAX_BYTES:
        raise ValueError(f"{path.name}: over {CI_ARTIFACT_MAX_BYTES} bytes")
    return json.loads(path.read_text())


def _valid_results_doc(data):
    """Shape check for a run-all-tests JSON doc: {results: {file: pass|fail}, totals?: {k: int}}."""
    if not isinstance(data, dict) or set(data) - {"results", "totals"}:
        return False
    results = data.get("results")
    if not isinstance(results, dict) or len(results) > CI_MAX_RESULTS:
        return False
    if not all(isinstance(k, str) and v in CI_STATUSES for k, v in results.items()):
        return False
    totals = data.get("totals", {})
    return isinstance(totals, dict) and len(totals) <= CI_MAX_KEYS and all(
        isinstance(k, str) and type(v) is int for k, v in totals.items())


def _valid_diff_doc(data):
    """Shape check for ci-snapshot-diff output: known list fields typed, other values scalar."""
    if not isinstance(data, dict) or len(data) > CI_MAX_KEYS:
        return False
    for key, value in data.items():
        if key in ("newly_failing", "newly_passing", "unchanged"):
            ok = isinstance(value, list) and len(value) <= CI_MAX_RESULTS and all(
                isinstance(x, str) for x in value)
        elif key in ("added", "removed"):
            ok = isinstance(value, list) and len(value) <= CI_MAX_RESULTS and all(
                isinstance(x, dict) and set(x) == {"file", "status"}
                and isinstance(x["file"], str) and x["status"] in CI_STATUSES for x in value)
        elif key == "totals":
            ok = isinstance(value, dict) and len(value) <= CI_MAX_KEYS and all(
                isinstance(k, str) and type(v) is int for k, v in value.items())
        else:
            ok = value is None or isinstance(value, (bool, int, float, str))
        if not ok:
            return False
    return True


# Total wall-clock budget for all CI lookups in one writer run. Once spent, remaining manual
# records get ci_snapshot null without calling gh (a null is final, so keep it bounded).
CI_LOOKUP_BUDGET_SECONDS = 180


def fetch_ci_snapshot(target_repo, head_sha=None, pr_number=None, timeout=60, deadline=None):
    """The CI base-vs-head test snapshot for a merged PR, or None. Never raises.

    Looks up the Test snapshot workflow run for the PR's head sha (a squash commit does not
    carry it, so it is resolved from the PR number) and downloads its `test-snapshot` artifact
    (base.json, head.json, diff.json). All gh access lives here, via `gh` on PATH, so tests
    stub it there. Offline, no gh, no run, no artifact or a malformed one all give None.
    `deadline` is a time.monotonic() value; each gh call is capped to what remains of it.
    """
    import shutil
    import tempfile
    try:
        gh_bin = shutil.which("gh")
        if gh_bin is None:
            return None

        def gh(*args):
            left = timeout if deadline is None else min(timeout, deadline - time.monotonic())
            if left <= 0:
                raise TimeoutError("ci lookup budget spent")
            proc = subprocess.run([gh_bin, *args], capture_output=True, text=True,
                                  timeout=left, check=False)
            return proc.stdout if proc.returncode == 0 else None

        if not head_sha and pr_number:
            out = gh("pr", "view", str(pr_number), "-R", target_repo, "--json", "headRefOid")
            head_sha = json.loads(out).get("headRefOid") if out else None
        if not head_sha:
            return None
        out = gh("run", "list", "-R", target_repo, "--workflow", "test-snapshot.yml",
                 "--commit", head_sha, "--limit", str(RUN_LOOKUP_LIMIT), "--json", "databaseId")
        for run in (json.loads(out) if out else []):
            with tempfile.TemporaryDirectory() as tmp:
                if gh("run", "download", str(run["databaseId"]), "-R", target_repo,
                      "-n", "test-snapshot", "-D", tmp) is None:
                    continue
                try:
                    files = {n: _read_artifact_json(Path(tmp) / f"{n}.json")
                             for n in ("base", "head")}
                except (OSError, ValueError):
                    continue
                if not all(_valid_results_doc(d) for d in files.values()):
                    continue
                try:
                    diff = _read_artifact_json(Path(tmp) / "diff.json")
                except (OSError, ValueError):
                    diff = None
                if diff is not None and not _valid_diff_doc(diff):
                    diff = None
                return {"run_id": run["databaseId"], "head_sha": head_sha,
                        "base": files["base"], "head": files["head"], "diff": diff}
    except Exception:  # informational only: nothing here may block a record
        return None
    return None


def build_record(repo, merge, prefix=DEFAULT_PREFIX, target_repo=None,
                 fetch_ci=True, ci_deadline=None):
    """Build the change record for one merge, or None if it does not qualify."""
    paths = changed_paths(repo, merge["base_sha"], merge["sha"], prefix)
    touched_paths = sorted({p for _, p in paths})
    kinds = set()
    units = set()
    sections = []
    for status, path in paths:
        rel = path[len(prefix):] if path.startswith(prefix) else path
        cls = classify_path(rel)
        if cls is None:
            continue
        kinds.add(cls[0])
        units.add(cls[1])
        if status != "D":
            for heading in derive_sections(repo, merge["base_sha"], merge["sha"], path):
                sections.append({"path": path, "section": heading})
    if not kinds:
        return None
    lane, source_refs = derive_lane_and_refs(merge, repo)
    ci = {}
    if lane == "manual":  # informational CI snapshot; null when unavailable or skipped
        pr = next((r[3:] for r in source_refs if r.startswith("pr:")), None)
        ci["ci_snapshot"] = fetch_ci_snapshot(
            target_repo or detect_target_repo(repo),
            merge["parents"][1] if merge.get("is_merge") else None, pr,
            deadline=ci_deadline) if fetch_ci else None
    record = {
        "schema_version": SCHEMA_VERSION,
        "change_id": f"chg-{merge['sha'][:CHANGE_ID_HEX]}",
        "lane": lane,
        "source_refs": source_refs,
        "target_repo": target_repo or detect_target_repo(repo),
        "touched_paths": touched_paths,
        "sections": sorted(sections, key=lambda s: (s["path"], s["section"])),
        "touched_units": sorted(units),
        "kind": next(k for k in KIND_PRIORITY if k in kinds),
        "base_sha": merge["base_sha"],
        "merged_sha": merge["sha"],
        "merged_at": merge["merged_at"],
    }
    record.update(ci)
    return record


def load_schema_validator():
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(SCHEMA_PATH.read_text())
    return lambda rec: jsonschema.validate(rec, schema)


def existing_merged_shas(out_dir):
    shas = {}
    if out_dir.is_dir():
        for p in out_dir.glob("*.json"):
            try:
                shas[json.loads(p.read_text()).get("merged_sha")] = p.name
            except (OSError, ValueError):
                continue
    return shas


def write_change_records(repo, out_dir, since=DEFAULT_SINCE, ref="main",
                         prefix=DEFAULT_PREFIX, target_repo=None, dry_run=False):
    """Write a record for every qualifying merge that has none yet.

    Returns {"written": [...], "skipped": [...], "ignored": [...], "errors": [...]} where the
    first two hold change_ids, ignored holds merge shas that do not qualify.
    """
    repo = Path(repo)
    out_dir = Path(out_dir)
    ref = resolve_ref(repo, ref)
    validate = load_schema_validator()
    have = existing_merged_shas(out_dir)
    result = {"written": [], "skipped": [], "ignored": [], "errors": []}
    ci_deadline = time.monotonic() + CI_LOOKUP_BUDGET_SECONDS
    for merge in list_merges(repo, ref, since):
        if merge["sha"] in have:
            result["skipped"].append(have[merge["sha"]][:-len(".json")])
            continue
        if not is_pr_change(merge):
            result["ignored"].append(merge["sha"])
            continue
        try:
            record = build_record(repo, merge, prefix, target_repo,
                                  fetch_ci=not dry_run, ci_deadline=ci_deadline)
            if record is None:
                result["ignored"].append(merge["sha"])
                continue
            if validate:
                validate(record)
        except Exception as exc:  # one bad merge must not stop the rest
            result["errors"].append(f"{merge['sha']}: {exc}")
            continue
        target = out_dir / f"{record['change_id']}.json"
        if target.exists():
            result["skipped"].append(record["change_id"])
            continue
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            if not _write_exclusive(target, json.dumps(record, indent=2) + "\n"):
                result["skipped"].append(record["change_id"])
                continue
        result["written"].append(record["change_id"])
    return result


def _write_exclusive(target, text):
    """Write target atomically via a unique temp file; never replace an existing one.

    Returns False if another writer created target first. The hard link fails
    with FileExistsError when target exists, so concurrent hook and weekly runs
    cannot clobber each other and no shared temp name exists.
    """
    import os
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        try:
            os.link(tmp_name, target)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def default_repo():
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.exit("not inside a git repo; pass --repo")
    return proc.stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write change records for merged PRs on main.")
    ap.add_argument("--repo")
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--out-dir")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--target-repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--commit", action="store_true",
                    help="commit new records (only attribution/change-records/ files) on the current branch, "
                         "then push them to origin/main when they are the only unpushed work")
    args = ap.parse_args(argv)

    if load_schema_validator() is None:
        if args.commit and not args.dry_run:
            print("error: jsonschema is not installed; refusing to write, commit and push "
                  "unvalidated change records (pip3 install jsonschema)", file=sys.stderr)
            return 1
        print("warning: jsonschema is not installed; records are written unvalidated",
              file=sys.stderr)

    prefix = args.prefix if args.prefix.endswith("/") else args.prefix + "/"
    repo = args.repo or default_repo()
    out_dir = args.out_dir or str(change_records_path())
    result = write_change_records(
        repo, out_dir, since=args.since, ref=args.ref, prefix=prefix,
        target_repo=args.target_repo, dry_run=args.dry_run,
    )
    verb = "would write" if args.dry_run else "written"
    for cid in result["written"]:
        print(f"{verb}: {cid}")
    print(f"{len(result['written'])} written, {len(result['skipped'])} skipped, "
          f"{len(result['ignored'])} ignored, {len(result['errors'])} errors")
    for err in result["errors"]:
        print(f"error: {err}", file=sys.stderr)
    if args.commit and not args.dry_run:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from change_records_commit import commit_change_records
        try:
            # Skipped ids too: records a failed earlier commit left untracked are "skipped" now.
            # Only files that git still sees as pending get committed, so tracked ones are no-ops.
            done = commit_change_records(repo, out_dir, result["written"] + result["skipped"])
        except Exception as exc:
            print(f"error: commit failed: {exc}", file=sys.stderr)
            return 1
        print(f"committed {len(done['files'])} record(s): {done['sha'][:12]}" if done["committed"]
              else "nothing to commit")
        # Push is best effort: a problem is a warning in the output, never a failed run.
        from change_records_push import push_change_records
        print(push_change_records(repo, out_dir)["message"])
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
