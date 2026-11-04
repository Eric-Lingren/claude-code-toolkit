# Change-record commit trailers

`scripts/attribution/write-change-records.py` decides each change record's `lane` and the lane-specific
`source_refs` from commit trailers. Any skill that opens an automated PR (the Phase 2 lane skills)
must emit exactly these trailers so the record lands in the right lane.

## The two trailers

| Trailer | Lane | Value | Effect on the record |
|---|---|---|---|
| `Seed-Id: <id>` | `seed` | the seed's id (its file basename without `.json`, e.g. `20261103-1510-phase-1-attribution-spine`) | the id is added to `source_refs` |
| `Learning-Ids: <id>,<id>` | `learnings` | comma-separated learning ids from one cluster commit | each id is added to `source_refs` |

A change with neither trailer is lane `manual`: the user's own hand-made PR. Seed-Id wins when
both appear.

## Rules

- Put the trailer on its own line, `Key: value`, in the commit message body (blank line before
  it, like `Co-authored-by`). The key is matched at the start of a line, case-insensitively, but
  emit it exactly as written above.
- One `Learning-Ids` line holds one cluster's ids, comma-separated. Repeating the line is also
  read, and spaces after commas are fine. Do not use any other separator.
- Ids go into `source_refs` verbatim, so use the id exactly as it appears in its source file.
- Each id must match `^[A-Za-z0-9._-]{1,80}$`. The writer drops any id that does not (it never sets the lane or reaches `source_refs`), so a trailer with only invalid ids leaves the lane `manual`.
- The writer reads the trailer from the commit itself (squash merge), from the merge commit
  message, and from any commit the merge brought in (merge commit). Putting it on the branch
  commit is enough.
- No other trailer changes the lane.

## Merge vs squash

Hand-made dotfiles PRs land as merge commits (`Merge pull request #95 from ...`). The record
stores the merge commit as `merged_sha` and its first parent as `base_sha`. A squash-merged PR
is a single commit on main: that commit is `merged_sha` and its parent is `base_sha`. A
`(#123)` suffix on a squash subject becomes a `pr:123` entry in `source_refs`.

## Example

```
Fold the repeated debug-loop correction into the debug skill

Learning-Ids: L-0412,L-0418,L-0431
```

```
Add attribution spine

Seed-Id: 20261103-1510-phase-1-attribution-spine
```
