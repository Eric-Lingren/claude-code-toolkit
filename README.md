# Claude Code toolkit

My shared Claude Code toolkit. Every project I work in loads it, through both my personal and work Claude Code profiles.

I maintain it in a private repo. A script publishes the shareable parts to [claude-code-toolkit](https://github.com/Eric-Lingren/claude-code-toolkit) after scanning them for private details. The public copy is rebuilt on every sync, so I don't take issues or pull requests there.

It is infrastructure, not an app. Skills and agents are markdown. Contracts between stages are JSON Schema. Anything mechanical lives in a script, and anything that must always or never happen lives in a hook.

## The pipeline

Work moves through five stages. Each stage writes a file that the next stage reads, so nothing downstream rereads the chat.

| Stage | What happens | Main pieces |
|---|---|---|
| **Plan** | An interview settles the plan before any code. The plan is written to a seed file, then adversary agents try to refute it against the transcript and a judge panel rules on each refutation. | [`grill-with-docs`](skills/grill-with-docs/SKILL.md), [`to-seed`](skills/to-seed/SKILL.md), [`seed-review/`](agents/seed-review/) |
| **Build** | The seed becomes vertical-slice tasks. Each task gets one build runner, one worktree, and a red-green-refactor TDD loop. Separate read-only agents run lint, tests and browser checks, so the author never grades its own work. | [`to-tasks`](skills/to-tasks/SKILL.md), [`build-code`](skills/build-code/SKILL.md), [`tdd`](skills/tdd/SKILL.md), [`build/`](agents/build/) |
| **Review** | The diff is reviewed before a person sees it. Reviewer comments are triaged and each one is checked against the code before anything changes. | [`pr-code-review`](skills/pr-code-review/SKILL.md), [`pr-revise`](skills/pr-revise/SKILL.md) |
| **Learn** | Each correction is written down in a fixed schema. An attribution agent traces it back to the earliest stage that could have stopped it, and a grounding judge rejects any learning whose evidence is not in the artifact. | [`learning/`](agents/learning/), [`learning-schema.json`](contracts/learning-schema.json) |
| **Improve** | Learnings become edits to the skill that caused them. A benchmark runner scores the skill against synthetic scenarios with a judge panel, so I can see whether an edit helped. | [`improve-skill-learnings`](skills/improve-skill-learnings/SKILL.md), [`improve-skill-benchmarks`](skills/improve-skill-benchmarks/SKILL.md) |

Improve feeds back into Plan on the next run.

[`contracts/OVERVIEW.md`](contracts/OVERVIEW.md) maps how the seed, task and runner-result formats connect.

## Design choices

**Contracts at every boundary.** Stages and agents pass data in shapes defined under [`contracts/`](contracts/). Each contract is a readable spec plus a JSON Schema, and agents must produce exactly that shape.

**Model tier per stage, not per session.** [`resources/model-tiers.json`](resources/model-tiers.json) maps every skill and agent to a tier. Haiku runs lookups and read-only checks. Sonnet builds, reviews and writes plans. Opus is kept for planning interviews, hard debugging and architecture, where a wrong call is expensive. A sync script pushes tier changes into each definition.

**Hooks enforce what prompts cannot.** [`hooks/`](hooks/) runs in the harness around tool calls, so the model cannot talk its way past it. Guards block destructive git, file system, SQL and sed commands, data exfiltration, remote code execution, package publishing and edits on the trunk branch. Other hooks lint each edited file and nudge toward the right model tier.

**LLM judges are checked too.** Judges rule only on cited evidence, and deterministic scripts verify that quotes actually appear in the source. The judges have their own eval suites under [`evals/`](evals/), with baselines to compare against.

**Usage is measured.** A weekly report tracks model mix, estimated cost and whether each skill runs on its intended tier. See [`cc-usage-analytics`](skills/cc-usage-analytics/SKILL.md) and [`scripts/usage/`](scripts/usage/).

Longer reasoning for structural choices is in the ADRs under [`docs/adr/`](docs/adr/).

## Layout

| Path | What it holds |
|---|---|
| [`skills/`](skills/) | Skills, one folder each with a `SKILL.md`. |
| [`agents/`](agents/) | Subagents grouped by lane. [`agents/README.md`](agents/README.md) lists each one with its model and the skills that spawn it. |
| [`contracts/`](contracts/) | Specs and JSON Schemas for data passed between stages and agents. |
| [`hooks/`](hooks/) | Harness hooks: guards, linting, tier advice. |
| [`scripts/`](scripts/) | Every mechanical step: API calls, git work, report builders, validators. Tested under `scripts/tests/`. |
| [`evals/`](evals/) | Eval suites and baselines for the judge agents. |
| [`resources/`](resources/) | Config and reference files read by skills: model tiers, routing, repo policy, templates. |
| [`docs/adr/`](docs/adr/) | Architecture decision records. |
| [`CONTEXT.md`](CONTEXT.md) | Glossary of the terms used across the toolkit. |
| [`LICENSE`](LICENSE) | MIT. |
