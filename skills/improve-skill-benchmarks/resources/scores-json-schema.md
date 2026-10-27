# scores.json Schema

Append each run result to the `runs` array:

```json
{
  "runs": [
    {
      "timestamp": "<ISO 8601>",
      "iterations": "<count>",
      "exit_reason": "strong_score|plateau|max_iterations",
      "behavioral_score": 82,
      "structural_score": 60,
      "scores": [
        {
          "iteration": 1,
          "total_cells": 15,
          "average_score": 58,
          "cell_scores": [
            {
              "scenario": "...",
              "assertion": "...",
              "score": 75,
              "judge_scores": [75, 75, 50],
              "reason": "null or failure reason"
            }
          ]
        }
      ],
      "structural_recommendations": [
        {
          "item": "token_weight",
          "pass": false,
          "detail": "5.2k tokens total, over 5k threshold. Extract examples to reduce by ~800 tokens."
        }
      ],
      "promotions": ["list of assertions promoted to SKILL.md"],
      "learnings_added": 3,
      "learnings_pruned": 1,
      "architecture": {
        "architecture_score": 71.4,
        "signals": {
          "A1": true,
          "A2": true,
          "A3": false,
          "A4": false,
          "A5": true,
          "A6": true,
          "A7": true
        },
        "recommendations": [
          {
            "signal": "A4",
            "finding": "Inline judge prompt encoding a reusable role",
            "location": "skills/improve-skill-benchmarks/SKILL.md:127",
            "recommendation": "Extract to agents/skill-judge.md",
            "proposed_agent": "skill-judge",
            "consumers": ["improve-skill-benchmarks"],
            "benefit": "Reusable agent; inline prompt eliminated.",
            "effort": "medium",
            "lifecycle": "NEW"
          }
        ]
      }
    }
  ]
}
```

## Architecture block

The `architecture` key is optional on older runs. Present on all runs after Step 6 was added.

| Field | Type | Description |
|---|---|---|
| `architecture_score` | float 0–100 | `confirmed_passed / 7 * 100` |
| `signals` | object | Per-signal A1–A7 pass (true) / fail (false) |
| `recommendations` | array | Confirmed findings with lifecycle labels |

### Recommendation record

| Field | Type | Notes |
|---|---|---|
| `signal` | string | A1–A7 |
| `finding` | string | One sentence |
| `location` | string | `path:line` |
| `recommendation` | string | One sentence |
| `proposed_agent` | string\|null | null for non-extraction findings |
| `consumers` | array\|null | Skills that would consume proposed agent |
| `benefit` | string | Value of the change |
| `effort` | string | low\|medium\|high |
| `lifecycle` | string | NEW \| PERSISTING \| RESOLVED |
```
