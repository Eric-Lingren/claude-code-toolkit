# Skill Architecture Contracts Overview

Three pipeline formats (seed, task, runner-result) plus three verification I/O contracts (refutation, verdict, persona-input).

## Format waists

```
SEED (seed-schema.json v2)          TASK (task-schema.json v1)         RUNNER-RESULT (runner-result-schema.json v1)
```

## Full skill interaction map

```
  [conversation / grill session]
          |
          v
       to-seed ─────────────────────────────────────┐
                                                     |
       prototype ─── (option 1: save as seed) ───────┤
                                                     |
                                              seed.json
                                            (docs/seeds/)
                                              /       \
                                             v         v
                                        to-tasks   to-spec
                                             ^         |
                                             |         | (embeds seed JSON)
                                             |         v
                                             └── HTML PRD (docs/prd/)
                                                 (to-tasks reads via
                                                  extract-prd-json.sh)

  debug ──────────────────────────────────────┐
  improve-component ──────────────────────────┤
  to-e2e-tasks ───────────────────────────────┤──► task.json ──► run-tasks ──► [TDD loop]
  to-tasks ───────────────────────────────────┤   (docs/tasks/)    |               |
  pr-code-review ─ (HITL vetting first) ───────┘                    |          lint-runner
                                                                    |          test-runner
                                                                    |          e2e-runner
                                                                    |               |
                                                                    |    runner-result verdict
                                                                    |    (inline JSON, schema v1)
                                                                    |
                                                             run-task-followups
                                                             tasks-to-linear
```

## Format details

| Format | Schema | Version | Naming script |
|---|---|---|---|
| Seed | `seed-schema.json` | `"2"` | `scripts/scaffolding/doc-filename.sh <slug> json` |
| Task | `task-schema.json` | `"1"` | `scripts/scaffolding/task-filename.sh <slug>` |
| Runner result | `runner-result-schema.json` | `"1"` | n/a (inline agent response) |

## Validation

Every format-touching skill has a `## Contract` section with a Step-0 bash invocation:

```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/<format>-schema.json \
  <file-path>
```

Exit 0 = valid. Non-zero = STOP, report stderr, do not proceed.

## Contract files

### Pipeline formats

- [task-contract.md](task-contract.md) + [task-schema.json](task-schema.json)
- [seed-contract.md](seed-contract.md) + [seed-schema.json](seed-schema.json)
- [runner-result-contract.md](runner-result-contract.md) + [runner-result-schema.json](runner-result-schema.json)

### Verification I/O (to-seed adversary panel)

- [refutation-contract.md](refutation-contract.md) + [refutation-schema.json](refutation-schema.json) — persona output: array of refutation objects or error-form
- [verdict-contract.md](verdict-contract.md) + [verdict-schema.json](verdict-schema.json) — judge output: single verdict or error-form
- [persona-input-contract.md](persona-input-contract.md) — orchestrator-to-persona/judge input shape (transcript path, seed JSON, disposed-ids lock list)

## Interactive diagram

[overview.html](overview.html) — full pipeline flowchart with subgraph layers, schema contracts, and skill nodes.
