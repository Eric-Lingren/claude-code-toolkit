---
name: persona-input-contract
description: Contract defining the input the orchestrator must provide to each adversary persona and judge agent during the to-seed verification stage.
---

# Persona and Judge Input Contract

**Producer:** to-seed verification stage orchestrator
**Consumer:** persona-grounding, persona-accuracy, persona-completeness, persona-coherence, persona-judge

## Persona input (all 4 adversary personas)

The orchestrator must pass the following as the agent prompt:

```
## Seed

seed_path: <absolute path to the seed JSON file produced in step 3a>

## Transcript

transcript_path: <absolute path to the cleaned transcript file produced by filter-session-transcript.sh>

## Disposed-id lock list

The following thread ids are disposed and must never be re-raised under any name:
<comma-separated list of disposed thread ids from seed.disposed_threads[*].id>
```

### Requirements

- **Seed**: a file path only. The persona uses Read to load the full JSON. Not inline JSON. Not a summary. The path must point to the temp seed file written by the orchestrator in step 3a.
- **Transcript**: a file path only. The persona uses Grep and Read to locate relevant spans. The path must point to the output of `filter-session-transcript.sh`, not the raw session JSONL.
- **Disposed-id lock list**: the `id` values from all entries in `seed.disposed_threads`. A persona that raises a refutation with a disposed id is producing an invalid result — the orchestrator discards it without judging.

## Judge input (batched)

The judge is no longer spawned per refutation. It receives the whole batch plus a windowed evidence pack — the round-1 screener over all refutations, then each of the 3 round-2 panelists over the upheld subset. Each judge call receives:

```
## Refutations

<JSON array of refutation objects, each carrying a stable ref_id>

## Evidence pack

evidence_pack_path: <absolute path to the windowed pack produced by window-transcript-spans.sh>

## Transcript (escape hatch)

transcript_path: <absolute path to the cleaned transcript — judge greps this only when far-context is needed>

## Seed (read-only context)

seed_path: <absolute path to the seed JSON file produced in step 3a>
```

### Requirements

- **Refutations**: a JSON array. Each object carries a `ref_id` minted by the orchestrator before the judge stage. The judge returns exactly one verdict per `ref_id`.
- **Evidence pack**: a file path only. One `## <ref_id>` section per refutation holding the windowed transcript context around its span (or an `ABSENCE CLAIM` / `SPAN NOT FOUND IN TRANSCRIPT` marker). This is the judge's default evidence and the windowing optimization: judges rule from the small pack instead of re-ingesting the full transcript once per refutation.
- **Transcript**: a file path only, the same cleaned transcript the personas used. It is an escape hatch, not the default. The window only shows context immediately around the span. When a verdict turns on far-context (stale-resolution, out-of-context), the judge greps the full transcript before ruling. Greps are targeted; the judge does not read the whole file top-to-bottom. This closes the windowing blind spot while keeping the common-case cost low.
- **Seed**: a file path only. Read-only — the judge reads the file and must not propose changes to the seed.

## Cleaned transcript path

The cleaned transcript is produced by running:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/seed-review/filter-session-transcript.sh <output-path>
```

before spawning any persona. The path is reused for all personas and all judge instances in the same verification run. The orchestrator is responsible for:
1. Resolving the session JSONL (via `CLAUDE_CODE_SESSION_ID` + `CLAUDE_CONFIG_DIR`, or `transcript_path` env var)
2. Running the pre-filter script once
3. Passing the output path to every agent in the run
4. Deleting the temp file after the verification stage completes
