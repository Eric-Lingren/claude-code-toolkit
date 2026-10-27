---
name: resolve-ref-pattern
description: Standard pattern for routing docs/ file-path arguments through resolve-ref.sh before reading. All consumer skills (grill-me, grill-with-docs, to-seed, to-tasks, to-spec, build-code, dispatch-tasks, attribution-tracer) use this pattern.
---

# Resolve-ref pattern for docs/ file paths

Before reading any docs/ file argument, route its basename through `resolve-ref.sh`. This makes file reads transparent whether the file is active or archived.

## Steps

```bash
BASENAME=$(basename <path>)
bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh "$BASENAME"
```

## Outcomes

**Active hit (exit 0, output does NOT start with `ARCHIVE:`):**
- Output is the path to the active file.
- Use this path for a normal file read (Read tool or script).

**Archive hit (exit 0, output starts with `ARCHIVE:`):**
- Line 1: `ARCHIVE:<bundle-path>` (the archive bundle path).
- Lines 2+: the artifact's full verbatim content (JSON object for JSON files, string for .md files).
- Use this content in place of a direct file read. Do not attempt to read the original path.

**Not-found (exit non-zero):**
- The resolver printed a structured diagnostic to stderr naming the basename sought, dirs searched, and archive bundles scanned.
- Surface this diagnostic to the user verbatim.
- Prompt: "File not found. Continue anyway?" (AskUserQuestion with Yes / No options).
- **Yes (bypass):** rebuild context from the live conversation and continue without the file.
- **No:** stop. Do not proceed.
- The bypass UX lives in the calling skill, not in resolve-ref.sh.

## Notes

- Always use the basename (filename only, no directory prefix). resolve-ref.sh searches recursively — passing the full path is redundant and fragile.
- The pattern applies to: seed files, task files, handoff files, prd files. It does NOT apply to transcript paths (transcripts are never archived).
- Since nothing is ever deleted, a not-found is a pipeline-integrity bug. The bypass exists only to let the user override in exceptional cases.
