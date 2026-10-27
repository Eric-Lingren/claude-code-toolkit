---
name: clean-scaffolding
description: Archive consumed doc scaffolding (seeds, PRDs, tasks, handoffs) in the current working tree. Bundles each fully-consumed lineage chain verbatim into docs/archive/. Leaves docs/adr/, docs/prototype/, docs/standups/, and docs/wizard/ untouched. Deletes browser-checks, task trace logs, and docs/visual-changes/<branch-slug>/ directories for branches whose PRs are merged — without archiving. Use when done with a workflow and ready to clear the clutter. Pass --force or -y to skip the confirmation prompt.
argument-hint: "--force or -y to skip confirmation"
model: haiku
effort: low
invokedBy: human
---

Archive consumed scaffolding artifacts from `docs/` in the current working directory.

This skill is **lineage-aware**: it groups artifacts by their provenance chain and only archives a chain atomically when the entire chain is consumed. It refuses partial operations that would leave dangling source refs.

**Three-way disposition:**
- **Archive set** (bundle then remove originals): `docs/seeds/`, `docs/prd/`, `docs/tasks/`, `docs/handoffs/`
- **Delete set** (remove without archiving): `docs/browser-checks/`, `docs/tasks/.logs/`, `docs/offload-output/`, `docs/visual-changes/<branch-slug>/` (each subdirectory is removed only when its branch's PR is merged; missing directory or unmerged/missing PR is a silent skip)
- **Untouched**: `docs/adr/`, `docs/prototype/`, `docs/standups/`, `docs/wizard/` (committed wizard snippets — durable example corpus, never archived or deleted)

## Process

### 1. Parse args

If args include `--force` or `-y`, set `skip_confirm = true`. Otherwise `skip_confirm = false`.

### 2. Preview (ALWAYS run this first)

Run:

```bash
bash ~/.dotfiles/claude-code-shared/skills/clean-scaffolding/scripts/clean-scaffolding.sh preview
```

This prints the full grouped file list and a total to the terminal.

- Exit code 2 (output `Nothing to clean.`): report `Nothing to clean.` and stop.
- Exit code 0: proceed to lineage analysis.

### 3. Lineage analysis

Run the lineage scanner to get a complete chain map in one call — do NOT read seed/task/handoff files individually during the scan phase:

```bash
python3 ~/.dotfiles/claude-code-shared/scripts/scaffolding/lineage-scan.py --root <project-root>
```

Use the JSON output for all chain traversal. The output shape is:

```json
{
  "chains": [
    {
      "seed": "20260606-foo.json",
      "prd": null,
      "tasks": "20260606-bar.json",
      "handoff": null,
      "status": "complete|partial"
    }
  ],
  "orphans": {
    "seeds": [],
    "tasks": [],
    "handoffs": []
  },
  "stats": {
    "total_seeds": 1,
    "total_tasks": 1,
    "complete_chains": 1,
    "partial_chains": 0
  }
}
```

**Interpret the output:**
- `status: "complete"` — every task in the chain's task file has `status: "done"` or `status: "merged"`. Ready to archive.
- `status: "partial"` — tasks still in progress, not started, or no task file yet. Not consumed.
- `orphans` — artifacts whose `source.ref` points to a seed not present in `docs/seeds/`. If every task in the orphan task file has `status: "done"` or `status: "merged"`, treat as ready to archive. Otherwise treat as partial (do not archive).

**A partial chain is NEVER archived.** If artifact B references artifact A via `source.ref`, and A would be archived but B would not (because B is outside the archive scope), refuse and explain the dangling ref.

### 4. Report chains

After analysis, categorize each chain:

- **Ready to archive**: fully consumed chains, plus orphan task files where all tasks are done/merged.
- **Not consumed**: tasks still in progress or not started.
- **Partial (dangling ref)**: a child artifact references something outside the chain.
- **Orphan (incomplete)**: orphan task file with tasks not yet done.

Report all categories to the user before asking to confirm.

### 5. Confirm

If `skip_confirm` is false, call `AskUserQuestion`:

- List only the chains in the "ready to archive" category and the count of files.
- Question: `Archive N files across M chains?`
- Options: `Yes, archive confirmed chains` / `No, cancel`

Stop if the user cancels or if no chains are ready to archive. If `skip_confirm` is true, skip this step.

### 6. Archive (atomic, per confirmed chain)

For each confirmed chain:

**Archive-set files** (seeds, tasks, handoffs, prd): collect all files in the chain and bundle them using the `archive-files` mode. The script writes a verbatim bundle to `docs/archive/<root-timestamp>-<slug>.json`, then removes the originals.

```bash
bash ~/.dotfiles/claude-code-shared/skills/clean-scaffolding/scripts/clean-scaffolding.sh \
  archive-files <file1> <file2> ...
```

**Delete-set files** (browser-checks, task trace logs): delete without archiving using `delete-files` mode:

```bash
bash ~/.dotfiles/claude-code-shared/skills/clean-scaffolding/scripts/clean-scaffolding.sh \
  delete-files <file1> <file2> ...
```

Both operations are atomic per chain sweep.

### 7. Report

Relay the script's `archived … to …`, `removed …`, and summary lines. Note any chains that were refused (not consumed or dangling ref) and what the user must do before those chains can be archived.

### 8. Clean merged visual-changes

Visual-changes directories hold ephemeral PNG evidence published per-branch during browser verification. They are never archived — delete only.

1. If `docs/visual-changes/` does not exist in the current working directory, skip this step silently.
2. List the immediate subdirectories of `docs/visual-changes/`. Each subdirectory name is the branch slug for a prior browser-verification run.
3. For each `<branch-slug>`:

   ```bash
   gh pr view <branch-slug> --json state 2>/dev/null
   ```

   - If the command fails (no PR found for that branch), skip silently.
   - If `state` is not `"MERGED"`, skip silently.
   - If `state` is `"MERGED"`, delete the directory:

     ```bash
     rm -rf docs/visual-changes/<branch-slug>/
     ```

4. If `docs/visual-changes/` is now empty after deletions, remove it too:

   ```bash
   rmdir docs/visual-changes/ 2>/dev/null || true
   ```

5. Report the count of directories removed (e.g. `Removed 2 visual-changes directories.`). If none were removed, say nothing — this is a silent pass.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `clean-scaffolding`.
<!-- skill-done: clean-scaffolding -->
<!-- learning-capture:end -->
