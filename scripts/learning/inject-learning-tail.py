#!/usr/bin/env python3
"""inject-learning-tail.py — install or update the managed learning capture tail block
in every shared SKILL.md.

Iterates over all SKILL.md files under claude-code-shared/skills/*/. Finds the
sentinel-delimited block (<!-- learning-capture:start --> / <!-- learning-capture:end -->)
and replaces it, or appends it if not yet present. Idempotent: a second run makes no
changes when the block is already up to date.

Reads skill-pipeline.json to bake each skill's closing next-step suggestion. Validates
that every slug referenced in pipeline 'next' edges exists in the skills/ directory.
Fails loudly if any referenced slug is missing.

Excludes plugin skill directories (caveman, chrome-devtools) — they live outside the
dotfiles tree and update on their own cadence.

Usage:
  inject-learning-tail.py --check     # show what would change (default)
  inject-learning-tail.py --apply     # write changes
  inject-learning-tail.py --skills-dir <path>   # override skills location
  inject-learning-tail.py --pipeline  <path>    # override pipeline file location

Environment overrides (for testing):
  INJECT_SKILLS_DIR   — override skills directory
  INJECT_PIPELINE_FILE — override pipeline file path
"""

import argparse
import json
import os
import re
import sys

DOTFILES = os.path.expanduser("~/.dotfiles/claude-code-shared")
SKILLS_DIR = os.environ.get("INJECT_SKILLS_DIR") or os.path.join(DOTFILES, "skills")
PIPELINE_FILE = os.environ.get("INJECT_PIPELINE_FILE") or os.path.join(DOTFILES, "skill-pipeline.json")

PLUGIN_SKILLS = {"caveman", "chrome-devtools"}

SENTINEL_START = "<!-- learning-capture:start -->"
SENTINEL_END = "<!-- learning-capture:end -->"

# Sentinels must sit alone on their own line. An inline mention (e.g. in
# backticks inside prose) must not match, or the lazy body swallows everything
# up to the real end marker.
BLOCK_RE = re.compile(
    r"^<!-- learning-capture:start -->$.*?^<!-- learning-capture:end -->$",
    re.S | re.M,
)


def build_tail_block(slug, next_edges):
    """Build the per-skill learning-capture tail block.

    Single source of truth lives in resources/learning-capture.md — this block
    only references it, so instruction changes never need a 30-file fan-out.

    slug: the skill slug (e.g. 'debug')
    next_edges: list of {skill, when} dicts from skill-pipeline.json
    """
    lines = [
        SENTINEL_START,
        "Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.",
        f"This skill's slug is `{slug}`.",
        f"<!-- skill-done: {slug} -->",
    ]
    for e in next_edges:
        lines.append(f"  - `/{e['skill']}` — {e['when']}")
    lines.append(SENTINEL_END)

    return "\n".join(lines)


def validate_pipeline_slugs(pipeline, skills_dir):
    """Return list of slugs referenced in pipeline that don't exist in skills_dir."""
    bad = []
    dag = pipeline.get("skills", {})
    existing = {e.name for e in os.scandir(skills_dir) if e.is_dir()} if os.path.isdir(skills_dir) else set()

    for source_slug, entry in dag.items():
        if source_slug not in existing:
            bad.append(source_slug)
        for edge in entry.get("next", []):
            target = edge.get("skill", "")
            if target and target not in existing and target not in bad:
                bad.append(target)
    return bad


def inject(text, block):
    """Replace existing block or append. Returns new text (unchanged if already correct)."""
    if BLOCK_RE.search(text):
        new_text = BLOCK_RE.sub(block, text)
        if not new_text.endswith("\n"):
            new_text += "\n"
        return new_text
    else:
        stripped = text.rstrip("\n")
        return stripped + "\n\n" + block + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="dry run (default)")
    g.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--skills-dir", default=None)
    ap.add_argument("--pipeline", default=None)
    args = ap.parse_args()
    apply = args.apply

    skills_dir = args.skills_dir or SKILLS_DIR
    pipeline_file = args.pipeline or PIPELINE_FILE

    if not os.path.isdir(skills_dir):
        sys.exit(f"ERROR: skills-dir not found: {skills_dir}")

    # Load pipeline
    try:
        with open(pipeline_file, encoding="utf-8") as fh:
            pipeline = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"ERROR: pipeline file not found: {pipeline_file}")
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: pipeline file is not valid JSON: {e}")

    # Validate pipeline slugs
    bad = validate_pipeline_slugs(pipeline, skills_dir)
    if bad:
        print(f"ERROR: pipeline references slugs not found in {skills_dir}:", file=sys.stderr)
        for slug in sorted(bad):
            print(f"  {slug}", file=sys.stderr)
        sys.exit(1)

    dag = pipeline.get("skills", {})

    print(f"{'APPLY' if apply else 'CHECK'}  skills-dir={skills_dir}\n")

    changed = ok = missing = skipped = 0

    for entry in sorted(os.scandir(skills_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        skill = entry.name
        if skill in PLUGIN_SKILLS:
            print(f"  SKIP     {skill:40s} (plugin — excluded)")
            skipped += 1
            continue

        path = os.path.join(entry.path, "SKILL.md")
        if not os.path.isfile(path):
            print(f"  MISSING  {skill:40s} (no SKILL.md)")
            missing += 1
            continue

        with open(path, encoding="utf-8") as fh:
            original = fh.read()

        next_edges = dag.get(skill, {}).get("next", [])
        tail_block = build_tail_block(skill, next_edges)
        new_text = inject(original, tail_block)

        if new_text == original:
            print(f"  ok       {skill}")
            ok += 1
        else:
            print(f"  CHANGE   {skill}")
            changed += 1
            if apply:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)

    print(f"\n{'wrote' if apply else 'would change'}: {changed}   already-ok: {ok}   "
          f"missing: {missing}   skipped: {skipped}")
    if not apply and changed:
        print("Run with --apply to write.")


if __name__ == "__main__":
    main()
