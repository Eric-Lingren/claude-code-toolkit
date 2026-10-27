#!/usr/bin/env python3
"""Stamp model + effort into skill frontmatter, and model into agent frontmatter.

Single source of truth: ~/.dotfiles/claude-code-shared/resources/model-tiers.json

For skills: writes explicit `model:` + `effort:` lines into each skill's
SKILL.md frontmatter. Every skill gets an EXPLICIT model (even T3, the default)
so skills are immune to a later change of the session default.

For agents: writes only `model:` into each agent's *.md frontmatter. Effort is
a session concept and is never stamped into agent files. Also updates the
matching agents/registry.json entry so the registry never drifts from the tier.

To re-tier later: edit model-tiers.json (change what a tier points to, or move
a skill/agent between tiers), then re-run this with --apply. One edit point,
all skills and agents re-stamped.

Frontmatter editing is surgical (no YAML round-trip): it inserts/replaces the
top-level `model:` and `effort:` lines just before the closing `---`, leaving
everything else (folded descriptions, key order) byte-for-byte intact.

Usage:
  sync-model-tiers.py --check     # show what WOULD change (default if no flag)
  sync-model-tiers.py --apply     # write the changes
  sync-model-tiers.py --skills-dir <path>   # override skills location
  sync-model-tiers.py --agents-dir <path>   # override agents location
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DOTFILES = os.path.expanduser("~/.dotfiles/claude-code-shared")
CONFIG = os.path.join(DOTFILES, "resources", "model-tiers.json")
SKILLS_DIR = os.path.join(DOTFILES, "skills")
AGENTS_DIR = os.path.join(DOTFILES, "agents")

TOP_KEY = re.compile(r"^(model|effort):", re.I)

DELEGATE_START = "<!-- tier-delegate: managed by sync-model-tiers.py -->"
DELEGATE_END = "<!-- /tier-delegate -->"
DELEGATE_BLOCK = f"""{DELEGATE_START}
## Delegate menial lookups to Haiku (cost control)

During this skill, push pure read-only lookups DOWN to a cheap subagent instead
of running them on the current model. This covers: multi-file grep/glob,
"where is X defined / what calls Y", mapping a directory, reading many files to
locate something, or fetching a URL for reference.

Use the Agent tool with the `caveman:cavecrew-investigator` subagent (Haiku,
returns a compressed file:line answer). If that subagent is unavailable, spawn a
general agent with `model: haiku`. Keep all reasoning, decisions, and edits on
the current model. Delegate only the menial searching.
{DELEGATE_END}"""

# Matches both legacy (sync-skill-tiers variant) and current managed delegate blocks.
DELEGATE_RE = re.compile(
    r"<!-- tier-delegate: managed by sync-(?:skill|model)-tiers\.py -->.*?<!-- /tier-delegate -->\n*",
    re.S)


def inject_delegate(post, want):
    """post = closing '---' + body. Insert/remove the managed delegate block
    right after the closing '---'. Idempotent."""
    body = DELEGATE_RE.sub("", post)
    if not want:
        return body
    lines = body.split("\n")
    head, rest = lines[0], "\n".join(lines[1:]).lstrip("\n")
    return head + "\n\n" + DELEGATE_BLOCK + "\n\n" + rest


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    tiers = cfg["tiers"]
    for sname, tier in cfg["skills"].items():
        if tier not in tiers:
            sys.exit(f"ERROR: skill '{sname}' -> unknown tier '{tier}'")
    for aname, tier in cfg.get("agents", {}).items():
        if tier not in tiers:
            sys.exit(f"ERROR: agent '{aname}' -> unknown tier '{tier}'")
    return cfg


def split_frontmatter(text):
    """Return (pre, fm_lines, post) where fm_lines are the lines BETWEEN the
    opening and closing '---'. Returns None if no frontmatter block."""
    if not text.startswith("---\n"):
        return None
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = lines[1:i]
            post = "\n".join(lines[i:])
            return "---\n", fm, post
    return None


def stamp(fm_lines, model, effort):
    """Replace or insert top-level model:/effort: in skill frontmatter."""
    out = []
    have_model = have_effort = False
    for ln in fm_lines:
        if re.match(r"^model:", ln, re.I):
            out.append(f"model: {model}")
            have_model = True
        elif re.match(r"^effort:", ln, re.I):
            out.append(f"effort: {effort}")
            have_effort = True
        else:
            out.append(ln)
    if not have_model:
        out.append(f"model: {model}")
    if not have_effort:
        out.append(f"effort: {effort}")
    return out


def stamp_agent_model(fm_lines, model):
    """Replace or insert top-level model: in agent frontmatter (no effort for agents)."""
    out = []
    have_model = False
    for ln in fm_lines:
        if re.match(r"^model:", ln, re.I):
            out.append(f"model: {model}")
            have_model = True
        else:
            out.append(ln)
    if not have_model:
        out.append(f"model: {model}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="dry run (default)")
    g.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--skills-dir", default=SKILLS_DIR)
    ap.add_argument("--agents-dir", default=AGENTS_DIR)
    ap.add_argument("--print-changed", action="store_true",
                    help="also print each changed file path as 'CHANGED<TAB>path' "
                         "(for the git pre-commit hook to re-stage exactly those files)")
    args = ap.parse_args()
    apply = args.apply

    cfg = load_config(args.config)
    tiers = cfg["tiers"]
    delegate = set(cfg.get("delegate", []))
    changed = missing = ok = 0
    changed_paths = []

    print(f"{'APPLY' if apply else 'CHECK'}  config={args.config}\n")

    # ---- Skills ----
    print("Skills:")
    for skill, tier in cfg["skills"].items():
        model = tiers[tier]["model"]
        effort = tiers[tier]["effort"]
        path = os.path.join(args.skills_dir, skill, "SKILL.md")
        if not os.path.isfile(path):
            print(f"  MISSING  {skill:36s} (no {path})")
            missing += 1
            continue
        with open(path) as f:
            text = f.read()
        parts = split_frontmatter(text)
        if parts is None:
            print(f"  NO-FM    {skill:36s} (no frontmatter block, skipped)")
            missing += 1
            continue
        pre, fm, post = parts
        new_fm = stamp(list(fm), model, effort)
        new_post = inject_delegate(post, skill in delegate)
        new_text = pre + "\n".join(new_fm) + "\n" + new_post
        deleg_tag = " +delegate" if skill in delegate else ""
        if new_text == text:
            print(f"  ok       {skill:36s} {tier} -> {model}/{effort}{deleg_tag}")
            ok += 1
            continue
        changed += 1
        changed_paths.append(path)
        print(f"  CHANGE   {skill:36s} {tier} -> {model}/{effort}{deleg_tag}")
        if apply:
            with open(path, "w") as f:
                f.write(new_text)

    # ---- Agents ----
    print("\nAgents:")
    registry_path = os.path.join(args.agents_dir, "registry.json")
    registry_data = None
    registry_changed = False
    if os.path.isfile(registry_path):
        with open(registry_path) as f:
            registry_data = json.load(f)

    agent_paths = {}
    for p in sorted(Path(args.agents_dir).rglob("*.md")):
        agent_paths.setdefault(p.stem, []).append(str(p))

    for agent_name, tier in cfg.get("agents", {}).items():
        model = tiers[tier]["model"]
        candidates = agent_paths.get(agent_name, [])
        if not candidates:
            print(f"  MISSING  {agent_name:36s} (no {agent_name}.md under {args.agents_dir})")
            missing += 1
            continue
        if len(candidates) > 1:
            print(f"  AMBIG    {agent_name:36s} ({', '.join(candidates)})")
            missing += 1
            continue
        path = candidates[0]
        with open(path) as f:
            text = f.read()
        parts = split_frontmatter(text)
        if parts is None:
            print(f"  NO-FM    {agent_name:36s} (no frontmatter block, skipped)")
            missing += 1
            continue
        pre, fm, post = parts
        new_fm = stamp_agent_model(list(fm), model)
        new_text = pre + "\n".join(new_fm) + "\n" + post
        if new_text == text:
            print(f"  ok       {agent_name:36s} {tier} -> {model} (model only)")
            ok += 1
        else:
            changed += 1
            changed_paths.append(path)
            print(f"  CHANGE   {agent_name:36s} {tier} -> {model} (model only)")
            if apply:
                with open(path, "w") as f:
                    f.write(new_text)

        # Update registry.json model field
        if registry_data is not None:
            for entry in registry_data.get("agents", []):
                if entry.get("name") == agent_name:
                    if entry.get("model") != model:
                        print(f"  CHANGE   registry.json: {agent_name}.model "
                              f"{entry.get('model')} -> {model}")
                        if apply:
                            entry["model"] = model
                            registry_changed = True
                        changed += 1
                    break

    if registry_data is not None and apply and registry_changed:
        with open(registry_path, "w") as f:
            json.dump(registry_data, f, indent=2)
            f.write("\n")
        changed_paths.append(registry_path)

    print(f"\n{'wrote' if apply else 'would change'}: {changed}   already-ok: {ok}   missing: {missing}")
    if not apply and changed:
        print("Run with --apply to write.")
    if args.print_changed:
        for p in changed_paths:
            print(f"CHANGED\t{p}")


if __name__ == "__main__":
    main()
