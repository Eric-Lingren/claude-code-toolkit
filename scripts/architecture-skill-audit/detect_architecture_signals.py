#!/usr/bin/env python3
"""Detect deterministic architecture signals (A1, A2, A7, A8) in a SKILL.md file.

Returns a JSON array of finding records. Exit 0 always (findings are not errors).

Usage:
  python3 detect-arch-signals.py <skill-md-path> <registry-json-path>
"""
import json
import os
import re
import sys

LOOP_PATTERNS = [
    r"for each\b",
    r"\bqueue\b",
    r"\biterat",
    r"\bper[ -]task\b",
    r"\bper[ -]scenario\b",
    r"\brepeat\b",
    r"\bloop\b",
]

SKILL_INVOKE_PATTERNS = [
    r"invoke\s+it",
    r"invoke\s+the\s+/",
    r"run\s+/[a-z]",
    r"seed\s+`?/[a-z]",
    r"/tdd\b",
    r"/run-tasks\b",
    r"Skill\s+tool",
]

LARGE_READ_PATTERNS = [
    r"full\s+contents",
    r"full\s+diff",
    r"full\s+PRD",
    r"read\s+the\s+(?:full|entire|whole)",
    r"full\s+text\s+output",
]

AGENT_SPAWN_PATTERNS = [
    r"subagent_type",
    r'Agent\s*\(',
    r"spawn\s+(?:an?\s+)?[Aa]gent",
    r"spawn\s+one\s+[Aa]gent",
]


def _read_lines(path):
    with open(path) as f:
        return f.readlines()


def _has_pattern_in_lines(lines, patterns):
    for i, line in enumerate(lines, 1):
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                return True, i
    return False, -1


def _find_loop_context(lines):
    for i, line in enumerate(lines, 1):
        for pat in LOOP_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                return i
    return -1


def _find_pattern_near(lines, patterns, anchor_line, window=30):
    start = max(0, anchor_line - 1)
    end = min(len(lines), anchor_line + window)
    for i, line in enumerate(lines[start:end], start + 1):
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                return i
    return -1


def _make_finding(signal, finding, location, recommendation,
                  proposed_agent=None, consumers=None, benefit=None, effort=None):
    return {
        "signal": signal,
        "finding": finding,
        "location": location,
        "recommendation": recommendation,
        "proposed_agent": proposed_agent,
        "consumers": consumers,
        "benefit": benefit or "",
        "effort": effort or "medium",
    }


def _count_spawn_lines_in_loop(lines, loop_line, window=80):
    """Count agent spawn lines and collect their line numbers within the loop window."""
    start = loop_line - 1
    end = min(len(lines), loop_line + window)
    spawn_lines = []
    for i, line in enumerate(lines[start:end], start + 1):
        for pat in AGENT_SPAWN_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                spawn_lines.append(i)
                break
    return spawn_lines


def _find_max_iterations(lines):
    """Find 'Max iterations: N' or similar and return N, else default 3."""
    for line in lines:
        m = re.search(r"[Mm]ax\s+iterations[:\s]+(\d+)", line)
        if m:
            return int(m.group(1))
    return 3


def _has_schema_near(lines, spawn_line, window=5):
    """Return True if 'schema:' appears within `window` lines of spawn_line."""
    start = max(0, spawn_line - 2)
    end = min(len(lines), spawn_line + window)
    for line in lines[start:end]:
        if re.search(r"\bschema\b", line, re.IGNORECASE):
            return True
    return False


def _extract_step_name(lines, spawn_line):
    """Walk backwards from spawn_line to find the nearest heading (#### / ### / ## line)."""
    for i in range(spawn_line - 2, max(-1, spawn_line - 20), -1):
        if re.match(r"#{2,4}\s+", lines[i]):
            return lines[i].strip().lstrip("#").strip()
    return f"line {spawn_line}"


def detect_deterministic(skill_path, registry):
    """Detect A1, A2, A7, A8 signals. Returns list of finding dicts."""
    findings = []
    lines = _read_lines(skill_path)
    skill_name = os.path.basename(os.path.dirname(skill_path))
    if not skill_name:
        skill_name = os.path.splitext(os.path.basename(skill_path))[0]

    loop_line = _find_loop_context(lines)
    has_loop = loop_line > 0

    if has_loop:
        invoke_line = _find_pattern_near(lines, SKILL_INVOKE_PATTERNS, loop_line)
        if invoke_line > 0:
            findings.append(_make_finding(
                signal="A1",
                finding=(
                    f"Skill invokes another skill inside a loop "
                    f"(loop at line {loop_line}, invoke at line {invoke_line})"
                ),
                location=f"{skill_path}:{invoke_line}",
                recommendation=(
                    "Extract the per-iteration work to an agent. "
                    "The session model should orchestrate; an agent handles each iteration in its own context."
                ),
                benefit="Isolates per-iteration context, prevents N-iteration context accumulation.",
                effort="medium",
            ))

        read_line = _find_pattern_near(lines, LARGE_READ_PATTERNS, loop_line)
        if read_line > 0:
            findings.append(_make_finding(
                signal="A2",
                finding=(
                    f"Full file/diff/PRD contents read per iteration inside loop "
                    f"(loop at line {loop_line}, read at line {read_line})"
                ),
                location=f"{skill_path}:{read_line}",
                recommendation=(
                    "Delegate per-iteration reads to an isolated agent context "
                    "rather than accumulating all content on the session model."
                ),
                benefit="Prevents session context bloat across iterations.",
                effort="medium",
            ))

        # A8: loop with many free-text agent spawns whose output feeds session model
        spawn_line_nums = _count_spawn_lines_in_loop(lines, loop_line)
        max_iter = _find_max_iterations(lines)
        agent_count = len(spawn_line_nums) * max_iter

        if agent_count >= 6:
            free_text_spawns = [
                ln for ln in spawn_line_nums
                if not _has_schema_near(lines, ln)
            ]
            has_downstream_read = read_line > 0 or _find_pattern_near(
                lines, LARGE_READ_PATTERNS, loop_line, window=120
            ) > 0

            if free_text_spawns and has_downstream_read:
                offending_steps = list({_extract_step_name(lines, ln) for ln in free_text_spawns})
                estimated_savings = len(free_text_spawns) * 2000
                findings.append({
                    "signal": "A8",
                    "finding": (
                        f"Loop at line {loop_line} spawns {len(spawn_line_nums)} agents/iteration "
                        f"x {max_iter} iterations = {agent_count} total agent calls; "
                        f"{len(free_text_spawns)} return free-text output consumed by session model."
                    ),
                    "location": f"{skill_path}:{loop_line}",
                    "recommendation": (
                        "Introduce a coordinator agent per iteration that runs all sub-agent calls "
                        "internally and returns only a compact JSON result to the session model."
                    ),
                    "loop_location": f"{skill_path}:{loop_line}",
                    "offending_steps": offending_steps,
                    "coordinator_inputs": [
                        "skill_md_contents",
                        "learnings_md_contents",
                        "eval_json",
                        "scenario_list",
                        "iteration_number",
                    ],
                    "coordinator_return_schema": {
                        "iteration": "integer",
                        "cell_scores": [
                            {"scenario": "string", "assertion": "string", "score": "integer"}
                        ],
                        "failure_reasons": [
                            {"scenario": "string", "assertion": "string",
                             "score": "integer", "reason": "string"}
                        ],
                    },
                    "estimated_tokens_saved_per_iteration": estimated_savings,
                    "proposed_agent": None,
                    "consumers": [skill_name],
                    "benefit": (
                        f"Estimated {estimated_savings} tokens/iteration saved; "
                        "session context no longer accumulates raw agent outputs across iterations."
                    ),
                    "effort": "medium",
                })

    registered_names = {a["name"] for a in registry.get("agents", [])}
    for i, line in enumerate(lines, 1):
        for pat in AGENT_SPAWN_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                name_match = re.search(
                    r'subagent_type["\s:]+(["\']?)([a-z0-9:_-]+)\1', line, re.IGNORECASE
                )
                spawned_name = name_match.group(2) if name_match else None
                builtin_types = {"general-purpose", "claude", "caveman:cavecrew-investigator",
                                 "caveman:cavecrew-builder", "caveman:cavecrew-reviewer",
                                 "Explore", "Plan", "fact-checker", "caveman:cavecrew"}
                if spawned_name and spawned_name not in registered_names and spawned_name not in builtin_types:
                    findings.append(_make_finding(
                        signal="A7",
                        finding=f"Agent spawn site references '{spawned_name}' not in registry.json",
                        location=f"{skill_path}:{i}",
                        recommendation=f"Add '{spawned_name}' to agents/registry.json via /register-skill.",
                        benefit="Keeps registry accurate for cross-skill reuse detection.",
                        effort="low",
                    ))
                elif spawned_name is None:
                    pass
                break

    return findings


def main():
    if len(sys.argv) < 3:
        print("Usage: detect-arch-signals.py <skill-md-path> <registry-json-path>", file=sys.stderr)
        sys.exit(1)

    skill_path = sys.argv[1]
    registry_path = sys.argv[2]

    with open(registry_path) as f:
        registry = json.load(f)

    findings = detect_deterministic(skill_path, registry)
    print(json.dumps(findings, indent=2))


if __name__ == "__main__":
    main()
