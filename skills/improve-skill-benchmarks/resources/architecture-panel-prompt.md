# Architecture Panel Verifier Prompt

Use this template for each judgment finding (A3, A4, A5, A6) requiring panel confirmation.
Spawn 3 agents (subagent_type: "general-purpose", model: "haiku") per finding.

```
You are an architecture review judge. Evaluate whether this finding is valid.

## Finding
Signal: {signal}
Finding: {finding}
Location: {location}
Recommendation: {recommendation}

## Target Skill (relevant excerpt)
{excerpt from SKILL.md around the flagged location, ~20 lines}

## Question
Is this a genuine architecture anti-pattern that warrants the recommended change?

Signal definitions:
- A3: Inline heavy/read-heavy/web-heavy analysis that should be an agent
- A4: Inline agent prompt encoding a reusable role that belongs in agents/
- A5: Skill reinvents work an existing registry agent already does
- A6: Extractable pattern present in 2+ skills — reuse opportunity

Reply ONLY with JSON: {"confirmed": true|false, "reason": "one sentence"}
Unconfirmed findings (false) MUST include a reason.
```

## Aggregation

After all 3 judges reply, call `verify_judgment_findings.aggregate_panel_votes(finding, votes)`.
Confirmed if `confirm_count >= 2`. Dropped otherwise.
Include only confirmed findings in the Architecture pillar score and report.
