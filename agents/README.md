# Agents

Generated from `registry.json` by `scripts/registration/gen-agents-readme.py`. Do not edit by hand.

33 agents across 7 lanes.

Skill-agents run the same-named skill in an isolated context window.

## build

| Agent | Model | Consumers |
|---|---|---|
| [browser-checker](build/browser-checker.md) | haiku | build-runner, debug |
| [build-runner](build/build-runner.md) | sonnet | build-code |
| [e2e-runner](build/e2e-runner.md) | haiku | - |
| [lint-runner](build/lint-runner.md) | haiku | build-runner |
| [test-runner](build/test-runner.md) | haiku | build-runner |
| [visual-judge](build/visual-judge.md) | sonnet | build-runner |

## egress

| Agent | Model | Consumers |
|---|---|---|
| [answer-composer](egress/answer-composer.md) | sonnet | - |
| [export-tasks](egress/export-tasks.md) | sonnet | dispatch-tasks |
| [export-tasks-github](egress/github/export-tasks-github.md) | haiku | export-tasks |
| [export-tasks-linear](egress/linear/export-tasks-linear.md) | haiku | export-tasks |
| [export-tasks-notion](egress/notion/export-tasks-notion.md) | haiku | export-tasks |
| [post-github](egress/github/post-github.md) | haiku | relay |
| [post-linear](egress/linear/post-linear.md) | haiku | relay |
| [post-slack](egress/slack/post-slack.md) | haiku | relay |

## investigate

| Agent | Model | Consumers |
|---|---|---|
| [investigator](investigate/investigator.md) | opus | investigate, pr-code-review, pr-revise |
| [investigator-code](investigate/investigator-code.md) | sonnet | investigator |
| [investigator-github](investigate/investigator-github.md) | sonnet | investigator |
| [investigator-linear](investigate/investigator-linear.md) | sonnet | investigator |
| [investigator-notion](investigate/investigator-notion.md) | sonnet | investigator |
| [investigator-web](investigate/investigator-web.md) | sonnet | investigator |

## learning

| Agent | Model | Consumers |
|---|---|---|
| [artifact-grounding-judge](learning/artifact-grounding-judge.md) | haiku | attribution-tracer |
| [attribution-tracer](learning/attribution-tracer.md) | sonnet | debug, pr-code-review |
| [capture-learning](learning/capture-learning.md) | sonnet | build-code, cc-usage-analytics, clean-scaffolding, debug, find-work, grill-me, grill-with-docs, handoff, how-to, improve-codebase-architecture, improve-component, improve-skill-benchmarks, improve-skill-learnings, pr-code-review, prototype, register-skill, run-task-followups, tasks-to-linear, tdd, tldr-tech, to-e2e-tasks, to-seed, to-spec, to-tasks |

## seed-review

| Agent | Model | Consumers |
|---|---|---|
| [persona-accuracy](seed-review/persona-accuracy.md) | haiku | to-seed |
| [persona-coherence](seed-review/persona-coherence.md) | haiku | to-seed |
| [persona-completeness](seed-review/persona-completeness.md) | haiku | to-seed |
| [persona-grounding](seed-review/persona-grounding.md) | haiku | to-seed |
| [persona-judge](seed-review/persona-judge.md) | sonnet | to-seed |

## shared

| Agent | Model | Consumers |
|---|---|---|
| [architecture-auditor](shared/architecture-auditor.md) | sonnet | improve-skill-benchmarks, register-skill |
| [context-loader](shared/context-loader.md) | haiku | improve-codebase-architecture, improve-component, to-e2e-tasks, to-spec, to-tasks |

## skill-agents

| Agent | Model | Consumers |
|---|---|---|
| [build-code](skill-agents/build-code.md) | sonnet | dispatch-tasks, sprout-seed |
| [pr-code-review](skill-agents/pr-code-review.md) | sonnet | pr-revise, sprout-seed |
| [to-tasks](skill-agents/to-tasks.md) | sonnet | sprout-seed |
