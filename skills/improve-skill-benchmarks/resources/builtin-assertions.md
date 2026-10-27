# Built-in Consistency Assertions

Always include these 3 assertions in eval.json. Use the exact names and rubrics below. Never rename or substitute them.

```json
{
  "name": "number_consistency",
  "description": "All counts, quantities, list lengths, and numeric references are internally consistent. If the output says '5 items' then exactly 5 items appear. Step numbers are sequential with no gaps or duplicates.",
  "rubric": {
    "0": "Multiple numeric contradictions: stated counts don't match actual counts, step numbers skip or repeat, quantities referenced earlier differ from later usage",
    "25": "One clear numeric mismatch: a stated count disagrees with actual items, or a step number is wrong",
    "50": "Numbers are technically correct but ambiguous: vague quantities like 'several' alongside exact counts, or implicit counts that require reader inference to verify",
    "75": "All numbers consistent with at most one trivially unclear reference that does not mislead",
    "100": "Every count, step number, list length, and numeric reference is exact and verifiable against the output content"
  }
},
{
  "name": "no_contradictions",
  "description": "No statement in the output contradicts another statement in the same output. Instructions do not conflict. Constraints stated in one section are not violated in another.",
  "rubric": {
    "0": "Direct contradictions: output says X in one place and not-X in another, or gives conflicting instructions for the same action",
    "25": "One clear contradiction or two statements that are difficult to reconcile without assuming one is wrong",
    "50": "No direct contradictions but some tension between sections that could confuse a reader about intended behavior",
    "75": "Fully consistent with at most one minor ambiguity that does not create a real conflict",
    "100": "Zero contradictions. Every statement is compatible with every other statement. Constraints are honored throughout"
  }
},
{
  "name": "plan_cohesion",
  "description": "The output presents a cohesive path from start to finish. Steps build on each other logically. The roadmap, plan, or sequence has no orphaned steps, circular dependencies, or gaps where the reader must guess how to get from A to B.",
  "rubric": {
    "0": "Disjointed output: steps reference things not yet introduced, sections appear unrelated, or the overall flow has no clear direction",
    "25": "General direction exists but significant gaps: a step depends on something never mentioned, or sections are loosely connected with missing transitions",
    "50": "Flow is followable but has one structural gap or an orphaned section that does not connect to the rest",
    "75": "Cohesive flow with clear progression. At most one minor ordering issue that does not block understanding",
    "100": "Fully cohesive: each section builds on prior context, dependencies are introduced before use, and the output reads as a unified whole"
  }
}
```
