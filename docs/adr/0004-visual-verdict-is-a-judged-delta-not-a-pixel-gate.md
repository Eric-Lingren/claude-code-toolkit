---
status: accepted
---

# Visual verdict is a vision-judged delta, not a pixel-diff gate

Baseline is captured at the feature branch's merge-base and compared with the Candidate. The pixel diff is only a signal. A zero diff with no intended change auto-passes. Otherwise one Sonnet screener judges, and any non-`expected` result goes to a 3-Sonnet panel (2-of-3). The rubric judges only the change, never aesthetics. Only desktop `regression` blocks; mobile regression and `unexpected`/`uncertain` become `needs_eyes` for async review.

## Considered Options

- Committed golden screenshots (`toHaveScreenshot`). Rejected: churn, flakiness with live data, and violates ADR-0003.
- Pixel-diff threshold as pass/fail. Rejected: cannot tell intended change from regression.
- Escalating uncertain cases to a single Opus judge. Rejected: a false block costs a full retry loop, and three independent Sonnet votes reduce false blocks more cheaply.
- Comparing against current `main`. Rejected: teammates' merged changes would show up as diffs.
