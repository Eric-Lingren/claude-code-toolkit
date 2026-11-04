# Change Record Contract

A change record says what a merged change touched. It is passive: written after the merge by
`scripts/attribution/write-change-records.py`, never read by anything that gates or alters a merge.
Schema: [change-record-schema.json](change-record-schema.json).

- **Location:** `attribution/change-records/<change_id>.json`, at the repo root and outside
  `claude-code-shared/`, so records are never published. They are kept apart from the seed bank, which
  holds only work to do (see ADR 0007).
- **Key:** `merged_sha`. `change_id` is `chg-` plus its first 12 hex characters. Re-running the writer
  never rewrites or duplicates an existing record.
- **Which merges:** merge commits on the first-parent line of main that change at least one skill,
  agent, script, hook or contract file under `claude-code-shared/`. Merges that only add learning
  rows, edit config or resources, change tests, or file issues get no record.
- **Fields:** see the schema. `sections` come from diff hunks: for each hunk in a markdown file, the
  heading the changed lines sit under. `touched_units` come from paths (`skill:<name>`,
  `agent:<name>`, `contract:<stem>`, `hook:<file>`, `script:<path under scripts/>`).
- **kind:** the first of skill, agent, contract, hook, script that the merge touches.
- **lane:** `manual` unless the merge carries a lane trailer (`Seed-Id` gives seed, `Learning-Ids` gives learnings).

- **ci_snapshot:** optional and informational, manual-lane records only. The writer looks up the Test
  snapshot workflow run for the PR's head sha (`gh run list --commit`) and downloads its
  `test-snapshot` artifact (`gh run download`), attaching `base.json`, `head.json` and `diff.json` plus
  `run_id` and `head_sha`. For a squash commit the head sha comes from `gh pr view`. No artifact, gh
  failing or missing, or being offline gives `ci_snapshot: null` and the record is still written;
  seed and learnings records omit the field. It never blocks anything. A null is final: records are
  never rewritten, so a later run does not fill it in. Nothing reads this field yet. Revisit this
  when something does.
