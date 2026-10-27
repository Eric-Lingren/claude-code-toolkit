# Consistency Auditor Prompt Template

Spawn one agent (model: sonnet) per scenario output after all per-assertion judges complete.

```
You are a consistency auditor. Review this skill output for internal logical consistency.

## Output
<captured output from step 4a>

Check each category. For each, reply with a score (0/25/50/75/100) and a one-sentence reason if score <= 50.

1. **Numeric consistency:** Do all stated counts, list lengths, step numbers, and quantities match what actually appears? Are step numbers sequential with no gaps or duplicates?
2. **No contradictions:** Does any statement contradict another? Do instructions conflict? Are constraints honored throughout?
3. **Plan cohesion:** Do steps build on each other? Are there orphaned sections, circular dependencies, or gaps in the logical flow?
4. **Terminology consistency:** Are the same concepts referred to with the same terms throughout? No unexplained synonyms or name drift?
5. **Scope alignment:** Does the output stay within what was asked? Does it promise deliverables it never provides, or provide things it never mentioned?

Reply ONLY with JSON:
{
  "numeric_consistency": {"score": <0-100>, "reason": "...or null"},
  "no_contradictions": {"score": <0-100>, "reason": "...or null"},
  "plan_cohesion": {"score": <0-100>, "reason": "...or null"},
  "terminology_consistency": {"score": <0-100>, "reason": "...or null"},
  "scope_alignment": {"score": <0-100>, "reason": "...or null"}
}
```

Merge consistency scores: for `number_consistency`, `no_contradictions`, `plan_cohesion`, average the per-assertion judge score with the corresponding cross-check score, then snap. The extra dimensions (`terminology_consistency`, `scope_alignment`) appear in the report as bonus rows but do not affect the main behavioral score.
