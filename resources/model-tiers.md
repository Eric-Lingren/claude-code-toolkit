# Model tiering for skills and agents

Each skill and agent runs on a deliberate model (+ effort for skills), chosen by
cognitive complexity. One config drives it all. Skills and agents can never drift
from that config.

## The tiers

| Tier | Model | Effort | For |
|------|-------|--------|-----|
| T1 | haiku | low | Pure lookup. No reasoning. (`how-to`, `find-work`) |
| T2 | sonnet | medium | Mechanical, narrow scope. (`tasks-to-linear`, `run-task-followups`) |
| T3 | sonnet | high | Standard build. Default tier for unlisted skills. (most skills) |
| T4 | opus | xhigh | Deep reasoning. (`grill-me`, `grill-with-docs`, `debug`, `improve-codebase-architecture`) |

Session default is set in `settings.json` (`"model": "opus"`, `"effortLevel": "high"`), not by
a tier. Ad-hoc prompts run there. Skills override model and effort via their own frontmatter.
Model aliases resolve through the pinned `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` env vars.

Agent files may live in subdirectories of `agents/`. The sync finds them by filename.

## The agents map

`model-tiers.json` contains two top-level maps: `"skills"` and `"agents"`.

```json
{
  "skills": { "how-to": "T1", ... },
  "agents": { "context-loader": "T1", ... }
}
```

**Agents consume model only.** Effort is a session concept (controlled by the
user before invoking a skill). It has no meaning for spawned agents, which are
always called by skills, not by users. So `sync-model-tiers.py` stamps only
`model:` into agent frontmatter — never `effort:`.

When you assign a tier to an agent, you are declaring the model that agent's
`.md` frontmatter and `registry.json` entry should carry. The sync enforces
consistency between the tier config, the frontmatter, and the registry.

## Moving parts

| File | Role |
|------|------|
| `resources/model-tiers.json` | **Source of truth.** Tier definitions + per-skill assignment + per-agent assignment + delegate list. |
| `scripts/registration/sync-model-tiers.py` | Stamps `model:`/`effort:` into each skill's frontmatter + `model:` into each agent's frontmatter + updates `registry.json` model fields. Injects delegation blocks into delegate-list skills. |
| `.githooks/pre-commit` | Auto-runs the sync on every commit. Re-stages anything it changed. No drift, ever. |
| `hooks/tier-advisor.sh` | `UserPromptSubmit` advisory. Nudges `/model opus` (deep) or `/model haiku` (lookup). |
| each `skills/*/SKILL.md` | Carries explicit `model:`/`effort:` (stamped, do not hand-edit). |
| each `agents/*.md` | Carries explicit `model:` (stamped, do not hand-edit). |
| `agents/registry.json` | `model` field for each agent is kept in sync by the sync script. |

## Re-tiering a skill or agent

1. Edit `resources/model-tiers.json` (move a skill/agent between tiers, or change what a tier's model/effort is).
2. `python3 ~/.dotfiles/claude-code-shared/scripts/registration/sync-model-tiers.py --check`  (preview)
3. `... --apply`  (write).

Or just edit the json and commit. The pre-commit hook applies it for you.

## Delegation

Skills in the config's `delegate` list get a block telling them to push menial
read-only lookups (grep/glob/read-many/fetch) to the `caveman:cavecrew-investigator`
subagent (Haiku) instead of burning the session model. A thin backstop rule lives
in the global `CLAUDE.md`.

Agents are never in the `delegate` list — delegation is a skill-body concept.

## The advisory hook

`hooks/tier-advisor.sh` fires on each prompt:
- Deep-reasoning signals (architecture, hard debug, tradeoffs) -> suggests `/model opus`.
- Pure lookups (`where is`, `how do i`, `list all`) with no action verb -> suggests `/model haiku`.
- Skill invocations -> reads the tier from `model-tiers.json` and nudges the correct model + effort.
- Everything else -> silent.

It never switches the model itself (hooks can't set the current turn's model). It
only nudges. Disable with `export CLAUDE_TIER_ADVISOR=0`.

## Fresh clone setup (one-time)

The pre-commit hook is tracked but activated by repo-local config. After cloning:

```bash
git config core.hooksPath .githooks
```

Skip the hook for one commit with `git commit --no-verify`.
