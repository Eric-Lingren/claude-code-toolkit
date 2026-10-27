# CONTEXT — claude-code-shared

This repo is the shared Claude Code tooling layer: skills, agents, contracts, hooks,
resources, and scripts consumed by both the `.cch` (personal) and `.cco` (work) Claude
Code profiles. It is infra, not a product codebase — there is no runtime app here, only
markdown-defined behaviors, JSON registries/contracts, and small executable scripts that
enforce or automate them.

## Noun glossary

- **Skill** — a directory under `skills/<name>/` containing a `SKILL.md` (frontmatter +
  instructions) that Claude loads on trigger match. May include `scripts/`, `resources/`,
  `assets/`, or `runs/` subdirectories per `resources/skill-directory-conventions.md`.
  Registered in `resources/model-tiers.json` under `skills`.
- **Agent** — a subagent defined by a `.md` file under `agents/<group>/<name>.md` (frontmatter:
  name, description, tools, model) that a skill spawns via the Agent tool for a bounded,
  stateless piece of work (e.g. `context-loader`, `lint-runner`, `browser-checker`).
  Every agent must be listed in `agents/registry.json`, which is the **source of truth**
  for what agents exist, their model, and their consumer skills.
- **Contract** — a paired `.md` (human-readable spec) + `.json` schema file under
  `contracts/` that defines the exact shape of data passed between a skill and an agent,
  or between pipeline stages (e.g. `seed-contract.md`/`seed-schema.json`,
  `task-contract.md`/`task-schema.json`, `runner-result-contract.md`/`-schema.json`).
  Agents and skills must produce/consume data matching these shapes exactly — contracts
  are the interface boundary, not documentation of intent.
- **Hook** — a shell/python script under `hooks/` wired into a Claude Code profile's
  `settings.json` (`PreToolUse`, `Stop`, etc.) that runs automatically around tool calls
  (e.g. `block-destructive-git.sh`, `tier-advisor.sh`, `stop-hook.py`). Hooks are the only
  mechanism for enforcing "always/never" behaviors — they run in the harness, not as
  model-followed instructions, so they can't be talked out of firing.
- **Resource** — a reference file under `resources/` (or a skill-local `resources/`)
  that a skill or agent reads for extra structured context: format guides, runbooks,
  or config-shaped JSON (`model-tiers.json`, `task-routing.json`, `repo-policy.json`).
  Not user-facing docs — these are inputs to skill/agent logic.
- **Registry** — `agents/registry.json`. The authoritative list of every agent: name,
  file path, model, description, and consumer skills. Anything spawning an agent not
  listed here is spawning an unregistered agent; `register-skill` exists to keep this
  file (and `model-tiers.json`) in sync when a new skill or agent is added.
- **Pipeline stage** — one node in the workflow DAG defined by `skill-pipeline.json`
  (`skills[<slug>].next: [{skill, when}]`). Encodes which skill logically follows
  another (e.g. `to-seed` → `to-tasks` → `dispatch-tasks`) so `inject-learning-tail.py`
  can bake an accurate "next step" suggestion into each skill's closing output.
- **Tier** — a model+effort pairing (T1–T4) defined in `resources/model-tiers.json`,
  keyed by skill name (`skills` map) or agent name (`agents` map). T1 = haiku/low
  (lookup), T2 = sonnet/medium (mechanical), T3 = sonnet/high (session default,
  context-aware build), T4 = opus/xhigh (deep reasoning). `scripts/registration/sync-model-tiers.py`
  is the only thing that should propagate tier changes into skill/agent frontmatter.

## Browser verification glossary

- **Baseline** — the "before" render of a route, captured by replaying the same
  check against the feature branch's merge-base with the base branch (default) or, as fallback, against the working tree
  before the change is built. A new route has no Baseline.
  _Avoid_: golden, snapshot (unless meaning committed Playwright golden files).
- **Candidate** — the "after" render of the same route and steps, captured on the
  feature branch.
- **Visual diff** — the pixel-level delta between Baseline and Candidate. A signal
  fed to the judge, never a pass/fail gate on its own.
- **Visual verdict** — a vision-model judgment of whether the Baseline→Candidate
  change matches the intended change and nothing else regressed. One of `expected`,
  `regression` (blocks), `unexpected`, or `uncertain` (both flag `needs_eyes`).
  Decided by a single screener judge; any non-`expected` screener result goes to a
  3-judge panel, 2-of-3 majority, no majority means `uncertain`. Judges the delta
  only, never aesthetics: flaws already present in the Baseline are not findings.
  Mobile-viewport `regression` is advisory (downgraded to `needs_eyes`); only desktop
  `regression` blocks.
  _Avoid_: as_intended, pass (for visual outcomes).
- **Check Spec** — the declarative, repeatable definition of one browser check: Role,
  viewports, and ordered steps (navigate, interact, capture, expect). Executed
  identically against Baseline and Candidate by a deterministic runner. Ephemeral:
  lives in excluded scaffolding, never committed to the target repo.
  _Avoid_: assertions (as the whole check), check script.
- **Planned check** — a Check Spec authored at planning time from seed intent.
- **Derived check** — a Check Spec inferred (Opus tier) from an FE-touching or
  response-shape-changing diff when no Planned check exists. Intent is unknown, so any
  visible change resolves to `needs_eyes`, not `expected`.
- **Unchecked consumer** — a route rendering a changed shared component beyond the
  derivation cap; listed in the Verification Report, not checked.
- **Exploratory pass** — a bounded, live-browser agent session run only to diagnose a
  failed step or uncertain Visual verdict. Produces explanation, never a verdict.
- **Verification Report** — one private artifact per build run showing Baseline,
  Candidate, and Visual diff per capture with verdicts. Fills live during the run;
  the reviewer can dismiss or reject (with a note) each capture. Never blocks the run.
  Rejects are harvested by `pr-revise` as a feedback source alongside PR comments.
- **needs_eyes** — task state for a check whose Visual verdict is `unexpected` or
  `uncertain`. Awaits async review in the Verification Report; does not block.
- **Publication** — automatic end-of-run export of every kept Baseline/Candidate pair
  plus a paste-ready `## Visual changes` snippet to `docs/visual-changes/<branch-slug>/`
  for manual attachment to the PR. Never pushed; deleted once the PR merges.
- **Base server** — one fresh app instance serving the merge-base SHA, shared by every
  check in a build run and discarded at run end. Source of all Baselines for that run.
  Runs locally or in a fresh Amp orb, per the repo's verify host.
- **Verify host** — where the app stack and browser run for checks: `local` or
  `amp-orb`. Set per repo in `repo-policy.json`. Planning, judging, and the report
  always stay local.
- **Final sweep** — re-running every Check Spec from a build run against the final
  branch head, to catch cross-task regressions per-task checks miss.
- **Auth Profile** — per-repo declaration of how to obtain an authenticated browser
  state for each Role. Strategy order: project-owned auth setup, then API login, then
  scripted form login, then human-seeded session. Holds no secrets; creds stay with
  the project (`.env.local`) or Keychain. Lives in dotfiles (`repo-policy.json`), never
  in the target repo.
- **Role** — a named identity an Auth Profile can produce state for (e.g. `admin`,
  `firm`, `trial`). Each Role has its own storage state.
- **Freshness probe** — a pre-run check that a Role's stored state still reaches a
  protected page. Stale triggers regeneration; unrecoverable yields `skipped:
  auth_expired`, never a failure.
- **Seed hook** — pre-check calls an Auth Profile declares so Baseline and Candidate
  render against identical data.

## Load-bearing invariants

1. **Absolute-path script references.** Skills and agents must invoke shared scripts
   via the absolute path `~/.dotfiles/claude-code-shared/scripts/<name>` (or the fully
   resolved `/Users/<user>/.dotfiles/...` form), never a relative path or a guessed
   alternate location (`~/.cch/`, `~/.cco/`, `~/.claude/`). If a referenced script is
   missing at that path, stop and surface it — don't silently fall back elsewhere.
2. **`agents/registry.json` is the source of truth for agents.** Every agent that can be
   spawned must have an entry here (name, file, model, description, consumers). Tooling
   (tier-advisor, weekly usage report, sync-model-tiers) reads this file, not the agent
   `.md` frontmatter, to know an agent exists.
3. **`resources/model-tiers.json` keys skills and agents by name.** Both the `skills`
   map and `agents` map must stay in sync with what's actually registered/present on
   disk, or tier-advisor and the usage report will silently miss items. Apply changes
   via `scripts/registration/sync-model-tiers.py --apply`, not by hand-editing frontmatter alone.
4. **`.cch` and `.cco` are symlink farms into this repo, not independent copies.**
   `~/.cch/skills` and `~/.cco/skills` symlink to `~/.claude-code-shared/skills` (itself
   pointing at `claude-code-shared/` in this dotfiles repo), and likewise for `agents/`
   and `settings.json`. There is exactly one copy of every skill/agent/setting; editing
   under `.cch/` or `.cco/` directly edits this repo through the symlink.
5. **Hooks are wired via `settings.json`, not auto-discovered.** A script dropped into
   `hooks/` does nothing until it's registered under the matching `PreToolUse`/`Stop`
   array in `settings.json` (see `block-destructive-git.sh`, `tier-advisor.sh`, etc. in
   the current wiring). Adding a hook file without wiring it is a no-op.
6. **Contracts gate agent I/O.** A skill spawning an agent must shape its prompt to
   match the agent's expected input, and treat the agent's response as matching the
   contract's output schema — validate against `contracts/<name>-schema.json` when in
   doubt, not against what the agent happened to return.

## Context Sources

- `agents/registry.json` — canonical agent list
- `resources/model-tiers.json` — tier assignments for skills and agents
- `skill-pipeline.json` — pipeline stage DAG
- `resources/skill-directory-conventions.md` — skill subdirectory layout rules
- `resources/repo-policy.json` — per-repo branching/domain policy consumed by branching flows
