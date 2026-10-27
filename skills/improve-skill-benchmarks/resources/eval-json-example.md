# Eval JSON Example

## Assertion format

```json
{
  "name": "snake_case_short_name",
  "description": "Natural language description of what the output must demonstrate.",
  "rubric": {
    "0": "Concrete description of what a 0 score looks like",
    "25": "Concrete description of what a 25 score looks like",
    "50": "Concrete description of what a 50 score looks like",
    "75": "Concrete description of what a 75 score looks like",
    "100": "Concrete description of what a 100 score looks like"
  }
}
```

## Scenario format

```json
{
  "name": "snake_case_scenario_name",
  "prompt": "The full prompt/task to give the skill",
  "context": "Optional setup context (language, framework, project type)"
}
```
