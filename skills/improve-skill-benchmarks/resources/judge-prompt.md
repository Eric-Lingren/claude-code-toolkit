# Judge Prompt Template

Use this exact structure for each (scenario, assertion) pair. Spawn 3 judges per cell.

```
You are a judge. Score the output against this assertion.

## Output
<captured output from step 4a>

## Assertion
<assertion description>

## Rubric
0: <rubric anchor for 0>
25: <rubric anchor for 25>
50: <rubric anchor for 50>
75: <rubric anchor for 75>
100: <rubric anchor for 100>

Reply ONLY with JSON: {"score": <0|25|50|75|100>}
Failing scores (0 or 25) MUST include "reason": "one sentence"
```
