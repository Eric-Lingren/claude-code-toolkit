# investigation-result Contract

**Schema:** `investigation-result-schema.json` (JSON Schema draft-07, `schema_version: "1"`)

All investigator agents — the orchestrator and every leaf agent — emit this single shape. No agent may return a different structure. Consumers must validate against the schema before acting on the result.

---

## Verdict axis

| Verdict | Meaning | evidence[] requirement |
|---|---|---|
| `VERIFIED_TRUE` | The claim is supported by cited evidence | **Non-empty** (schema violation if empty) |
| `VERIFIED_FALSE` | The claim is contradicted by cited evidence | **Non-empty** (schema violation if empty) |
| `INSUFFICIENT_EVIDENCE` | Sources were checked; none confirmed or denied the claim | May be empty |
| `CONTESTED` | Genuine expert disagreement; no single authoritative answer | May be empty |

Leaf agents pick from this axis per sub-claim. The orchestrator aggregates leaf results and applies the propagation rule before emitting its own verdict for the parent claim.

---

## Citation mapping by source type

Every evidence item carries `source`, `ref`, and `quote`. The `ref` format is fixed per source:

| `source` | `ref` format | Example |
|---|---|---|
| `web` | Full URL | `https://docs.example.com/feature-flags` |
| `code` | GitHub blob URL with line anchor | `https://github.com/org/repo/blob/main/src/auth/middleware.ts#L88` |
| `linear` | Linear ticket URL | `https://linear.app/acme/issue/ENG-512` |
| `github` | GitHub issue or PR URL | `https://github.com/org/repo/pull/204` |
| `notion` | Notion page URL or block id | `https://notion.so/workspace/Design-Brief-abc123` |

`quote` is always the verbatim or near-verbatim span from the source that supports the verdict. It must be substantive enough to be independently verifiable — paraphrases are not acceptable.

---

## INSUFFICIENT_EVIDENCE propagation rule

If **any load-bearing sub-claim** in the orchestrator's decomposition returns `INSUFFICIENT_EVIDENCE`, the **aggregate verdict inherits that uncertainty**. The orchestrator:

1. Sets its own `verdict` to `INSUFFICIENT_EVIDENCE`.
2. Writes a `summary` that **explicitly states which sub-claim could not be confirmed** and why.
3. Does **not** average, smooth over, or downgrade the uncertainty to produce false confidence.

Example orchestrator summary when a sub-claim is unresolved:

> "Sub-claim 2 (whether the change was deployed before the incident) returned INSUFFICIENT_EVIDENCE — deployment logs were not available in any indexed source. The overall verdict cannot be VERIFIED_TRUE or VERIFIED_FALSE without this information."

The propagation rule is contract-enforced, not model-judged. An orchestrator that emits `VERIFIED_TRUE` when a load-bearing leaf returned `INSUFFICIENT_EVIDENCE` is violating this contract.

---

## Optional fields

- `summary` — human-readable explanation of the verdict. **Required** from the orchestrator; optional for leaf agents. Must not introduce claims not grounded in `evidence[]`.
- `sub_claim` — the specific sub-claim this result addresses, as decomposed by the orchestrator. Populated by leaf agents; omitted by the orchestrator (which addresses the parent claim).
- `adjacent_facts` — orchestrator-only. Bounded expansion: a relevance-gated array of adjacent material facts the asker likely needs, each carrying a `claim`, `verdict`, `evidence[]`, and optional `summary`. Facts with no supporting evidence are excluded (not included with empty evidence). Capped at implementation judgment (typically 2–4). Each code `evidence[].ref` uses a GitHub blob URL with `#L<line>` anchor, constructed by the same method as investigator-code.
- `sub_results`: orchestrator-only. One entry per decomposed sub-claim, each carrying `sub_claim`, `verdict`, `evidence[]`, and optional `summary`, copied from that leaf's result. `VERIFIED_TRUE` and `VERIFIED_FALSE` entries need non-empty `evidence[]`. The field only adds per-sub-claim visibility. The aggregate `verdict` and the INSUFFICIENT_EVIDENCE propagation rule are unchanged. It is additive within `schema_version` `"1"`. Top-level `sub_claim` stays leaf-only. The orchestrator carries per-sub-claim text in `sub_results[].sub_claim` instead.

## Bounded expansion (orchestrator only)

After resolving the literal claim, the orchestrator identifies adjacent material facts the asker likely needs — related but not directly asked questions that would change how the asker acts on the answer. Examples: if the claim is about a function, adjacent facts might include callers, tests, or recent changes.

Rules:
1. **Relevance-gate:** only facts materially related to the original question qualify.
2. **Evidence gate:** a fact is included in `adjacent_facts` only if the sub-investigation returns `VERIFIED_TRUE` or `VERIFIED_FALSE` with at least one evidence item. Facts returning `INSUFFICIENT_EVIDENCE` are silently dropped.
3. **Cap:** implementation judgment, typically 2–4 adjacent facts. Do not pad.
4. **Citation format:** code refs use GitHub blob URLs with `#L<line>` anchor (same construction as investigator-code leaf agent).

---

## Schema versioning

`schema_version` uses hard-cutover semantics. A bump from `"1"` to `"2"` is a breaking change. Consumers that receive an unknown `schema_version` must reject the document rather than attempt to parse it under the old schema. The orchestrator and all leaf agents must be updated atomically when the version bumps.

---

## Emitters

- **Orchestrator:** `investigator` agent (aggregates leaf results, emits the parent-claim verdict)
- **Leaf agents:** `investigator-web`, `investigator-code`, `investigator-linear`, `investigator-github`, `investigator-notion` (each emits one result per sub-claim)

No other agent type emits this contract. Doorway skills (e.g., `investigate`) pass the orchestrator result through without modification.
