---
status: accepted
---

# Change records live in `attribution/`, apart from the seed bank

Change records are kept as committed files in `attribution/change-records/` at the repo root. The seed bank holds approved seeds waiting for the dispatcher, so records of finished work do not belong there. The root location keeps the records out of the published toolkit, and pushing a record does not start the publish workflow.

## Considered Options

- Deriving records from git on demand. Rejected: every field read today comes from git, but gate-time test snapshots are planned, and git cannot hold those. Revisit if those snapshots are dropped.
- Keeping records in `seed-bank/changes/`, under `claude-code-shared/`. Rejected: it mixes finished work with work to do, and it relies on the publish allowlist alone.

## Consequences

- `attribution/` is a protected path, because the regression detectors trust these records. `seed-bank/` is not protected, because seed status changes as work runs.
- Every script finds the records through one shared path helper. A wrong path reads as "no records" with no error, which would silence the alarm.
- Scripts stay in `claude-code-shared/` and are published. Only the data is private.
