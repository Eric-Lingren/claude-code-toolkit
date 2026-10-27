#!/usr/bin/env python3
"""Claude Code usage profiler — aggregates across profiles (office + personal).

Reads session JSONL transcripts and produces benchmark distributions:
  - volume (sessions, turns, projects, date range), split by profile
  - model mix
  - token usage + rough cost
  - thinking ("deep thinking") ratio
  - tool frequency
  - slash command / skill usage
  - git branch work-type mix
  - per-request intent classification (heuristic)
  - per-skill wall-clock latency + parallelism shape (--latency)

Heuristic only. No network, no LLM. Transparent + tunable.

Profiles (Claude Code session stores):
  office   -> ~/.cco/projects   (work)
  personal -> ~/.cch/projects   (home)

Usage:
  cc-usage-bench.py                  # all profiles, aggregated + per-profile split
  cc-usage-bench.py --profile office
  cc-usage-bench.py --profile personal
  cc-usage-bench.py --roots /path/a /path/b   # override roots
  cc-usage-bench.py --latency --min-n 3      # slowest skills + parallelism
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from glob import glob

TIER_CONFIG = os.path.expanduser("~/.dotfiles/claude-code-shared/resources/model-tiers.json")

# profile -> projects root
PROFILES = {
    "office":   os.path.expanduser("~/.cco/projects"),
    "personal": os.path.expanduser("~/.cch/projects"),
}

# ---- rough price per 1M tokens (USD). Adjust as needed. ----
PRICE = {
    "opus":   {"in": 15.0, "out": 75.0, "cache_w": 18.75, "cache_r": 1.50},
    "sonnet": {"in": 3.0,  "out": 15.0, "cache_w": 3.75,  "cache_r": 0.30},
    "haiku":  {"in": 1.0,  "out": 5.0,  "cache_w": 1.25,  "cache_r": 0.10},
}

# ---- weekly digest (snapshot + delta header + terminal nudge) ----
REPORT_DIR = os.path.expanduser(
    os.environ.get("CC_USAGE_REPORT_DIR", "~/.cache/cc-usage-reports"))
SNAPSHOT_PATH = os.path.join(REPORT_DIR, "snapshots.json")
# Adherence pre-dates tiering; weeks before rollout used the session model and
# read as fake-low. None = use all weeks. Pin e.g. "2026-W21" to ignore noise.
ROLLOUT_WEEK = None

def model_family(m):
    if not m or m == "<synthetic>":
        return "synthetic"
    m = m.lower()
    if "opus" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return "other"

def text_of(content):
    """Flatten a message.content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                out.append(c.get("text", ""))
        return "\n".join(out)
    return ""

def is_tool_result_turn(content):
    """A user record that is actually a tool_result, not a human prompt."""
    if isinstance(content, list):
        return any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
    return False

SLASH_RE = re.compile(r"<command-name>\s*/?([\w:-]+)\s*</command-name>")

def slash_cmd(raw_text):
    m = SLASH_RE.search(raw_text)
    return m.group(1).lower() if m else None

NOISE_RE = re.compile(r"<local-command-caveat>|<command-name>\s*/?exit\s*</command-name>|local-command-stdout", re.I)
def is_noise(raw, cmd):
    """Control/system records that are not human work requests."""
    if cmd == "exit":
        return True
    if NOISE_RE.search(raw):
        return True
    return False

ACK_RE = re.compile(r"^(y|n|ya|yo|ok|k|go|yes|yep|yup|nope|no|sure|do it|continue|cont|next|proceed|"
                    r"thanks|thx|ty|great|nice|perfect|good|lgtm|looks good|afk|done|stop|wait|"
                    r"[a-f]|[0-9]{1,3}|[a-f]\s*[\+,&]\s*[a-f]|[0-9]+\s*-\s*[0-9]+)$", re.I)
def is_continuation(raw):
    """Short ack or AskUserQuestion answer (a/b/c, yes, go, 1-5, b+c)."""
    s = raw.strip()
    if len(s) <= 3:
        return True
    if ACK_RE.match(s):
        return True
    return False

QUESTION_RE = re.compile(r"^\s*(can (we|i|you)|could (we|i|you)|will it|will that|does (it|this|that)|"
                         r"do (we|you)|should (we|i)|is it|is that|are (we|you)|why (does|is|do)|"
                         r"what (about|if|is|happens)|how (does|come)|wont|won't|isnt|isn't)\b", re.I)

def branch_prefix(b):
    if not b:
        return "none"
    return b.split("/")[0].lower() if "/" in b else b.lower()

# ---- intent classification ----
# When registering a new skill, add it to the matching CMD set here.
# See register-skill step 2h.
PLAN_CMDS   = {"to-spec", "to-prd", "grill-me", "grill-with-docs", "to-tasks",
               "prototype", "plan", "sprout-seed", "to-seed", "find-work", "pick"}
TEST_CMDS   = {"tdd", "to-e2e-tests"}
REVIEW_CMDS = {"review", "code-review", "pr-code-review", "security-review",
               "caveman-review", "relay", "pr-revise", "frontend-review"}
REFAC_CMDS  = {"improve-component", "improve-codebase-architecture", "simplify",
"improve-skill-benchmarks", "improve-agent-benchmarks", "register-skill", "clean-scaffolding",
               "improve-directory-structure"}
"improve-skill", "improve-skill-learnings", "register-skill",
RESEARCH_CMDS = {"how-to", "deep-research", "tldr-tech", "investigate",
                 "cc-usage-analytics", "standup", "retro"}
RUNTASK_CMDS = {"run-tasks", "run-task-followups", "tasks-to-linear",
                "build-code", "dispatch-tasks"}
BUGFIX_CMDS  = {"debug", "ci-fix"}
GITOPS_CMDS  = {"worktree", "caveman-commit"}
DELEGATE_CMDS = {"offload", "handoff"}

TEST_BASH = re.compile(r"\b(pytest|jest|vitest|playwright|npm (run )?test|yarn test|go test|cargo test|rspec|mocha|tox|unittest)\b", re.I)
GIT_COMMIT_BASH = re.compile(r"\bgit (commit|push|merge|rebase|cherry-pick)\b", re.I)
GIT_BRANCH_BASH = re.compile(r"\bgit (checkout -b|switch -c|branch)\b", re.I)

def classify(prompt, cmd, tools, bash_cmds, thinking_tokens, branch, edits, writes):
    """Return (primary_bucket, set_of_labels)."""
    p = prompt.lower()
    labels = set()

    # command-driven (strong signal)
    if cmd in PLAN_CMDS: labels.add("planning")
    if cmd in TEST_CMDS: labels.add("testing")
    if cmd in REVIEW_CMDS: labels.add("review")
    if cmd in REFAC_CMDS: labels.add("refactor")
    if cmd in RESEARCH_CMDS: labels.add("research")
    if cmd in RUNTASK_CMDS: labels.add("feature-code")
    if cmd in BUGFIX_CMDS: labels.add("bugfix")
    if cmd in GITOPS_CMDS: labels.add("git-ops")
    if cmd in DELEGATE_CMDS: labels.add("feature-code")

    # bash-driven
    if any(TEST_BASH.search(b) for b in bash_cmds): labels.add("testing")
    if any(GIT_COMMIT_BASH.search(b) for b in bash_cmds): labels.add("git-ops")
    if any(GIT_BRANCH_BASH.search(b) for b in bash_cmds): labels.add("git-ops")

    # keyword-driven
    if re.search(r"\b(plan|design|architect|approach|strategy|spec|prd|think through|brainstorm)\b", p): labels.add("planning")
    if re.search(r"\b(test|spec|coverage|e2e|unit test|integration test)\b", p): labels.add("testing")
    if re.search(r"\b(rename|refactor|extract|move|clean ?up|reorganize|simplify|dedupe|consolidate)\b", p): labels.add("refactor")
    if re.search(r"\b(fix|bug|broken|failing|error|crash|regression|not working|doesn't work)\b", p): labels.add("bugfix")
    if re.search(r"\b(add|implement|build|create|feature|new (page|component|endpoint|screen)|make (it|the|a|sure)|change|update|set|use|show|color|wire|hook up|support|enable)\b", p): labels.add("feature-code")
    if re.search(r"\b(review|audit|look over|check my)\b", p): labels.add("review")
    if re.search(r"\b(commit|push|open (a )?pr|pull request|merge)\b", p): labels.add("git-ops")
    if re.search(r"\b(how do i|how to|what is|explain|why does|research|investigate|find out)\b", p): labels.add("research")

    # tool/branch-driven
    bp = branch_prefix(branch)
    if bp in ("feat", "feature"): labels.add("feature-code")
    if bp == "fix": labels.add("bugfix")
    if bp == "spike": labels.add("research")
    if edits + writes >= 3: labels.add("feature-code")

    # deep-thinking proxy
    if thinking_tokens >= 2000: labels.add("planning")

    # discussion: question-form prompt with no work signal
    if not labels and QUESTION_RE.search(prompt):
        labels.add("discussion")

    if not labels:
        labels.add("other")

    # primary = priority order
    for b in ["planning", "review", "testing", "refactor", "bugfix", "git-ops",
              "research", "feature-code", "discussion", "other"]:
        if b in labels:
            return b, labels
    return "other", labels


def new_stats():
    return {
        "sessions": 0,
        "user_prompts": 0,
        "asst_turns": 0,
        "proj": Counter(),
        "models": Counter(),
        "tok": defaultdict(lambda: defaultdict(int)),
        "tools": Counter(),
        "cmds": Counter(),
        "branches": Counter(),
        "thinking_turns": 0,
        "primary": Counter(),
        "labels": Counter(),
        "noise": 0,
        "continuation": 0,
        "dates": [],
    }

def merge(dst, src):
    dst["sessions"] += src["sessions"]
    dst["user_prompts"] += src["user_prompts"]
    dst["asst_turns"] += src["asst_turns"]
    dst["proj"].update(src["proj"])
    dst["models"].update(src["models"])
    for fam, d in src["tok"].items():
        for k, v in d.items():
            dst["tok"][fam][k] += v
    dst["tools"].update(src["tools"])
    dst["cmds"].update(src["cmds"])
    dst["branches"].update(src["branches"])
    dst["thinking_turns"] += src["thinking_turns"]
    dst["primary"].update(src["primary"])
    dst["labels"].update(src["labels"])
    dst["noise"] += src["noise"]
    dst["continuation"] += src["continuation"]
    dst["dates"].extend(src["dates"])


def scan_root(root, S):
    """Scan one projects root into stats dict S. Main sessions only (depth: root/proj/file)."""
    files = sorted(glob(os.path.join(root, "*", "*.jsonl")))
    for f in files:
        S["sessions"] += 1
        proj = os.path.basename(os.path.dirname(f))
        S["proj"][proj] += 1
        try:
            with open(f) as fh:
                recs = [json.loads(l) for l in fh if l.strip()]
        except Exception:
            continue

        cur = None

        def flush(cur):
            if cur is None:
                return
            primary, labels = classify(
                cur["prompt"], cur["cmd"], cur["tools"], cur["bash"],
                cur["think"], cur["branch"], cur["edits"], cur["writes"])
            S["primary"][primary] += 1
            for l in labels:
                S["labels"][l] += 1

        for d in recs:
            t = d.get("type")
            if t == "user":
                if d.get("isSidechain"):
                    continue
                content = d.get("message", {}).get("content")
                if is_tool_result_turn(content):
                    continue
                raw = content if isinstance(content, str) else text_of(content)
                cmd = slash_cmd(raw if isinstance(content, str) else json.dumps(content))
                if is_noise(raw, cmd):
                    cur = None
                    S["noise"] += 1
                    continue
                flush(cur)
                S["user_prompts"] += 1
                if cmd:
                    S["cmds"][cmd] += 1
                branch = d.get("gitBranch")
                S["branches"][branch_prefix(branch)] += 1
                ts = d.get("timestamp", "")
                if ts:
                    S["dates"].append(ts[:10])
                if is_continuation(raw) and not cmd:
                    S["continuation"] += 1
                    cur = None
                    continue
                cur = {"prompt": raw, "cmd": cmd, "tools": Counter(), "bash": [],
                       "think": 0, "branch": branch, "edits": 0, "writes": 0}
            elif t == "assistant":
                if d.get("isSidechain"):
                    continue
                S["asst_turns"] += 1
                m = d.get("message", {})
                fam = model_family(m.get("model"))
                S["models"][fam] += 1
                u = m.get("usage", {}) or {}
                S["tok"][fam]["in"] += u.get("input_tokens", 0)
                S["tok"][fam]["out"] += u.get("output_tokens", 0)
                S["tok"][fam]["cache_w"] += u.get("cache_creation_input_tokens", 0)
                S["tok"][fam]["cache_r"] += u.get("cache_read_input_tokens", 0)
                content = m.get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        ct = c.get("type")
                        if ct == "thinking":
                            if cur is not None:
                                cur["think"] += len(c.get("thinking", "")) // 4
                        elif ct == "tool_use":
                            name = c.get("name", "?")
                            S["tools"][name] += 1
                            if cur is not None:
                                cur["tools"][name] += 1
                                if name == "Edit": cur["edits"] += 1
                                elif name == "Write": cur["writes"] += 1
                                elif name == "Bash":
                                    cmd_str = (c.get("input", {}) or {}).get("command", "")
                                    cur["bash"].append(cmd_str)
                    if any(isinstance(c, dict) and c.get("type") == "thinking" for c in content):
                        S["thinking_turns"] += 1
        flush(cur)


def pct(n, d):
    return f"{(100.0*n/d):5.1f}%" if d else "  0.0%"

def cost_of(tok):
    grand = 0.0
    rows = []
    for fam in ("opus", "sonnet", "haiku"):
        if fam not in tok:
            continue
        d = tok[fam]
        pr = PRICE[fam]
        cost = (d["in"]*pr["in"] + d["out"]*pr["out"]
                + d["cache_w"]*pr["cache_w"] + d["cache_r"]*pr["cache_r"]) / 1e6
        grand += cost
        rows.append((fam, d, cost))
    return rows, grand


def report(title, S, detailed=True):
    print("=" * 64)
    print(title)
    print("=" * 64)
    work_reqs = sum(S["primary"].values())
    if S["dates"]:
        print(f"Date range : {min(S['dates'])} -> {max(S['dates'])}")
    print(f"Sessions   : {S['sessions']}")
    print(f"Human reqs : {S['user_prompts']}  (work={work_reqs}, continuation/ack={S['continuation']})")
    print(f"Noise dropped: {S['noise']}  (/exit, local-command-caveat)")
    print(f"Asst turns : {S['asst_turns']}")
    if S["sessions"]:
        print(f"Reqs/session: {S['user_prompts']/S['sessions']:.1f}")

    if detailed:
        print("\n--- PROJECTS (sessions) ---")
        for p, n in S["proj"].most_common(12):
            print(f"  {n:4d}  {p}")

    print("\n--- MODEL MIX (assistant turns) ---")
    tot_m = sum(S["models"].values())
    for fam, n in S["models"].most_common():
        print(f"  {pct(n,tot_m)}  {fam:10s} {n}")

    print("\n--- TOKENS + ROUGH COST (USD) ---")
    rows, grand = cost_of(S["tok"])
    for fam, d, cost in rows:
        print(f"  {fam:8s} in={d['in']:>10,} out={d['out']:>9,} "
              f"cacheW={d['cache_w']:>11,} cacheR={d['cache_r']:>12,}  ${cost:,.2f}")
    print(f"  {'TOTAL':8s} ~ ${grand:,.2f}")

    print("\n--- DEEP THINKING ---")
    print(f"  {pct(S['thinking_turns'], S['asst_turns'])} of assistant turns had a thinking block ({S['thinking_turns']})")

    if detailed:
        print("\n--- TOP TOOLS ---")
        tot_t = sum(S["tools"].values())
        for name, n in S["tools"].most_common(15):
            print(f"  {pct(n,tot_t)}  {name:34s} {n}")

        print("\n--- SLASH COMMANDS / SKILLS ---")
        for name, n in S["cmds"].most_common(25):
            print(f"  {n:4d}  /{name}")

        print("\n--- GIT BRANCH WORK-TYPE (by prompt) ---")
        tot_b = sum(S["branches"].values())
        for name, n in S["branches"].most_common(12):
            print(f"  {pct(n,tot_b)}  {name:18s} {n}")

    print("\n--- REQUEST INTENT (primary bucket, work requests only) ---")
    tot_p = sum(S["primary"].values())
    for name, n in S["primary"].most_common():
        print(f"  {pct(n,tot_p)}  {name:14s} {n}")

    print("\n--- REQUEST INTENT (all labels, multi-count) ---")
    for name, n in S["labels"].most_common():
        print(f"  {pct(n,tot_p)}  {name:14s} {n}")
    print()


def _iso_week(ts):
    try:
        y, w, _ = datetime.date.fromisoformat(ts[:10]).isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return None


def compute_trend(roots):
    """Weekly model-mix + est cost, bucketed by ISO week. Returns {week: W}."""
    weeks = {}
    for name, root in roots.items():
        for f in sorted(glob(os.path.join(root, "*", "*.jsonl"))):
            try:
                recs = [json.loads(l) for l in open(f) if l.strip()]
            except Exception:
                continue
            for d in recs:
                if d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                wk = _iso_week(d.get("timestamp", ""))
                if not wk:
                    continue
                m = d.get("message", {})
                fam = model_family(m.get("model"))
                if fam not in ("opus", "sonnet", "haiku"):
                    continue
                W = weeks.setdefault(wk, {"turns": Counter(),
                                          "tok": defaultdict(lambda: defaultdict(int))})
                W["turns"][fam] += 1
                u = m.get("usage", {}) or {}
                W["tok"][fam]["in"] += u.get("input_tokens", 0)
                W["tok"][fam]["out"] += u.get("output_tokens", 0)
                W["tok"][fam]["cache_w"] += u.get("cache_creation_input_tokens", 0)
                W["tok"][fam]["cache_r"] += u.get("cache_read_input_tokens", 0)
    return weeks


def week_cost(W):
    _, cost = cost_of(W["tok"])
    return cost


def week_opus_pct(W):
    t = W["turns"]
    tot = sum(t.values()) or 1
    return 100 * t["opus"] // tot


def print_trend(weeks):
    print("=" * 64)
    print("WEEKLY MODEL TREND (assistant turns + est cost)")
    print("=" * 64)
    print(f"  {'week':9s} {'turns':>6} {'opus%':>6} {'son%':>6} {'hai%':>6} {'est$':>9}")
    for wk in sorted(weeks):
        W = weeks[wk]
        t = W["turns"]
        tot = sum(t.values()) or 1
        print(f"  {wk:9s} {tot:6d} {100*t['opus']//tot:5d}% "
              f"{100*t['sonnet']//tot:5d}% {100*t['haiku']//tot:5d}% ${week_cost(W):8,.0f}")
    print()


def trend_report(roots):
    print_trend(compute_trend(roots))


SKILL_BODY_RE = re.compile(r"skills/([a-z0-9-]+)/SKILL\.md", re.I)

def detect_skill(raw, cmd):
    if cmd:
        return cmd
    m = SKILL_BODY_RE.search(raw)
    return m.group(1) if m else None


def compute_adherence(roots, config_path):
    """Per-skill expected (config) vs actual model, bucketed by ISO week.

    Returns (expected, per_week) where per_week[week][skill] = Counter(actual
    dominant model per invocation). The invocation's week is the ISO week of the
    user prompt that launched the skill.
    """
    cfg = json.load(open(config_path))
    tiers, assign = cfg["tiers"], cfg["skills"]
    expected = {s: tiers[t]["model"] for s, t in assign.items()}
    expected.update({a: tiers[t]["model"] for a, t in cfg.get("agents", {}).items()})
    per_week = defaultdict(lambda: defaultdict(Counter))

    for name, root in roots.items():
        for f in sorted(glob(os.path.join(root, "*", "*.jsonl"))):
            try:
                recs = [json.loads(l) for l in open(f) if l.strip()]
            except Exception:
                continue
            cur = None

            def flush(cur):
                if cur is None or not cur["skill"] or not cur["models"]:
                    return
                if cur["skill"] not in expected or not cur["week"]:
                    return
                dom = cur["models"].most_common(1)[0][0]
                per_week[cur["week"]][cur["skill"]][dom] += 1

            for d in recs:
                t = d.get("type")
                if t == "user":
                    if d.get("isSidechain"):
                        continue
                    c = d.get("message", {}).get("content")
                    if is_tool_result_turn(c):
                        continue
                    raw = c if isinstance(c, str) else text_of(c)
                    cmd = slash_cmd(raw if isinstance(c, str) else json.dumps(c))
                    if is_noise(raw, cmd):
                        cur = None
                        continue
                    flush(cur)
                    cur = {"skill": detect_skill(raw, cmd), "models": Counter(),
                           "week": _iso_week(d.get("timestamp", ""))}
                elif t == "assistant":
                    if d.get("isSidechain") or cur is None:
                        continue
                    fam = model_family(d.get("message", {}).get("model"))
                    if fam in ("opus", "sonnet", "haiku"):
                        cur["models"][fam] += 1
            flush(cur)

    return expected, per_week


def adherence_for_week(expected, per_week, wk):
    """Adherence summary for one ISO week: overall %, match/n, per-skill mix."""
    skills = per_week.get(wk, {})
    match = n = 0
    out = {}
    for s, c in skills.items():
        cn = sum(c.values())
        match += c.get(expected[s], 0)
        n += cn
        out[s] = {"exp": expected[s], "mix": dict(c)}
    return {"overall_pct": round(100.0 * match / n, 1) if n else None,
            "match": match, "n": n, "skills": out}


def print_adherence(expected, per_week):
    # all-time view = sum across weeks
    per = defaultdict(Counter)
    for wk, skills in per_week.items():
        for s, c in skills.items():
            per[s].update(c)

    print("=" * 64)
    print("TIER ADHERENCE (skills + agents: expected vs actual model)")
    print("=" * 64)
    print("  Only meaningful for sessions AFTER tiering rollout (older runs used the session model).")
    print(f"  {'skill':32s} {'exp':7s} {'match':>6} {'n':>5}  actual-mix")
    tot_match = tot = 0
    for s in sorted(expected):
        c = per.get(s)
        if not c:
            continue
        n = sum(c.values())
        match = c.get(expected[s], 0)
        tot_match += match
        tot += n
        mix = ", ".join(f"{k}:{v}" for k, v in c.most_common())
        print(f"  {s:32s} {expected[s]:7s} {pct(match,n).strip():>6} {n:5d}  {mix}")
    print(f"\n  OVERALL adherence: {pct(tot_match,tot).strip()} ({tot_match}/{tot})")
    print()


def adherence_report(roots, config_path):
    try:
        expected, per_week = compute_adherence(roots, config_path)
    except Exception as e:
        print(f"cannot read tier config {config_path}: {e}", file=sys.stderr)
        return
    print_adherence(expected, per_week)


# ---------------------------------------------------------------------------
# WEEKLY DIGEST: snapshot persistence + delta header (adherence-centric)
# ---------------------------------------------------------------------------

def load_snapshot():
    try:
        return json.load(open(SNAPSHOT_PATH))
    except Exception:
        return {}


def write_snapshot(expected, per_week):
    """Backfill every week present in the logs. Idempotent: re-run overwrites
    the week's entry, never duplicates."""
    snap = load_snapshot()
    for wk in per_week:
        snap[wk] = {"adherence": adherence_for_week(expected, per_week, wk)}
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as fh:
        json.dump(snap, fh, indent=2, sort_keys=True)
    return snap


def current_iso_week():
    y, w, _ = datetime.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def _eligible_weeks(weeks, current):
    """Complete weeks (strictly before the current, partial week), honoring
    the optional ROLLOUT_WEEK floor. Sorted ascending."""
    ws = [w for w in weeks if w < current]
    if ROLLOUT_WEEK:
        ws = [w for w in ws if w >= ROLLOUT_WEEK]
    return sorted(ws)


def drift_weeks(snap, skill, this_wk):
    """Consecutive weeks (counting back from this_wk) the skill drifted.
    Breaks at the first clean OR absent week. Honors ROLLOUT_WEEK floor."""
    wks = sorted((w for w in snap if w <= this_wk
                  and (not ROLLOUT_WEEK or w >= ROLLOUT_WEEK)), reverse=True)
    streak = 0
    for w in wks:
        sk = snap[w]["adherence"]["skills"].get(skill)
        if not sk:
            break
        if any(m != sk["exp"] for m in sk["mix"]):
            streak += 1
        else:
            break
    return streak


def action_for(exp):
    if exp == "haiku":
        return "T1 ran pricier -> check launch path / hook"
    if exp == "sonnet":
        return "re-tier up or add model override"
    if exp == "opus":
        return "ran cheaper -> check model override / hook"
    return "review"


def _delta_pct(cur, prev):
    if prev in (None, 0):
        return "n/a"
    d = 100.0 * (cur - prev) / prev
    arrow = "^" if d > 0 else ("v" if d < 0 else "=")
    return f"{arrow}{abs(d):.0f}%"


def _delta_pt(cur, prev):
    if cur is None or prev is None:
        return "n/a"
    d = cur - prev
    arrow = "^" if d > 0 else ("v" if d < 0 else "=")
    return f"{arrow}{abs(d):.0f}pt"


def digest_report(roots, config_path):
    """TL;DR header: spend/opus from trend, adherence/drift from snapshot.
    Writes the snapshot (backfill all weeks) as a side effect."""
    weeks = compute_trend(roots)
    try:
        expected, per_week = compute_adherence(roots, config_path)
    except Exception as e:
        print(f"cannot read tier config {config_path}: {e}", file=sys.stderr)
        return
    snap = write_snapshot(expected, per_week)
    cur = current_iso_week()

    tw = _eligible_weeks(weeks, cur)
    this_wk = tw[-1] if tw else None
    prev_wk = tw[-2] if len(tw) >= 2 else None

    print("=" * 64)
    if not this_wk:
        print("CC USAGE DIGEST -- no complete week of data yet")
        print("=" * 64)
        print()
        return
    hdr = f"CC USAGE DIGEST -- {this_wk}"
    hdr += f" (vs {prev_wk}, last complete weeks)" if prev_wk else " (baseline, no prior week)"
    print(hdr)
    print("=" * 64)

    # spend + opus% from trend (raw logs, ISO-week bucketed)
    Wc = weeks[this_wk]
    cost_c, opus_c = week_cost(Wc), week_opus_pct(Wc)
    cost_p = opus_p = None
    if prev_wk:
        Wp = weeks[prev_wk]
        cost_p, opus_p = week_cost(Wp), week_opus_pct(Wp)
    print(f"  spend    ${cost_c:>7,.0f}   {_delta_pct(cost_c, cost_p)}")
    print(f"  opus%    {opus_c:>6d}%   {_delta_pt(opus_c, opus_p)}")

    # adherence from snapshot (THE signal)
    a_c = snap[this_wk]["adherence"]
    a_p = snap[prev_wk]["adherence"] if (prev_wk and prev_wk in snap) else None
    ac = a_c["overall_pct"]
    ap = a_p["overall_pct"] if a_p else None
    ac_s = f"{ac:.0f}%" if ac is not None else "n/a"
    print(f"  adher.   {ac_s:>7}   {_delta_pt(ac, ap)}   ({a_c['match']}/{a_c['n']})")
    print()

    # drift list: skills with any off-tier invocation this week
    drift = []
    for s, info in a_c["skills"].items():
        exp, mix = info["exp"], info["mix"]
        off = sum(v for m, v in mix.items() if m != exp)
        if off == 0:
            continue
        drift.append((s, exp, mix, off, sum(mix.values()), drift_weeks(snap, s, this_wk)))

    if not drift:
        print("  DRIFT: none -- all skills on-tier this week.")
        print()
        return

    def line(d):
        s, exp, mix, off, n, streak = d
        actual = ",".join(f"{m}:{v}" for m, v in
                          sorted(mix.items(), key=lambda kv: -kv[1]) if m != exp)
        return (f"  {s:26s} exp {exp:6s} -> {actual:13s} "
                f"{off}/{n}  {streak}wk  -> {action_for(exp)}")

    act = [d for d in drift if d[5] >= 2 or d[3] >= 3]
    minor = [d for d in drift if d not in act]
    act.sort(key=lambda d: (-d[5], -d[3]))
    minor.sort(key=lambda d: (-d[5], -d[3]))
    if act:
        print("  DRIFT (act):")
        for d in act:
            print(line(d))
    if minor:
        print("  DRIFT (minor / watch):")
        for d in minor:
            print(line(d))
    print()


# ---------------------------------------------------------------------------
# latency: per-skill wall-clock + parallelism shape
# ---------------------------------------------------------------------------
# Transcripts carry no tool durations, so timing comes from record timestamps.
# Per invocation we report:
#   work_s - sum of gaps between consecutive records, each gap capped at
#            IDLE_CAP. The cap discards human away-from-keyboard time while
#            keeping agent wait time, which is the part worth optimising.
# Subagent turns are absent from these files (no isSidechain records), so an
# agent's cost shows up only as the gap its spawn leaves on the main thread.
# A skill invoked by another skill is folded into the outer skill's span, so
# these are entry-point totals, not per-skill self time.
IDLE_CAP = 300.0

ASYNC_RESULT_RE = re.compile(r"Async agent launched", re.I)
# User-role records that are machinery, not a human request. A background
# agent finishing arrives as <task-notification>; treating either of these as
# a new prompt would chop a span into one-turn fragments.
NON_PROMPT_RE = re.compile(r"<task-notification>|<system-reminder>", re.I)
SKILLS_DIR = os.path.expanduser("~/.dotfiles/claude-code-shared/skills")


def known_skills(config_path):
    """Real skill names, so /model, /effort and friends do not pollute the table."""
    names = set()
    try:
        cfg = json.load(open(config_path))
        names |= set(cfg.get("skills", {}))
    except Exception:
        pass
    try:
        names |= {d for d in os.listdir(SKILLS_DIR)
                  if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md"))}
    except Exception:
        pass
    return names


def _ts(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _gap(a, b, cap):
    ta, tb = _ts(a), _ts(b)
    if not ta or not tb:
        return 0.0
    return min(max((tb - ta).total_seconds(), 0.0), cap)


def _skill_tool_use(msg):
    for c in (msg.get("content") or []):
        if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill":
            return str((c.get("input") or {}).get("skill", "")).lower() or None
    return None


def _result_is_async(c):
    cc = c.get("content")
    txt = cc if isinstance(cc, str) else json.dumps(cc)
    return bool(ASYNC_RESULT_RE.search(txt))


def _new_span(skill, t0, prev):
    # One assistant message is written to the transcript as one record per
    # content block, so a batched spawn looks like N records sharing a
    # message.id. Turns and hops are counted over ids, not records, or every
    # batch would read as N serial steps.
    return {"skill": skill, "t0": t0, "prev": prev, "week": _iso_week(t0),
            "work": 0.0, "tools": 0, "agents": 0, "agents_async": 0,
            "agents_done": 0, "out": 0, "models": Counter(),
            "turn_ids": set(), "hop_ids": set()}


def compute_latency(roots, config_path, idle_cap=IDLE_CAP):
    """Per-skill invocation spans. Returns {skill: [span, ...]}."""
    known = known_skills(config_path)
    spans = defaultdict(list)

    for name, root in roots.items():
        for f in sorted(glob(os.path.join(root, "*", "*.jsonl"))):
            try:
                recs = [json.loads(l) for l in open(f) if l.strip()]
            except Exception:
                continue
            recs = [d for d in recs if d.get("timestamp") and not d.get("isSidechain")]
            recs.sort(key=lambda d: d["timestamp"])
            cur = None
            last_human = None     # most recent human keystroke, caveat rows included
            pending = set()       # open Agent tool_use ids

            def flush(cur):
                if cur is not None and cur["work"] > 0:
                    cur["turns"] = len(cur["turn_ids"])
                    cur["agent_hops"] = len(cur["hop_ids"])
                    spans[cur["skill"]].append(cur)

            for d in recs:
                now = d["timestamp"]
                if cur is not None:
                    cur["work"] += _gap(cur["prev"], now, idle_cap)
                    cur["prev"] = now
                t = d.get("type")

                if t == "user":
                    if d.get("isMeta"):
                        continue
                    c = d.get("message", {}).get("content")
                    if is_tool_result_turn(c):
                        for x in (c or []):
                            if isinstance(x, dict) and x.get("type") == "tool_result" \
                               and x.get("tool_use_id") in pending:
                                pending.discard(x["tool_use_id"])
                                if cur is not None and _result_is_async(x):
                                    cur["agents_async"] += 1
                        continue
                    raw = c if isinstance(c, str) else text_of(c)
                    cmd = slash_cmd(raw if isinstance(c, str) else json.dumps(c))
                    if NON_PROMPT_RE.search(raw):
                        if cur is not None and "<task-notification>" in raw.lower():
                            cur["agents_done"] += 1
                        continue
                    last_human = now
                    # A slash-command echo is wrapped in a caveat block. It is
                    # still a keystroke (so it anchors t0) but never starts a
                    # span on its own; the Skill tool_use that follows does.
                    if is_noise(raw, cmd):
                        continue
                    # Short acks ("y", "go", "b") are HITL gates inside a run,
                    # not a new request. They keep the current span open.
                    if is_continuation(raw) and cur is not None and not cmd:
                        continue
                    flush(cur)
                    cur = None
                    sk = detect_skill(raw, cmd)
                    if sk in known:
                        cur = _new_span(sk, now, now)

                elif t == "assistant":
                    m = d.get("message", {})
                    if cur is None:
                        sk = _skill_tool_use(m)
                        if sk in known:
                            t0 = last_human or now
                            cur = _new_span(sk, t0, now)
                            cur["work"] = _gap(t0, now, idle_cap)
                    if cur is None:
                        continue
                    mid = m.get("id") or d.get("uuid")
                    first_block = mid not in cur["turn_ids"]
                    cur["turn_ids"].add(mid)
                    fam = model_family(m.get("model"))
                    if fam in ("opus", "sonnet", "haiku"):
                        cur["models"][fam] += 1
                    if first_block:
                        cur["out"] += (m.get("usage") or {}).get("output_tokens", 0) or 0
                    for c in (m.get("content") or []):
                        if not isinstance(c, dict) or c.get("type") != "tool_use":
                            continue
                        cur["tools"] += 1
                        if c.get("name") == "Agent":
                            cur["agents"] += 1
                            cur["hop_ids"].add(mid)
                            pending.add(c.get("id"))
            flush(cur)

    return spans


def _med(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _p90(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * 0.9))] if v else 0.0


def print_latency(spans, min_n=1, idle_cap=IDLE_CAP):
    rows = [(k, v) for k, v in spans.items() if len(v) >= min_n]
    if not rows:
        print("  no skill invocations found")
        return
    rows.sort(key=lambda kv: -_med([s["work"] for s in kv[1]]))

    print("=" * 78)
    print(f"SKILL LATENCY  (work_s drops human idle; each gap capped at {int(idle_cap)}s)")
    print("=" * 78)
    print(f"  {'skill':26s} {'n':>4} {'med_s':>7} {'p90_s':>7} {'turns':>6} "
          f"{'tools':>6} {'agents':>7} {'out_tok':>8}")
    for k, v in rows:
        w = [s["work"] for s in v]
        print(f"  {k:26s} {len(v):4d} {_med(w):7.0f} {_p90(w):7.0f} "
              f"{_med([s['turns'] for s in v]):6.0f} "
              f"{_med([s['tools'] for s in v]):6.0f} "
              f"{_med([s['agents'] for s in v]):7.0f} "
              f"{_med([s['out'] for s in v]):8.0f}")
    print()

    # fanout = agents launched per spawning turn. 1.0 means each agent got its
    # own turn (serial). Higher means they were batched into one message.
    par = [(k, v) for k, v in rows if sum(s["agents"] for s in v)]
    if par:
        print("=" * 78)
        print("PARALLELISM  (hops = turns that spawned agents = serial depth)")
        print("=" * 78)
        print(f"  {'skill':26s} {'agents':>7} {'hops':>6} {'fanout':>7} {'async%':>7}  verdict")
        for k, v in par:
            ag = sum(s["agents"] for s in v)
            hp = sum(s["agent_hops"] for s in v)
            asy = sum(s["agents_async"] for s in v)
            fo = ag / hp if hp else 0.0
            print(f"  {k:26s} {ag:7d} {hp:6d} {fo:7.2f} {pct(asy, ag).strip():>7}  "
                  f"{'serial: batch these' if fo < 1.5 else 'batched'}")
        print()

    # Skills burning many turns with no delegation are the extraction targets.
    solo = [(k, v) for k, v in rows
            if not sum(s["agents"] for s in v) and _med([s["turns"] for s in v]) >= 10]
    if solo:
        print("=" * 78)
        print("NO DELEGATION  (>=10 median turns, zero agents spawned)")
        print("=" * 78)
        for k, v in solo:
            print(f"  {k:26s} {len(v):4d} runs   {_med([s['turns'] for s in v]):3.0f} turns   "
                  f"{_med([s['work'] for s in v]):5.0f}s median")
        print()


def latency_report(roots, config_path, min_n=1, idle_cap=IDLE_CAP):
    print_latency(compute_latency(roots, config_path, idle_cap), min_n, idle_cap)


def main():
    ap = argparse.ArgumentParser(description="Claude Code usage benchmark across profiles.")
    ap.add_argument("--profile", choices=list(PROFILES) + ["all"], default="all",
                    help="which session profile to scan (default: all)")
    ap.add_argument("--roots", nargs="+", help="override: explicit projects roots to scan")
    ap.add_argument("--trend", action="store_true",
                    help="weekly model-mix + est cost trend (shows the shift over time)")
    ap.add_argument("--adherence", action="store_true",
                    help="per-skill expected (config) vs actual model used")
    ap.add_argument("--latency", action="store_true",
                    help="per-skill wall-clock latency + parallelism shape")
    ap.add_argument("--min-n", type=int, default=1,
                    help="--latency: drop skills with fewer than N invocations")
    ap.add_argument("--idle-cap", type=float, default=IDLE_CAP,
                    help="--latency: per-gap cap in seconds used to drop human idle")
    ap.add_argument("--digest", action="store_true",
                    help="TL;DR delta header (writes weekly snapshot as a side effect)")
    ap.add_argument("--config", default=TIER_CONFIG, help="path to model-tiers.json")
    args = ap.parse_args()

    if args.roots:
        roots = {f"root{i}": r for i, r in enumerate(args.roots)}
    elif args.profile == "all":
        roots = PROFILES
    else:
        roots = {args.profile: PROFILES[args.profile]}

    if args.digest:
        digest_report(roots, args.config)
        return
    if args.trend:
        trend_report(roots)
        return
    if args.adherence:
        adherence_report(roots, args.config)
        return
    if args.latency:
        latency_report(roots, args.config, args.min_n, args.idle_cap)
        return

    per = {}
    agg = new_stats()
    for name, root in roots.items():
        if not os.path.isdir(root):
            print(f"[skip] {name}: no dir at {root}", file=sys.stderr)
            continue
        S = new_stats()
        scan_root(root, S)
        per[name] = S
        merge(agg, S)

    # per-profile reports
    for name, S in per.items():
        report(f"PROFILE: {name.upper()}  ({roots[name]})", S, detailed=True)

    # aggregate (only if >1 profile scanned)
    if len(per) > 1:
        report("AGGREGATE (all profiles combined)", agg, detailed=True)

        # compact side-by-side intent split
        print("=" * 64)
        print("INTENT SPLIT BY PROFILE (primary %)")
        print("=" * 64)
        names = list(per)
        buckets = ["planning", "feature-code", "testing", "bugfix", "review",
                   "refactor", "research", "git-ops", "discussion", "other"]
        hdr = "  " + "bucket".ljust(14) + "".join(n.ljust(12) for n in names) + "AGG"
        print(hdr)
        for b in buckets:
            row = "  " + b.ljust(14)
            for n in names:
                tp = sum(per[n]["primary"].values())
                row += pct(per[n]["primary"].get(b, 0), tp).strip().ljust(12)
            ta = sum(agg["primary"].values())
            row += pct(agg["primary"].get(b, 0), ta).strip()
            print(row)
        print()

if __name__ == "__main__":
    main()
