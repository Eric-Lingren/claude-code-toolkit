#!/usr/bin/env python3
"""
gen-architecture.py -- Regenerate architecture_map.html from the live directory.

Scans claude-code-shared/ for skills, agents, hooks, scripts, resources.
Reads model-tiers.json and registry.json for tier assignments.
Writes a complete architecture_map.html with the same visual style and pan/zoom.

Usage:
  python3 gen-architecture.py              # regenerate architecture_map.html
  python3 gen-architecture.py --check      # report unclassified items, exit 1 if any
  python3 gen-architecture.py --out /path  # write to a different file
"""

import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]  # claude-code-shared/
OUT_DEFAULT = BASE / "architecture_map.html"

# ─── GROUP CONFIG ───────────────────────────────────────────────────────────
# Each item maps to a group. Items not listed here trigger --check warnings.
# Skills, agents, hooks are classified separately.

SKILL_GROUPS = {
    "ENTRY": ["grill-me", "grill-with-docs", "debug", "ci-fix", "pr-code-review"],
    "DISTILL": ["to-seed"],
    "INVESTIGATE": ["investigate", "pr-revise"],
    "PLAN": ["to-spec", "to-tasks", "to-e2e-tasks", "prototype", "sprout-seed"],
    "EXECUTE": ["build-code", "tdd"],
    "EXPORT": ["dispatch-tasks", "tasks-to-linear", "relay"],
    "IMPROVE": [
        "improve-skill-benchmarks", "improve-agent-benchmarks", "improve-component", "register-skill",
        "improve-codebase-architecture", "improve-directory-structure",
    ],
    "UTILITY": [
        "find-work", "clean-scaffolding", "handoff", "offload",
        "how-to", "tldr-tech", "worktree", "cc-usage-analytics",
        "run-task-followups",
    ],
}

AGENT_GROUPS = {
    "DISTILL": [
        "persona-grounding", "persona-accuracy",
        "persona-completeness", "persona-coherence", "persona-judge",
    ],
    "INVESTIGATE": [
        "investigator", "investigator-code", "investigator-web",
        "investigator-github", "investigator-linear", "investigator-notion",
        "answer-composer", "pr-code-review",
    ],
    "PLAN": ["context-loader", "to-tasks"],
    "EXECUTE": ["build-runner", "build-code", "lint-runner", "test-runner", "browser-checker", "e2e-runner"],
    "EXPORT": [
        "export-tasks", "export-tasks-github", "export-tasks-linear",
        "export-tasks-notion", "post-github", "post-linear", "post-slack",
    ],
    "IMPROVE": ["architecture-auditor"],
    "LEARN": [
        "capture-learning",
        "attribution-tracer", "artifact-grounding-judge",
    ],
}

HOOK_NAMES = [
    "block-data-exfil", "block-destructive-fs", "block-destructive-git",
    "block-destructive-sed", "block-destructive-sql", "block-remote-exec",
    "block-package-publish", "block-edit-on-trunk", "block-var-keyword",
    "lint-edited-file", "tier-advisor", "stop-hook", "test-hook",
]


# ─── DISCOVERY ──────────────────────────────────────────────────────────────

def discover_skills():
    d = BASE / "skills"
    if not d.is_dir():
        return []
    return sorted(
        p.name for p in d.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def discover_agents():
    d = BASE / "agents"
    if not d.is_dir():
        return []
    names = []
    for p in d.rglob("*.md"):
        name = p.stem
        if p.name == "README.md":
            continue
        if name not in names:
            names.append(name)
    return sorted(names)


def discover_hooks():
    d = BASE / "hooks"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def discover_scripts():
    d = BASE / "scripts"
    if not d.is_dir():
        return []
    return sorted(
        p.name for p in d.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name != "gen-architecture.py"
    )


def discover_resources():
    d = BASE / "resources"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def load_tiers():
    p = BASE / "resources" / "model-tiers.json"
    if not p.exists():
        return {}, {}
    data = json.loads(p.read_text())
    return data.get("skills", {}), data.get("agents", {})


def tier_to_letter(tier_str, tiers_def=None):
    if tiers_def is None:
        tiers_def = {"T1": "H", "T2": "S", "T3": "S", "T4": "O"}
    mapping = {"T1": "H", "T2": "S", "T3": "S", "T4": "O"}
    return mapping.get(tier_str, "S")


def classify_items(items, group_map):
    classified = set()
    for members in group_map.values():
        classified.update(members)
    unclassified = [i for i in items if i not in classified]
    return unclassified


# ─── CHECK MODE ─────────────────────────────────────────────────────────────

def run_check():
    skills = discover_skills()
    agents = discover_agents()
    hooks = discover_hooks()

    all_skill_classified = set()
    for members in SKILL_GROUPS.values():
        all_skill_classified.update(members)

    all_agent_classified = set()
    for members in AGENT_GROUPS.values():
        all_agent_classified.update(members)

    problems = []

    for s in skills:
        if s not in all_skill_classified:
            problems.append(f"  SKILL unclassified: {s}")

    for a in agents:
        if a not in all_agent_classified:
            problems.append(f"  AGENT unclassified: {a}")

    for h in hooks:
        if h not in HOOK_NAMES:
            problems.append(f"  HOOK unclassified: {h}")

    # Check for stale entries (in config but not on disk)
    for g, members in SKILL_GROUPS.items():
        for m in members:
            if m not in skills:
                problems.append(f"  SKILL stale in {g}: {m} (not on disk)")

    for g, members in AGENT_GROUPS.items():
        for m in members:
            if m not in agents:
                problems.append(f"  AGENT stale in {g}: {m} (not on disk)")

    if problems:
        print("gen-architecture: classification issues found:")
        for p in problems:
            print(p)
        return 1

    print(f"gen-architecture: OK ({len(skills)} skills, {len(agents)} agents, {len(hooks)} hooks)")
    return 0


# ─── SVG GENERATION ────────────────────────────────────────────────────────

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def skill_box(x, y, name, w=130, h=28):
    cx = x + w / 2
    cy = y + h / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" class="sk"/>'
        f'<text x="{cx}" y="{cy}" class="sn">{esc(name)}</text>'
    )


def agent_box(x, y, name, tier="S", w=150, h=26):
    cx = x + w / 2
    cy = y + h / 2
    badge_cls = {"H": "bH", "S": "bS", "O": "bO"}.get(tier, "bS")
    text_cls = {"H": "btH", "S": "btS", "O": "btO"}.get(tier, "btS")
    bx = x + w - 35
    by = y + (h - 14) / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" class="ag"/>'
        f'<text x="{cx}" y="{cy}" class="an">{esc(name)}</text>'
        f'<rect x="{bx}" y="{by}" width="22" height="14" rx="3" class="{badge_cls}"/>'
        f'<text x="{bx + 11}" y="{by + 8}" class="bt {text_cls}">{tier}</text>'
    )


def hook_box(x, y, name, w=140, h=22):
    cx = x + w / 2
    cy = y + h / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" class="hk"/>'
        f'<text x="{cx}" y="{cy}" class="hn">{esc(name)}</text>'
    )


def script_box(x, y, name, w=130, h=20):
    cx = x + w / 2
    cy = y + h / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
        f'fill="var(--bg-s)" stroke="var(--bdr)" stroke-width="1"/>'
        f'<text x="{cx}" y="{cy}" class="hn">{esc(name)}</text>'
    )


def resource_box(x, y, name, w=140, h=20):
    cx = x + w / 2
    cy = y + h / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
        f'fill="var(--pur-d)" stroke="var(--pur)" stroke-width="1"/>'
        f'<text x="{cx}" y="{cy}" class="hn" style="fill:var(--pur)">{esc(name)}</text>'
    )


def contract_box(x, y, name, w=150, h=28):
    cx = x + w / 2
    cy = y + h / 2
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" class="ct"/>'
        f'<text x="{cx}" y="{cy}" class="cn">{esc(name)}</text>'
    )


def flow_row(items, x_start, y, box_fn, gap=10, **kw):
    """Lay out items in a row. Return (svg_parts, next_x)."""
    parts = []
    cx = x_start
    for item in items:
        w = kw.get("w", None)
        if w is None:
            w = max(len(item) * 8.5 + 30, 100)
        parts.append(box_fn(cx, y, item, w=w, **{k: v for k, v in kw.items() if k != "w"}))
        cx += w + gap
    return parts, cx


def flow_grid(items, x_start, y_start, box_fn, cols=4, gap_x=10, gap_y=8, **kw):
    """Lay out items in a grid. Return (svg_parts, max_y)."""
    parts = []
    max_y = y_start
    for i, item in enumerate(items):
        row = i // cols
        col = i % cols
        w = kw.get("w", max(len(item) * 8.5 + 30, 100))
        h = kw.get("h", 28)
        x = x_start + col * (w + gap_x)
        y = y_start + row * (h + gap_y)
        parts.append(box_fn(x, y, item, **kw))
        max_y = max(max_y, y + h)
    return parts, max_y


# ─── GROUP RENDERERS ────────────────────────────────────────────────────────
# Special groups (ENTRY+DISTILL, INVESTIGATE, EXECUTE) use bespoke layouts.
# Generic groups (PLAN, EXPORT, IMPROVE, UTILITY) use auto-layout.

def render_entry_distill(skill_tiers, agent_tiers):
    """Row 1, Left: ENTRY POINTS + DISTILL + VERIFICATION PANEL"""
    parts = []
    gw, gh = 1110, 355
    parts.append(f'<g transform="translate(20,20)">')
    parts.append(f'<rect width="{gw}" height="{gh}" rx="10" class="grp"/>')
    parts.append('<text x="105" y="20" class="gl">ENTRY POINTS</text>')
    parts.append('<text x="310" y="20" class="gl">DISTILL</text>')
    parts.append('<text x="755" y="20" class="gl">VERIFICATION PANEL</text>')

    entry_skills = SKILL_GROUPS["ENTRY"]
    y = 40
    for s in entry_skills:
        parts.append(skill_box(25, y, s, w=155))
        y += 38

    parts.append(skill_box(230, 68, "to-seed", w=130))

    for i, s in enumerate(entry_skills):
        sy = 40 + i * 38 + 14
        parts.append(f'<line x1="180" y1="{sy}" x2="228" y2="{68 + 8 + i * 3}" class="ln" marker-end="url(#a)"/>')

    parts.append('<line x1="360" y1="83" x2="418" y2="83" class="ln" marker-end="url(#a)"/>')

    parts.append('<rect x="420" y="32" width="675" height="312" rx="8" class="vb"/>')

    personas = AGENT_GROUPS["DISTILL"]
    critics = [a for a in personas if a.startswith("persona-") and a != "persona-judge"]

    row1_critics = critics[:2]
    row2_critics = critics[2:]

    cx = 435
    for c in row1_critics:
        t = tier_to_letter(agent_tiers.get(c, "T1"))
        w = max(len(c) * 9 + 40, 140)
        parts.append(
            f'<rect x="{cx}" y="52" width="{w}" height="26" rx="5" class="ag" style="stroke:var(--red)"/>'
            f'<text x="{cx + w // 2}" y="65" class="an" style="fill:var(--red)">{esc(c)}</text>'
            f'<rect x="{cx + w - 35}" y="58" width="22" height="14" rx="3" class="bH"/>'
            f'<text x="{cx + w - 24}" y="66" class="bt btH">{t}</text>'
        )
        cx += w + 15

    cx = 435
    for c in row2_critics:
        t = tier_to_letter(agent_tiers.get(c, "T1"))
        w = max(len(c) * 9 + 40, 140)
        parts.append(
            f'<rect x="{cx}" y="88" width="{w}" height="26" rx="5" class="ag" style="stroke:var(--red)"/>'
            f'<text x="{cx + w // 2}" y="101" class="an" style="fill:var(--red)">{esc(c)}</text>'
            f'<rect x="{cx + w - 35}" y="94" width="22" height="14" rx="3" class="bH"/>'
            f'<text x="{cx + w - 24}" y="102" class="bt btH">{t}</text>'
        )
        cx += w + 15

    parts.append('<line x1="580" y1="114" x2="580" y2="136" class="ln" marker-end="url(#a)"/>')
    parts.append('<text x="580" y="148" class="al">refutations[]</text>')

    parts.append('<line x1="580" y1="156" x2="580" y2="172" class="ln" marker-end="url(#a)"/>')
    parts.append(
        '<rect x="490" y="175" width="180" height="26" rx="5" class="ag" style="stroke:var(--pur)"/>'
        '<text x="565" y="188" class="an" style="fill:var(--pur)">Round 1: Screener</text>'
        '<rect x="636" y="181" width="22" height="14" rx="3" class="bH"/>'
        '<text x="647" y="189" class="bt btH">H</text>'
    )

    parts.append('<line x1="580" y1="201" x2="580" y2="216" class="ln" marker-end="url(#a)"/>')
    parts.append('<text x="580" y="228" class="sl">upheld subset</text>')

    parts.append('<line x1="580" y1="236" x2="580" y2="250" class="ln" marker-end="url(#a)"/>')
    for i, label in enumerate(["Judge A", "Judge B", "Judge C"]):
        jx = 435 + i * 145
        parts.append(
            f'<rect x="{jx}" y="253" width="130" height="26" rx="5" class="ag" style="stroke:var(--pur)"/>'
            f'<text x="{jx + 65}" y="266" class="an" style="fill:var(--pur)">{label}</text>'
        )

    parts.append('<text x="640" y="295" class="sl">2-of-3 majority</text>')

    parts.append(
        '<rect x="480" y="305" width="100" height="24" rx="5" style="fill:var(--grn-d);stroke:var(--grn);stroke-width:1.5"/>'
        '<text x="530" y="317" class="an" style="fill:var(--grn)">Grounded</text>'
        '<rect x="600" y="305" width="100" height="24" rx="5" style="fill:var(--red-d);stroke:var(--red);stroke-width:1.5"/>'
        '<text x="650" y="317" class="an" style="fill:var(--red)">Rejected</text>'
    )

    parts.append(contract_box(840, 150, "seed.json v4", w=150))
    parts.append('<line x1="760" y1="83" x2="838" y2="160" class="lf ld" marker-end="url(#af)"/>')

    parts.append('</g>')
    return "\n".join(parts)


def render_investigate(skill_tiers, agent_tiers):
    """Row 1, Right: INVESTIGATE fan-out."""
    parts = []
    parts.append('<g transform="translate(1160,20)">')
    parts.append('<rect width="840" height="355" rx="10" class="grp"/>')
    parts.append('<text x="420" y="20" class="gl">INVESTIGATE</text>')

    inv_skills = SKILL_GROUPS["INVESTIGATE"] + ["pr-code-review"]
    sx = 20
    for s in inv_skills:
        w = max(len(s) * 9 + 20, 110)
        parts.append(skill_box(sx, 42, s, w=w))
        sx += w + 15

    t = tier_to_letter(agent_tiers.get("answer-composer", "T3"))
    parts.append(agent_box(560, 42, "answer-composer", t, w=165))

    parts.append(
        '<line x1="90" y1="70" x2="350" y2="108" class="ln" marker-end="url(#a)"/>'
        '<line x1="252" y1="70" x2="370" y2="108" class="ln" marker-end="url(#a)"/>'
        '<line x1="410" y1="70" x2="390" y2="108" class="ln" marker-end="url(#a)"/>'
    )

    t = tier_to_letter(agent_tiers.get("investigator", "T4"))
    parts.append(
        '<rect x="270" y="110" width="220" height="32" rx="5" class="ag" style="stroke:var(--pur)"/>'
        f'<text x="365" y="126" class="an" style="fill:var(--pur)">investigator</text>'
        f'<rect x="447" y="118" width="28" height="14" rx="3" class="bO"/>'
        f'<text x="461" y="126" class="bt btO">{t}</text>'
    )

    leaf_agents = [
        "investigator-code", "investigator-web", "investigator-github",
        "investigator-linear", "investigator-notion",
    ]

    fan_xs = [100, 260, 420, 580, 740]
    for i, fx in enumerate(fan_xs):
        parts.append(f'<line x1="{310 + i * 35}" y1="142" x2="{fx}" y2="178" class="ln" marker-end="url(#a)"/>')

    leaf_w = 150
    leaf_gap = 10
    total_leaf_w = len(leaf_agents) * leaf_w + (len(leaf_agents) - 1) * leaf_gap
    lx = (840 - total_leaf_w) // 2
    for i, la in enumerate(leaf_agents):
        cx = lx + leaf_w // 2
        parts.append(
            f'<rect x="{lx}" y="180" width="{leaf_w}" height="26" rx="5" class="ag"/>'
            f'<text x="{cx}" y="193" class="an" style="font-size:8.5px">{la}</text>'
            f'<rect x="{lx + leaf_w - 30}" y="186" width="22" height="14" rx="3" class="bH"/>'
            f'<text x="{lx + leaf_w - 19}" y="194" class="bt btH">H</text>'
        )
        lx += leaf_w + leaf_gap

    for i, fx in enumerate([100, 260, 420, 580, 740]):
        parts.append(
            f'<line x1="{fx}" y1="206" x2="{330 + i * 35}" y2="248" '
            f'class="ln" style="stroke:var(--blu)" marker-end="url(#a)"/>'
        )

    parts.append(contract_box(260, 250, "investigation-result.json", w=280))

    parts.append(
        '<line x1="540" y1="264" x2="610" y2="264" class="ln" marker-end="url(#a)"/>'
        '<line x1="640" y1="68" x2="640" y2="258" class="ln ld"/>'
    )

    parts.append('</g>')
    return "\n".join(parts)


def render_plan(skill_tiers, agent_tiers):
    """Row 2, Left: PLAN group."""
    parts = []
    parts.append('<g transform="translate(20,405)">')
    parts.append('<rect width="580" height="310" rx="10" class="grp"/>')
    parts.append('<text x="290" y="20" class="gl">PLAN</text>')

    plan_skills = SKILL_GROUPS["PLAN"]
    row1 = plan_skills[:3]
    row2 = plan_skills[3:]

    sx = 20
    for s in row1:
        w = max(len(s) * 9 + 20, 120)
        parts.append(skill_box(sx, 42, s, w=w))
        sx += w + 15

    sx = 20
    for s in row2:
        w = max(len(s) * 9 + 20, 120)
        parts.append(skill_box(sx, 82, s, w=w))
        sx += w + 15

    t = tier_to_letter(agent_tiers.get("context-loader", "T1"))
    parts.append(agent_box(295, 82, "context-loader", t, w=155))

    parts.append(
        '<line x1="80" y1="70" x2="80" y2="155" class="ln" marker-end="url(#a)"/>'
        '<line x1="217" y1="70" x2="300" y2="155" class="ln" marker-end="url(#a)"/>'
        '<line x1="365" y1="70" x2="340" y2="155" class="ln" marker-end="url(#a)"/>'
    )

    parts.append(contract_box(20, 158, "spec (HTML)", w=130))
    parts.append(contract_box(190, 158, "task.json v1", w=180))

    parts.append(
        '<path d="M82 110 L82 135 Q82 145 72 145 L40 145 Q30 145 30 135 L30 96" '
        'class="ln ld" marker-end="url(#a)"/>'
        '<text x="20" y="150" class="sl" style="text-anchor:start;font-size:7.5px">reseed</text>'
    )

    parts.append('<text x="290" y="210" class="sub">Consumed by: build-code, dispatch-tasks</text>')
    parts.append('<text x="290" y="228" class="sub">prototype loops back to to-seed on confirmed approach</text>')
    parts.append(
        '<line x1="80" y1="184" x2="190" y2="184" class="ln ld" marker-end="url(#a)"/>'
        '<text x="135" y="196" class="sl">feeds</text>'
    )

    parts.append('</g>')
    return "\n".join(parts)


def render_execute(skill_tiers, agent_tiers):
    """Row 2, Center: EXECUTE group."""
    parts = []
    parts.append('<g transform="translate(630,405)">')
    parts.append('<rect width="500" height="310" rx="10" class="grp"/>')
    parts.append('<text x="250" y="20" class="gl">EXECUTE</text>')

    parts.append(skill_box(100, 42, "build-code", w=140))
    parts.append(skill_box(260, 42, "tdd", w=100))

    t = tier_to_letter(agent_tiers.get("build-runner", "T3"))
    parts.append(
        '<line x1="170" y1="70" x2="230" y2="98" class="ln" marker-end="url(#a)"/>'
        '<line x1="310" y1="70" x2="270" y2="98" class="ln" marker-end="url(#a)"/>'
    )
    parts.append(agent_box(150, 100, "build-runner", t, w=200))

    parts.append(
        '<line x1="200" y1="130" x2="80" y2="160" class="ln" marker-end="url(#a)"/>'
        '<line x1="240" y1="130" x2="210" y2="160" class="ln" marker-end="url(#a)"/>'
        '<line x1="280" y1="130" x2="330" y2="160" class="ln" marker-end="url(#a)"/>'
        '<line x1="310" y1="130" x2="430" y2="160" class="ln" marker-end="url(#a)"/>'
    )

    sub_agents = [
        ("lint-runner", 15), ("test-runner", 150),
        ("browser-chk", 285), ("e2e", 415),
    ]
    for name, ax in sub_agents:
        w = 120 if name != "e2e" else 70
        t = "H"
        parts.append(
            f'<rect x="{ax}" y="163" width="{w}" height="24" rx="4" class="ag"/>'
            f'<text x="{ax + w // 2}" y="175" class="an" style="font-size:9px">{name}</text>'
            f'<rect x="{ax + w - 25}" y="167" width="20" height="14" rx="3" class="bH"/>'
            f'<text x="{ax + w - 15}" y="175" class="bt btH">{t}</text>'
        )

    parts.append(
        '<rect x="15" y="42" width="75" height="55" rx="6" fill="none" '
        'stroke="var(--acc)" stroke-width="1" stroke-dasharray="3 2"/>'
        '<text x="52" y="55" class="sl" style="fill:var(--acc);font-size:7.5px">TDD CYCLE</text>'
        '<text x="52" y="67" class="sl" style="fill:var(--red);font-size:8px">Red</text>'
        '<text x="52" y="78" class="sl" style="fill:var(--grn);font-size:8px">Green</text>'
        '<text x="52" y="89" class="sl" style="fill:var(--blu);font-size:8px">Refactor</text>'
    )

    parts.append('<line x1="250" y1="187" x2="250" y2="218" class="ln" style="stroke:var(--grn)" marker-end="url(#a)"/>')
    parts.append(contract_box(130, 220, "runner-result.json v1", w=210))

    parts.append('</g>')
    return "\n".join(parts)


def render_export(skill_tiers, agent_tiers):
    """Row 2, Right: EXPORT group."""
    parts = []
    parts.append('<g transform="translate(1160,405)">')
    parts.append('<rect width="840" height="310" rx="10" class="grp"/>')
    parts.append('<text x="420" y="20" class="gl">EXPORT</text>')

    export_skills = SKILL_GROUPS["EXPORT"]
    sx = 20
    for s in export_skills:
        w = max(len(s) * 9 + 20, 100)
        parts.append(skill_box(sx, 42, s, w=w))
        sx += w + 15

    t = tier_to_letter(agent_tiers.get("export-tasks", "T2"))
    parts.append('<line x1="100" y1="70" x2="100" y2="100" class="ln" marker-end="url(#a)"/>')
    parts.append(agent_box(20, 103, "export-tasks", t, w=160))

    parts.append(
        '<line x1="60" y1="129" x2="60" y2="155" class="ln" marker-end="url(#a)"/>'
        '<line x1="100" y1="129" x2="225" y2="155" class="ln" marker-end="url(#a)"/>'
        '<line x1="140" y1="129" x2="390" y2="155" class="ln" marker-end="url(#a)"/>'
    )

    adapters = [
        ("export-tasks-github", 15, 160), ("export-tasks-linear", 190, 175),
        ("export-tasks-notion", 380, 175),
    ]
    for name, ax, w in adapters:
        t = tier_to_letter(agent_tiers.get(name, "T1"))
        parts.append(
            f'<rect x="{ax}" y="158" width="{w}" height="24" rx="4" class="ag"/>'
            f'<text x="{ax + w // 2}" y="170" class="an" style="font-size:9px">{name}</text>'
            f'<rect x="{ax + w - 33}" y="162" width="22" height="14" rx="3" class="bH"/>'
            f'<text x="{ax + w - 22}" y="170" class="bt btH">{t}</text>'
        )

    parts.append('<text x="100" y="205" class="sl" style="fill:var(--tx-d)">EGRESS ADAPTERS (copy-only)</text>')

    egress = [
        ("post-github", 15, 130), ("post-linear", 160, 125), ("post-slack", 300, 120),
    ]
    for name, ax, w in egress:
        t = tier_to_letter(agent_tiers.get(name, "T3"))
        parts.append(
            f'<rect x="{ax}" y="215" width="{w}" height="24" rx="4" class="ag"/>'
            f'<text x="{ax + w // 2}" y="227" class="an" style="font-size:9px">{name}</text>'
            f'<rect x="{ax + w - 33}" y="219" width="22" height="14" rx="3" class="bS"/>'
            f'<text x="{ax + w - 22}" y="227" class="bt btS">{t}</text>'
        )

    parts.append(contract_box(560, 158, "egress-result.json", w=190))

    for i, dest in enumerate(["GitHub Issues", "Linear", "Notion", "Slack"]):
        parts.append(f'<text x="655" y="{200 + i * 14}" class="sl">{dest}</text>')

    parts.append('</g>')
    return "\n".join(parts)


def render_improve(skill_tiers, agent_tiers):
    """Row 3, Left: IMPROVE group."""
    parts = []
    parts.append('<g transform="translate(20,745)">')
    parts.append('<rect width="580" height="250" rx="10" class="grp"/>')
    parts.append('<text x="290" y="20" class="gl">IMPROVE</text>')

    improve_skills = SKILL_GROUPS["IMPROVE"]
    row1 = improve_skills[:3]
    row2 = improve_skills[3:]

    sx = 20
    for s in row1:
        w = max(len(s) * 9 + 20, 130)
        parts.append(skill_box(sx, 42, s, w=w))
        sx += w + 15

    sx = 20
    for s in row2:
        w = max(len(s) * 9 + 20, 160)
        parts.append(skill_box(sx, 82, s, w=w))
        sx += w + 15

    t = tier_to_letter(agent_tiers.get("architecture-auditor", "T3"))
    parts.append('<line x1="90" y1="70" x2="220" y2="138" class="ln" marker-end="url(#a)"/>')
    parts.append(agent_box(140, 140, "architecture-auditor", t, w=180))

    parts.append('<text x="290" y="195" class="sub">Produces audit reports, feeds back into to-tasks pipeline</text>')

    parts.append('</g>')
    return "\n".join(parts)


def render_learn(skill_tiers, agent_tiers):
    """Row 3, Center: LEARN group."""
    parts = []
    parts.append('<g transform="translate(630,745)">')
    parts.append('<rect width="500" height="250" rx="10" class="grp"/>')
    parts.append('<text x="250" y="20" class="gl">LEARN</text>')
    parts.append('<text x="250" y="36" class="sl">from all skills (universal tail)</text>')

    learn_agents = AGENT_GROUPS["LEARN"]

    t = tier_to_letter(agent_tiers.get("capture-learning", "T3"))
    parts.append(agent_box(20, 55, "capture-learning", t, w=175))

    parts.append('<line x1="107" y1="81" x2="107" y2="105" class="ln" marker-end="url(#a)"/>')

    parts.append('<rect x="20" y="108" width="195" height="26" rx="5" class="ct"/>')
    parts.append('<text x="117" y="121" class="cn">verify-anchors.py</text>')

    t = tier_to_letter(agent_tiers.get("attribution-tracer", "T3"))
    parts.append(agent_box(270, 55, "attribution-tracer", t, w=175))

    parts.append('<line x1="357" y1="81" x2="357" y2="105" class="ln" marker-end="url(#a)"/>')

    t = tier_to_letter(agent_tiers.get("artifact-grounding-judge", "T3"))
    parts.append(agent_box(260, 108, "artifact-ground-judge", t, w=200))

    parts.append(
        '<line x1="250" y1="134" x2="250" y2="162" class="ln" style="stroke:var(--pur)" marker-end="url(#a)"/>'
        '<rect x="100" y="165" width="280" height="26" rx="5" class="ct" style="stroke:var(--pur);fill:var(--pur-d)"/>'
        '<text x="240" y="178" class="cn" style="fill:var(--pur)">unified-learnings.jsonl</text>'
    )

    parts.append('<text x="250" y="210" class="sub" style="text-anchor:middle">Correction events fed back to improve future runs</text>')

    parts.append('</g>')
    return "\n".join(parts)


def render_utility(skill_tiers, agent_tiers):
    """Row 3, Right: UTILITY group."""
    parts = []
    parts.append('<g transform="translate(1160,745)">')
    parts.append('<rect width="840" height="250" rx="10" class="grp"/>')
    parts.append('<text x="420" y="20" class="gl">UTILITY</text>')

    util_skills = SKILL_GROUPS["UTILITY"]
    cols_per_row = 4
    col_w = 190
    row_h = 36
    for i, s in enumerate(util_skills):
        row = i // cols_per_row
        col = i % cols_per_row
        w = max(len(s) * 9 + 20, 110)
        x = 20 + col * col_w
        y = 42 + row * row_h
        parts.append(skill_box(x, y, s, w=w))

    parts.append(
        '<text x="420" y="170" class="sub" style="text-anchor:middle">'
        'Standalone skills. Each connects to learning tail.</text>'
    )
    parts.append(
        '<text x="420" y="186" class="sub" style="text-anchor:middle">'
        'handoff produces seed handoff docs. find-work searches for work items.</text>'
    )

    parts.append('</g>')
    return "\n".join(parts)


def render_infrastructure(hooks, scripts, resources):
    """Row 4, Full Width: INFRASTRUCTURE group."""
    parts = []
    parts.append('<g transform="translate(20,1025)">')
    parts.append('<rect width="1980" height="255" rx="10" class="grp"/>')
    parts.append('<text x="990" y="20" class="gl">INFRASTRUCTURE</text>')

    parts.append(
        f'<text x="100" y="42" class="sl" style="fill:var(--red);text-anchor:start;'
        f'font-weight:700;letter-spacing:0.08em">HOOKS ({len(hooks)} BLOCKING GATES)</text>'
    )

    hx, hy = 20, 55
    hw = 145
    hgap = 10
    cols = 7
    for i, h in enumerate(hooks):
        row = i // cols
        col = i % cols
        x = 20 + col * (hw + hgap)
        y = 55 + row * 30
        parts.append(hook_box(x, y, h, w=hw))

    parts.append(
        f'<text x="100" y="130" class="sl" style="fill:var(--tx-m);text-anchor:start;'
        f'font-weight:700;letter-spacing:0.08em">KEY SCRIPTS ({len(scripts)} TOTAL)</text>'
    )

    display_scripts = scripts[:8]
    sx = 20
    for s in display_scripts:
        w = max(len(s) * 7.5 + 20, 110)
        parts.append(script_box(sx, 142, s, w=w))
        sx += w + 10

    parts.append(
        f'<text x="100" y="185" class="sl" style="fill:var(--pur);text-anchor:start;'
        f'font-weight:700;letter-spacing:0.08em">KEY RESOURCES ({len(resources)} TOTAL)</text>'
    )

    display_resources = resources[:8]
    rx = 20
    for r in display_resources:
        w = max(len(r) * 7.5 + 20, 120)
        parts.append(resource_box(rx, 197, r, w=w))
        rx += w + 10

    parts.append('</g>')
    return "\n".join(parts)


def render_connections():
    """Inter-group flow arrows."""
    return """<g id="connections">
  <path d="M300 375 L300 393 L300 403" class="lf" marker-end="url(#af)" fill="none"/>
  <text x="320" y="393" class="al" style="text-anchor:start">seed.json</text>

  <line x1="600" y1="560" x2="628" y2="560" class="lf" marker-end="url(#af)"/>
  <text x="614" y="550" class="al" style="font-size:8px">task.json</text>

  <path d="M500 715 L500 728 L1160 728 L1160 715" class="lf ld" marker-end="url(#af)" fill="none"/>
  <text x="830" y="722" class="al">task.json (dispatch path)</text>

  <path d="M880 715 L880 743" class="lf ld" marker-end="url(#af)" fill="none"/>
  <text x="895" y="734" class="al" style="text-anchor:start;font-size:8px">results</text>

  <text x="625" y="778" class="al" style="font-size:9px;text-anchor:end">all skills &#8594;</text>

  <path d="M630 870 L10 870 L10 200 L18 200" class="lf ld" marker-end="url(#af)" fill="none"/>
  <text x="10" y="540" class="al" transform="rotate(-90, 10, 540)" style="font-size:8px">feedback loop</text>

  <path d="M1580 375 L1580 393 L1135 393 L1135 743" class="ln ld" marker-end="url(#a)" fill="none"/>
  <text x="1280" y="387" class="sl" style="font-size:8px">attribution path</text>
</g>"""


# ─── FULL HTML ──────────────────────────────────────────────────────────────

CSS = """\
:root {
    --font-mono: 'JetBrains Mono', monospace;
    --font-body: 'Source Sans 3', system-ui, sans-serif;
    --bg: #0B1222; --bg-s: #141D2F; --bg-e: #1A2540;
    --tx: #E2E8F0; --tx-m: #94A3B8; --tx-d: #475569;
    --acc: #F0B429; --acc-d: rgba(240,180,41,0.15);
    --blu: #60A5FA; --blu-d: rgba(96,165,250,0.12);
    --red: #F87171; --red-d: rgba(248,113,113,0.1);
    --grn: #4ADE80; --grn-d: rgba(74,222,128,0.1);
    --pur: #C084FC; --pur-d: rgba(192,132,252,0.1);
    --bdr: #1E293B;
  }
  @media(prefers-color-scheme:light){:root:not([data-theme="dark"]){
    --bg:#FAFBFC;--bg-s:#F1F5F9;--bg-e:#E8EDF4;
    --tx:#1E293B;--tx-m:#64748B;--tx-d:#94A3B8;
    --acc:#B8860B;--acc-d:rgba(184,134,11,0.08);
    --blu:#2563EB;--blu-d:rgba(37,99,235,0.06);
    --red:#DC2626;--red-d:rgba(220,38,38,0.06);
    --grn:#16A34A;--grn-d:rgba(22,163,74,0.06);
    --pur:#9333EA;--pur-d:rgba(147,51,234,0.06);
    --bdr:#E2E8F0;
  }}
  :root[data-theme="light"]{
    --bg:#FAFBFC;--bg-s:#F1F5F9;--bg-e:#E8EDF4;
    --tx:#1E293B;--tx-m:#64748B;--tx-d:#94A3B8;
    --acc:#B8860B;--acc-d:rgba(184,134,11,0.08);
    --blu:#2563EB;--blu-d:rgba(37,99,235,0.06);
    --red:#DC2626;--red-d:rgba(220,38,38,0.06);
    --grn:#16A34A;--grn-d:rgba(22,163,74,0.06);
    --pur:#9333EA;--pur-d:rgba(147,51,234,0.06);
    --bdr:#E2E8F0;
  }

  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:var(--font-body);background:var(--bg);color:var(--tx);overflow:hidden;height:100vh;width:100vw}

  .hdr{position:fixed;top:0;left:0;right:0;z-index:50;background:var(--bg);border-bottom:1px solid var(--bdr);padding:10px 24px;display:flex;align-items:center;gap:24px;flex-wrap:wrap}
  .hdr h1{font-family:var(--font-mono);font-size:1rem;font-weight:700;white-space:nowrap}
  .stats{font-family:var(--font-mono);font-size:0.75rem;color:var(--tx-d);white-space:nowrap}
  .legend{display:flex;gap:16px;font-family:var(--font-mono);font-size:0.7rem;margin-left:auto;flex-wrap:wrap}
  .legend-item{display:flex;align-items:center;gap:5px;white-space:nowrap}
  .legend-swatch{width:14px;height:14px;border-radius:3px;border:1.5px solid}
  .sw-skill{background:var(--bg-s);border-color:var(--blu)}
  .sw-agent{background:var(--bg-s);border-color:var(--grn)}
  .sw-contract{background:var(--acc-d);border-color:var(--acc)}
  .sw-hook{background:var(--bg-s);border-color:var(--red)}
  .badge-legend{display:inline-flex;align-items:center;gap:3px;padding:1px 5px;border-radius:3px;font-weight:700;font-size:0.6rem}
  .bl-h{background:var(--grn-d);color:var(--grn)}
  .bl-s{background:var(--blu-d);color:var(--blu)}
  .bl-o{background:var(--pur-d);color:var(--pur)}

  .vp{position:fixed;top:48px;left:0;right:0;bottom:0;overflow:hidden;cursor:grab}
  .vp.dragging{cursor:grabbing}
  .vp svg{position:absolute;top:0;left:0;transform-origin:0 0;transition:none}

  .ctrl{position:fixed;bottom:20px;right:20px;z-index:50;display:flex;gap:6px}
  .ctrl button{width:34px;height:34px;border:1px solid var(--bdr);border-radius:6px;background:var(--bg-s);color:var(--tx-m);cursor:pointer;font-family:var(--font-mono);font-size:0.9rem;display:flex;align-items:center;justify-content:center}
  .ctrl button:hover{border-color:var(--acc);color:var(--acc)}
  .ctrl .lbl{font-size:0.7rem;padding:0 8px;width:auto}

  .grp{fill:var(--bg);stroke:var(--bdr);stroke-width:1;stroke-dasharray:6 4}
  .gl{fill:var(--tx-d);font-family:var(--font-mono);font-size:10px;font-weight:700;letter-spacing:0.1em;text-anchor:middle}
  .sk{fill:var(--bg-s);stroke:var(--blu);stroke-width:1.5}
  .sn{fill:var(--tx);font-family:var(--font-mono);font-size:10px;font-weight:600;text-anchor:middle;dominant-baseline:central}
  .ag{fill:var(--bg-s);stroke:var(--grn);stroke-width:1.5}
  .an{fill:var(--tx);font-family:var(--font-mono);font-size:9.5px;font-weight:500;text-anchor:middle;dominant-baseline:central}
  .ct{fill:var(--acc-d);stroke:var(--acc);stroke-width:1.5}
  .cn{fill:var(--acc);font-family:var(--font-mono);font-size:9.5px;font-weight:600;text-anchor:middle;dominant-baseline:central}
  .hk{fill:var(--bg-s);stroke:var(--red);stroke-width:1}
  .hn{fill:var(--tx-m);font-family:var(--font-mono);font-size:8px;font-weight:500;text-anchor:middle;dominant-baseline:central}
  .bH{fill:var(--grn-d);stroke:var(--grn);stroke-width:1}
  .bS{fill:var(--blu-d);stroke:var(--blu);stroke-width:1}
  .bO{fill:var(--pur-d);stroke:var(--pur);stroke-width:1}
  .bt{font-family:var(--font-mono);font-size:8px;font-weight:700;text-anchor:middle;dominant-baseline:central}
  .btH{fill:var(--grn)}.btS{fill:var(--blu)}.btO{fill:var(--pur)}
  .ln{stroke:var(--tx-d);stroke-width:1.2;fill:none}
  .lf{stroke:var(--acc);stroke-width:2;fill:none}
  .ld{stroke-dasharray:5 4}
  .sl{fill:var(--tx-d);font-family:var(--font-mono);font-size:8.5px;text-anchor:middle;dominant-baseline:central}
  .al{fill:var(--acc);font-family:var(--font-mono);font-size:9px;font-weight:600;text-anchor:middle;dominant-baseline:central}
  .vb{fill:none;stroke:var(--red);stroke-width:1;stroke-dasharray:4 3}
  .sub{fill:var(--tx-d);font-family:var(--font-body);font-size:9px;text-anchor:start;dominant-baseline:central}

  @media(prefers-reduced-motion:reduce){.vp svg{transition:none}}"""

JS = """\
(function(){
  const vp = document.getElementById('vp');
  const svg = document.getElementById('dia');
  let scale = 1, px = 0, py = 0;
  let dragging = false, sx, sy, spx, spy;

  function fit() {
    const vw = vp.clientWidth, vh = vp.clientHeight;
    const sw = 2020, sh = 1300;
    scale = Math.min(vw / sw, vh / sh) * 0.98;
    px = (vw - sw * scale) / 2;
    py = (vh - sh * scale) / 2;
    apply();
  }

  function apply() {
    svg.style.transform = 'translate(' + px + 'px,' + py + 'px) scale(' + scale + ')';
  }

  function zoom(d, cx, cy) {
    const ns = Math.max(0.15, Math.min(3, scale * d));
    const r = ns / scale;
    px = cx - (cx - px) * r;
    py = cy - (cy - py) * r;
    scale = ns;
    apply();
  }

  vp.addEventListener('wheel', function(e) {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1.12 : 0.89, e.clientX, e.clientY - 48);
  }, {passive: false});

  vp.addEventListener('mousedown', function(e) {
    if (e.button !== 0) return;
    dragging = true; sx = e.clientX; sy = e.clientY; spx = px; spy = py;
    vp.classList.add('dragging');
  });

  window.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    px = spx + (e.clientX - sx);
    py = spy + (e.clientY - sy);
    apply();
  });

  window.addEventListener('mouseup', function() {
    dragging = false;
    vp.classList.remove('dragging');
  });

  let ts = 0, td = 0;
  vp.addEventListener('touchstart', function(e) {
    if (e.touches.length === 1) {
      sx = e.touches[0].clientX; sy = e.touches[0].clientY; spx = px; spy = py;
    } else if (e.touches.length === 2) {
      td = Math.hypot(e.touches[1].clientX - e.touches[0].clientX, e.touches[1].clientY - e.touches[0].clientY);
      ts = scale;
    }
  });

  vp.addEventListener('touchmove', function(e) {
    e.preventDefault();
    if (e.touches.length === 1) {
      px = spx + (e.touches[0].clientX - sx);
      py = spy + (e.touches[0].clientY - sy);
      apply();
    } else if (e.touches.length === 2) {
      const nd = Math.hypot(e.touches[1].clientX - e.touches[0].clientX, e.touches[1].clientY - e.touches[0].clientY);
      scale = Math.max(0.15, Math.min(3, ts * (nd / td)));
      apply();
    }
  }, {passive: false});

  document.getElementById('zi').addEventListener('click', function() { zoom(1.25, vp.clientWidth / 2, vp.clientHeight / 2); });
  document.getElementById('zo').addEventListener('click', function() { zoom(0.8, vp.clientWidth / 2, vp.clientHeight / 2); });
  document.getElementById('zr').addEventListener('click', fit);

  fit();
  window.addEventListener('resize', fit);
})();"""


def generate_html(skill_tiers, agent_tiers, hooks, scripts, resources,
                   n_skills, n_agents):
    contracts = ["seed.json", "task.json", "runner-result.json",
                 "investigation-result.json", "egress-result.json",
                 "unified-learnings.jsonl"]

    svg_groups = [
        render_entry_distill(skill_tiers, agent_tiers),
        render_investigate(skill_tiers, agent_tiers),
        render_plan(skill_tiers, agent_tiers),
        render_execute(skill_tiers, agent_tiers),
        render_export(skill_tiers, agent_tiers),
        render_improve(skill_tiers, agent_tiers),
        render_learn(skill_tiers, agent_tiers),
        render_utility(skill_tiers, agent_tiers),
        render_infrastructure(hooks, scripts, resources),
        render_connections(),
    ]

    svg_body = "\n\n".join(svg_groups)

    return f"""\
<title>Claude Code Architecture</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Source+Sans+3:wght@400;600&display=swap">

<style>
  {CSS}
</style>

<header class="hdr">
  <h1>Claude Code Shared Architecture</h1>
  <span class="stats">{n_skills} Skills &middot; {n_agents}+ Agents &middot; {len(contracts)} Contracts &middot; {len(hooks)} Hooks &middot; {len(scripts)} Scripts</span>
  <div class="legend">
    <span class="legend-item"><span class="legend-swatch sw-skill"></span>Skill</span>
    <span class="legend-item"><span class="legend-swatch sw-agent"></span>Agent</span>
    <span class="legend-item"><span class="legend-swatch sw-contract"></span>Contract</span>
    <span class="legend-item"><span class="legend-swatch sw-hook"></span>Hook</span>
    <span class="legend-item"><span class="badge-legend bl-h">H</span>Haiku</span>
    <span class="legend-item"><span class="badge-legend bl-s">S</span>Sonnet</span>
    <span class="legend-item"><span class="badge-legend bl-o">O</span>Opus</span>
  </div>
</header>

<div class="vp" id="vp">
<svg viewBox="0 0 2020 1300" id="dia" xmlns="http://www.w3.org/2000/svg">
<defs>
  <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 1L8 5L0 9z" fill="var(--tx-d)"/></marker>
  <marker id="af" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L8 5L0 9z" fill="var(--acc)"/></marker>
</defs>

{svg_body}

</svg>
</div>

<div class="ctrl">
  <button id="zi" title="Zoom in">+</button>
  <button id="zo" title="Zoom out">&minus;</button>
  <button id="zr" class="lbl" title="Reset view">Fit</button>
</div>

<script>
{JS}
</script>
"""


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate architecture.html from live directory")
    parser.add_argument("--check", action="store_true", help="Report unclassified items only")
    parser.add_argument("--out", type=str, default=None, help="Output file path")
    args = parser.parse_args()

    if args.check:
        sys.exit(run_check())

    skill_tiers, agent_tiers = load_tiers()
    skills = discover_skills()
    agents = discover_agents()
    hooks = discover_hooks()
    scripts = discover_scripts()
    resources = discover_resources()

    unclassified = classify_items(skills, SKILL_GROUPS)
    if unclassified:
        print(f"Warning: unclassified skills: {', '.join(unclassified)}", file=sys.stderr)

    unclassified_agents = classify_items(agents, AGENT_GROUPS)
    if unclassified_agents:
        print(f"Warning: unclassified agents: {', '.join(unclassified_agents)}", file=sys.stderr)

    html = generate_html(
        skill_tiers, agent_tiers, hooks, scripts, resources,
        n_skills=len(skills), n_agents=len(agents),
    )

    out_path = Path(args.out) if args.out else OUT_DEFAULT
    out_path.write_text(html)
    print(f"Wrote {out_path} ({len(skills)} skills, {len(agents)} agents, {len(hooks)} hooks, {len(scripts)} scripts)")


if __name__ == "__main__":
    main()
