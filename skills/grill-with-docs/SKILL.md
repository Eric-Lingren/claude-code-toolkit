---
name: grill-with-docs
description: Grilling session that challenges your plan against the existing domain model, sharpens terminology, and updates documentation (CONTEXT.md, ADRs) inline as decisions crystallise. Use when user wants to stress-test a plan against their project's language and documented decisions.
argument-hint: "[optional: path to a seed or handoff doc to resume from]"
model: opus
effort: xhigh
invokedBy: human
---

## Mode detection

**Trigger:** argument-presence detection. If ARGUMENTS contains a path, this is a resume. Do not inspect the schema — just check whether a path was passed.

### Fresh grill (no argument)

Read decisions from the live conversation only. Do not extract decisions from any seed or handoff file that happens to exist on disk. The live context is authoritative.

### Resume grill (argument present)

Route the path through resolve-ref.sh before reading (see `resources/resolve-ref-pattern.md`):
- Run `bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh $(basename <path>)`. On archive hit (output starts with `ARCHIVE:`), use the extracted content. On not-found (exit non-zero), surface the diagnostic and ask "Continue anyway?" — bypass rebuilds context from conversation.

Then read the file and check for `open_threads`:

**(a) open_threads is non-empty:** enter resume mode.
- Load `decisions` (from the seed or handoff's embedded base seed) as settled givens. Challenge these only if the live conversation or codebase evidence directly contradicts them.
- Load `disposed_threads` as the off-limits lock list. Every id in this list is blocked from re-entry — under the original name or any relabeled form. Relabel-resurrection (same semantic content, different wording) is blocked.
- Treat `open_threads` as the live agenda. Drill each one in sequence using the domain model and codebase as grounding material, same as a fresh grill question.
- Apply the standard one-question-per-turn protocol.

**(b) open_threads is empty (status: ready):** stop immediately.
- Tell the user: "This seed is already solidified — all threads are resolved. No grilling needed."
- Show next steps by running: `python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py grill-with-docs` and printing the output.
- Do not start a grill session.

**(c) path is unreadable or missing:** say so plainly and ask what the user wants.
- Do not guess or fall back to a fresh grill. State the exact error and stop until the user responds.

---

<what-to-do>

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing. Asking multiple questions at once is bewildering.

Before formulating your question, if the answer could be in the codebase, spawn a read-only Haiku subagent for file exploration rather than reading files inline. If a *fact* can be answered by reading code or project files, delegate that lookup to a Haiku subagent to look it up instead of asking the user. The *decisions* however are the users - put each one to them and wait for their answer.

</what-to-do>

<supporting-info>

## Domain awareness

During codebase exploration, also look for existing documentation:

### File structure

Most repos have a single context:

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
│       ├── 0001-event-sourced-orders.md
│       └── 0002-postgres-for-write-model.md
└── src/
```

If a `CONTEXT-MAP.md` exists at the root, the repo has multiple contexts. The map points to where each one lives:

```
/
├── CONTEXT-MAP.md
├── docs/
│   └── adr/                          ← system-wide decisions
├── src/
│   ├── ordering/
│   │   ├── CONTEXT.md
│   │   └── docs/adr/                 ← context-specific decisions
│   └── billing/
│       ├── CONTEXT.md
│       └── docs/adr/
```

Create files lazily — only when you have something to write. If no `CONTEXT.md` exists, create one when the first term is resolved. If no `docs/adr/` exists, create it when the first ADR is needed.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `CONTEXT.md`, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y — which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account' — do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Establish source before naming

When a question involves storage paths or naming schemes, first ask where the artifacts come from (who or what produces them, and how). Only then present naming options. The source workflow decides which uniqueness, readability, and traceability constraints apply.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible — which is right?"

### Update CONTEXT.md inline

When a term is resolved, update `CONTEXT.md` right there. Don't batch these up — capture them as they happen. Use the format in [CONTEXT-FORMAT.md](./resources/CONTEXT-FORMAT.md).

Don't couple `CONTEXT.md` to implementation details. Only include terms that are meaningful to domain experts.

### Offer ADRs sparingly

Only offer to create an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If any of the three is missing, skip the ADR. Use the format in [ADR-FORMAT.md](./resources/ADR-FORMAT.md).

</supporting-info>

<session-end-protocol>

## Session end

When all major branches are resolved and terminology is stable, run the reconciliation pass BEFORE signalling end.

### ADR reconciliation pass

While the conversation is still live and the final decision set is known, reconcile inline ADRs against the full set of decisions reached:

**Step 1: Collect ADRs written this session.** Read `docs/adr/` for any ADR created or updated during this conversation. Build a list: `{id, title, decision_recorded}`.

**Step 2: Diff against the final decision set.** For each decision reached in this session, check whether an ADR for it was:
- Written inline during the session → OK, compare the ADR's decision against the final form.
- Written inline but later contradicted by a revision → **stale: must supersede.**
- Architecturally significant (hard to reverse + surprising without context + genuine trade-off) but never offered → **missing: must capture now.**

Apply the same three-criteria filter before writing any new ADR. Do not capture every decision — only those that are hard to reverse, would surprise a future reader without context, and were the result of a real trade-off. Do not extract decisions from seeds or handoffs; only from the live conversation.

**Step 3: Supersede stale ADRs.** For each ADR whose decision was overridden by a later revision: create a new ADR that documents the current decision and add a `Supersedes: ADR-XXXX` line. Update the old ADR with a `Superseded by: ADR-YYYY` note. Use the format in [ADR-FORMAT.md](./resources/ADR-FORMAT.md).

**Step 4: Write missing ADRs.** For each architecture-grade decision that was reached during the session but never offered as an ADR: offer it now and write it if the user confirms. Use the format in [ADR-FORMAT.md](./resources/ADR-FORMAT.md).

After the reconciliation pass, signal the end explicitly:

> "Grill session complete. All branches resolved. ADR reconciliation done: [N superseded, M new]."

Then ask the user ONE question using `AskUserQuestion`:

> "What would you like to do with this?"

Options:
- **Create a seed** - capture decisions and context as a JSON IR (runs `/to-seed`). From there you can render a PRD, generate tasks, or prototype.
- **Nothing yet** - session was just for thinking it through

**CRITICAL RULES:**
- Do NOT implement any code, create any files (other than CONTEXT.md and ADR updates during the session), or make any changes after the grill session ends without explicit user authorization from this question.
- Answering "yes" or "yep" to a specific scoped question during the session ("do I have permission to delete X?") does NOT authorize broader implementation. Specific questions have specific scope.
- Wait for the user's answer before doing anything.

</session-end-protocol>

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `grill-with-docs`.
<!-- skill-done: grill-with-docs -->
  - `/to-seed` — decisions are crisp and ready to capture
<!-- learning-capture:end -->
