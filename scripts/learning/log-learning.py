#!/usr/bin/env python3
"""log-learning.py — validate and atomically append a v2 learning entry to unified-learnings.jsonl.

Reads a partial learning entry as JSON from stdin. Rejects any caller-provided
server-injected fields (schema_version, id, timestamp, dotfiles_sha,
dotfiles_dirty, session_id, attribution_tag), injects them, validates
against learning-schema.json, then atomically appends to
claude-code-shared/learnings/unified-learnings.jsonl.

Usage:
    echo '{...}' | python log-learning.py

Caller supplies all fields EXCEPT: schema_version, id, timestamp, dotfiles_sha,
dotfiles_dirty, session_id, attribution_tag.

Attribution stamps (server-side):
    dotfiles_sha   — HEAD of the dotfiles repo (null if git cannot be read)
    dotfiles_dirty — true when the dotfiles tree has uncommitted changes (null if git cannot be read)
    session_id     — first UUID found in evidence[].ref (null if none)
    attribution_tag — {kind, ref} from tag-attribution.py: regression_of <change_id>,
                      recurrence_of <learning id>, or new_defect. Computed from the
                      validated escape_point, the change records in
                      <dotfiles>/attribution/change-records and the applied
                      rows already in unified-learnings.jsonl. Callers cannot supply it.

Required caller fields (see learning-schema.json for full contract):
    type, reported_by, improves_type, cause, problem, why_missed, lesson,
    fix, evidence, confidence
    Plus: improves (nullable), cause_label (null unless cause=='other'),
    and optionally trace (attribution records) and status.

regression_of verification:
    A regression_of tag is checked against the real diff of the tagged change before it is
    written. load-change-diff.py loads the diff for the escape-point path and section; if the
    section is not edited in it, the tag is downgraded. Otherwise the artifact-grounding-judge
    ("Regression check") rules on the tag. Only a `pass` verdict keeps it. A `rejected`
    verdict, an unloadable diff, or a judge call that fails, times out or returns something
    unparsable downgrades the tag to {kind: new_defect, ref: null}. The learning keeps all its
    other fields. The judge returns only verdict, confidence and reason; this script applies
    the downgrade.

Escape point:
    A caller-supplied escape_point {path, section} is checked by
    validate-escape-point.py before writing. A missing heading nulls the
    section; a nonexistent path drops the escape_point. The learning logs
    either way. A null section cannot later receive a regression_of tag.

Environment:
    LOG_LEARNING_DEST — override the learnings/ directory (used by tests).
    LOG_LEARNING_DOTFILES_DIR — override the repo read for the stamps
        (default ~/.dotfiles; used by tests).
    LOG_LEARNING_JUDGE_CMD — command that runs the regression_of judge: it reads the judge
        prompt on stdin and prints the verdict JSON on stdout (default: claude -p --model
        haiku with no tools and all hooks disabled). Tests point it at a stub so no real model is called.

Exit 0 on success. Exit 1 on any error (printed to stderr).
"""

import fcntl
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONTRACTS_DIR = SCRIPT_DIR.parents[1] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "learning-schema.json"

_dest_override = os.environ.get("LOG_LEARNING_DEST")
LEARNINGS_DIR = pathlib.Path(_dest_override) if _dest_override else SCRIPT_DIR.parents[1] / "learnings"

SERVER_FIELDS = {
    "schema_version", "id", "timestamp",
    "dotfiles_sha", "dotfiles_dirty", "session_id", "attribution_tag",
}

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _dotfiles_dir():
    override = os.environ.get("LOG_LEARNING_DOTFILES_DIR")
    return pathlib.Path(override) if override else pathlib.Path.home() / ".dotfiles"


def _changes_dir():
    """Change-records folder: under LOG_LEARNING_DOTFILES_DIR when set, else the shared helper's."""
    override = os.environ.get("LOG_LEARNING_DOTFILES_DIR")
    if override:
        return pathlib.Path(override) / "attribution" / "change-records"
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shared"))
    from change_records_path import change_records_path
    return change_records_path()


def _git_out(repo, *args):
    """Run a git subcommand against repo; return stdout, or None on any failure."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _dotfiles_stamp():
    """Return (sha, dirty); each is None when git state cannot be read."""
    repo = _dotfiles_dir()
    sha = _git_out(repo, "rev-parse", "HEAD")
    status = _git_out(repo, "status", "--porcelain")
    if sha is None or status is None:
        print(
            f"WARNING: could not read git state of {repo}; "
            "dotfiles_sha/dotfiles_dirty may be null",
            file=sys.stderr,
        )
    return (
        sha.strip() if sha is not None else None,
        bool(status.strip()) if status is not None else None,
    )


def _session_id(entry):
    """First UUID found in an evidence[].ref (lowercased), or None."""
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        ref = item.get("ref") if isinstance(item, dict) else None
        if isinstance(ref, str):
            m = _UUID_RE.search(ref)
            if m:
                return m.group(0).lower()
    return None


def _load_script(filename, modname):
    spec = importlib.util.spec_from_file_location(modname, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _validate_escape_point(proposal):
    """Run validate-escape-point.py's checks; None means drop the escape_point."""
    mod = _load_script("validate-escape-point.py", "validate_escape_point")
    result = mod.validate_escape_point(proposal, _dotfiles_dir())
    if result is None:
        print("WARNING: escape_point dropped (path not found in repo)", file=sys.stderr)
    elif isinstance(proposal, dict) and result["section"] != proposal.get("section"):
        print(
            "WARNING: escape_point section not found in file; section set to null",
            file=sys.stderr,
        )
    return result


def _attribution_tag(entry):
    """Tag the entry via tag-attribution.py; None (with a warning) if tagging fails."""
    try:
        mod = _load_script("tag-attribution.py", "tag_attribution")
        changes_dir = _changes_dir()
        return mod.tag_attribution(
            entry,
            mod.load_changes(changes_dir),
            mod.load_applied(LEARNINGS_DIR / "unified-learnings.jsonl"),
        )
    except Exception as e:
        print(f"WARNING: attribution_tag not set: {e}", file=sys.stderr)
        return None


def _recurrence_fallback(entry):
    """After a rejected regression_of, a recurrence_of tag if the entry repeats an applied learning."""
    try:
        tagger = _load_script("tag-attribution.py", "tag_attribution")
        ref = tagger._recurrence_ref(
            entry, tagger.load_applied(LEARNINGS_DIR / "unified-learnings.jsonl"))
    except Exception as e:
        print(f"WARNING: recurrence_of fallback skipped: {e}", file=sys.stderr)
        return None
    return {"kind": "recurrence_of", "ref": ref} if ref else None


# The judge reads a merged diff, so it runs with no tools (--tools "") and with every hook
# disabled: prompt injection in a diff cannot steer a tool, and the child session cannot
# fire capture hooks of its own. (--bare would also skip hooks but drops OAuth auth.)
DEFAULT_JUDGE_CMD = (
    'claude -p --model haiku --tools "" '
    "--settings '{\"disableAllHooks\": true}'"
)
JUDGE_TIMEOUT_SECONDS = 20  # foreground attribution path waits on this; timeout downgrades to new_defect
JUDGE_DIFF_BYTE_BUDGET = 24000  # section_diff sent to the judge is cut to this many UTF-8 bytes
NEW_DEFECT = {"kind": "new_defect", "ref": None}
JUDGE_FAILURE_DEFECT = {"kind": "new_defect", "ref": None, "downgrade_reason": "judge_failure"}
JUDGE_DOC = SCRIPT_DIR.parents[1] / "agents" / "learning" / "artifact-grounding-judge.md"


def _bounded_change_diff(change_diff):
    """The judge payload's diff: only section_diff, cut to JUDGE_DIFF_BYTE_BUDGET bytes.

    The full per-path `diff` is dropped: no judge decision rule uses it, and a large or newly
    added file would overflow the model context or the judge timeout.
    """
    out = {k: v for k, v in change_diff.items() if k != "diff"}
    sd = out.get("section_diff")
    if isinstance(sd, str):
        raw = sd.encode("utf-8")
        if len(raw) > JUDGE_DIFF_BYTE_BUDGET:
            cut = raw[:JUDGE_DIFF_BYTE_BUDGET].decode("utf-8", errors="ignore")
            out["section_diff"] = cut + f"\n... [truncated: {len(raw)} bytes, kept {JUDGE_DIFF_BYTE_BUDGET}]\n"
            out["section_diff_truncated"] = True
    return out


def _judge_prompt(entry, tag, change_diff):
    """Prompt for the artifact-grounding-judge's regression_of check."""
    doc = JUDGE_DOC.read_text(encoding="utf-8")
    if doc.startswith("---"):
        doc = doc.split("---", 2)[-1]
    payload = {
        "learning": {k: entry.get(k) for k in (
            "improves", "cause", "problem", "why_missed", "lesson", "fix", "escape_point")},
        "attribution_tag": tag,
        "change_diff": _bounded_change_diff(change_diff),
    }
    return (
        f"{doc.strip()}\n\n## Regression check input\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n"
    )


def _parse_verdict(text):
    """The judge's verdict object from its stdout, or None when it cannot be read."""
    text = text.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.extend(reversed(re.findall(r"\{[^{}]*\}", text)))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("verdict") in ("pass", "rejected"):
            return obj
    return None


def _run_judge(prompt):
    """Run the judge command; return its verdict dict, or raise RuntimeError."""
    cmd = shlex.split(os.environ.get("LOG_LEARNING_JUDGE_CMD") or DEFAULT_JUDGE_CMD)
    try:
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                             timeout=JUDGE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"judge call failed: {e}") from e
    if res.returncode != 0:
        raise RuntimeError(f"judge exited {res.returncode}: {res.stderr.strip()[:200]}")
    verdict = _parse_verdict(res.stdout)
    if verdict is None:
        raise RuntimeError("judge returned no readable verdict")
    return verdict


def _verify_regression_tag(entry, tag):
    """Keep a regression_of tag only if the change's real diff grounds it; else new_defect."""
    ep = entry.get("escape_point")
    try:
        helper = _load_script("load-change-diff.py", "load_change_diff")
        tagger = _load_script("tag-attribution.py", "tag_attribution")
        changes_dir = _changes_dir()
        change = next((c for c in tagger.load_changes(changes_dir)
                       if c.get("change_id") == tag["ref"]), None)
        if change is None or not isinstance(ep, dict):
            raise ValueError("change record or escape_point not available")
        diff = helper.load_change_diff(change, ep.get("path"), ep.get("section"), _dotfiles_dir())
    except Exception as e:
        print(f"WARNING: regression_of {tag['ref']} downgraded to new_defect: {e}", file=sys.stderr)
        return dict(NEW_DEFECT)

    try:
        section_edited = diff["section_edited"]
        section = diff["section"]
    except Exception as e:
        print(f"WARNING: regression_of {tag['ref']} downgraded to new_defect: malformed diff ({e!r})",
              file=sys.stderr)
        return dict(NEW_DEFECT)
    if not section_edited:
        print(
            f"WARNING: regression_of {tag['ref']} downgraded to new_defect: "
            f"section {section!r} is not edited in the change's diff",
            file=sys.stderr,
        )
        return dict(NEW_DEFECT)

    try:
        verdict = _run_judge(_judge_prompt(entry, tag, diff))
    except Exception as e:
        # Prompt build (JUDGE_DOC read: OSError/UnicodeDecodeError) or judge call: never lose the learning.
        print(f"WARNING: regression_of {tag['ref']} downgraded to new_defect (judge_failure): {e}",
              file=sys.stderr)
        return dict(JUDGE_FAILURE_DEFECT)
    if verdict["verdict"] != "pass":
        print(
            f"WARNING: regression_of {tag['ref']} downgraded to new_defect: "
            f"judge rejected it: {verdict.get('reason')}",
            file=sys.stderr,
        )
        return dict(NEW_DEFECT)
    return tag


def _build_validator(schema):
    import re
    try:
        from importlib.metadata import version as pkg_version
        v = pkg_version("jsonschema")
    except Exception:
        import jsonschema
        v = jsonschema.__version__
    m = re.match(r"(\d+)\.(\d+)", v)
    major, minor = (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    use_registry = (major > 4) or (major == 4 and minor >= 18)
    if use_registry:
        try:
            from referencing import Registry, Resource
            from referencing.jsonschema import DRAFT7
            from jsonschema import Draft7Validator

            all_schemas = {}
            for sf in sorted(CONTRACTS_DIR.glob("*.json")):
                try:
                    data = json.loads(sf.read_text())
                    if "$id" in data:
                        all_schemas[data["$id"]] = data
                except Exception:
                    pass

            resources = [
                (sid, Resource.from_contents(sdata, default_specification=DRAFT7))
                for sid, sdata in all_schemas.items()
            ]
            registry = Registry().with_resources(resources)
            return Draft7Validator(schema, registry=registry)
        except ImportError:
            pass

    from jsonschema import Draft7Validator, RefResolver
    return Draft7Validator(schema, resolver=RefResolver(
        base_uri=schema.get("$id", ""), referrer=schema, store={}
    ))


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERROR: no input on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(entry, dict):
        print("ERROR: input must be a JSON object", file=sys.stderr)
        sys.exit(1)

    # Reject caller-provided server-injected fields
    caller_server_fields = SERVER_FIELDS & entry.keys()
    if caller_server_fields:
        bad = ", ".join(sorted(caller_server_fields))
        print(
            f"ERROR: caller must not supply server-injected fields: {bad}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Inject server-side fields
    entry["schema_version"] = "2"
    entry["id"] = str(uuid.uuid4())
    entry["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["dotfiles_sha"], entry["dotfiles_dirty"] = _dotfiles_stamp()
    entry["session_id"] = _session_id(entry)

    # Validate a caller-supplied escape_point; the learning logs regardless
    if "escape_point" in entry:
        try:
            validated = _validate_escape_point(entry["escape_point"])
        except Exception as e:
            print(f"WARNING: escape_point dropped (validation failed: {e})", file=sys.stderr)
            validated = None
        if validated is None:
            del entry["escape_point"]
        else:
            entry["escape_point"] = validated

    tag = _attribution_tag(entry)
    if tag is not None and not isinstance(tag, dict):
        print(f"WARNING: attribution tag omitted (tagger returned {type(tag).__name__}, not an object)",
              file=sys.stderr)
        tag = None
    if tag is not None and tag.get("kind") == "regression_of":
        tag = _verify_regression_tag(entry, tag)
        if tag.get("kind") == "new_defect":
            tag = _recurrence_fallback(entry) or tag
    if tag is not None:
        entry["attribution_tag"] = tag

    # Load schema
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as e:
        print(f"ERROR: could not load learning-schema.json: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate
    try:
        validator = _build_validator(schema)
        errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.absolute_path))
    except Exception as e:
        print(f"ERROR: validator setup failed: {e}", file=sys.stderr)
        sys.exit(1)

    if errors:
        for err in errors:
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            print(f"ERROR: validation failed at {path}: {err.message}", file=sys.stderr)
        sys.exit(1)

    # Atomic append: write to temp file then rename (append semantics via lock)
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LEARNINGS_DIR / "unified-learnings.jsonl"
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"

    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError as e:
        print(f"ERROR: could not write to {out_path}: {e}", file=sys.stderr)
        sys.exit(1)

    lesson = " ".join(str(entry.get("lesson", "")).split())
    print(f"OK: appended learning entry (type={entry['type']}, id={entry['id']}) to {out_path} | lesson: {lesson}")


if __name__ == "__main__":
    main()
