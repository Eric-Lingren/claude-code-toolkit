---
name: improve-component
description: Analyze React components or TypeScript/HTML files for reusability, modularity, and hygiene improvements. Separates business logic from presentation, enforces design system usage, and proposes focused refactors. Use when user wants to improve a specific component, clean up a file, or make code more modular.
model: sonnet
effort: high
invokedBy: human
---

<!-- tier-delegate: managed by sync-model-tiers.py -->
## Delegate menial lookups to Haiku (cost control)

During this skill, push pure read-only lookups DOWN to a cheap subagent instead
of running them on the current model. This covers: multi-file grep/glob,
"where is X defined / what calls Y", mapping a directory, reading many files to
locate something, or fetching a URL for reference.

Use the Agent tool with the `caveman:cavecrew-investigator` subagent (Haiku,
returns a compressed file:line answer). If that subagent is unavailable, spawn a
general agent with `model: haiku`. Keep all reasoning, decisions, and edits on
the current model. Delegate only the menial searching.
<!-- /tier-delegate -->

# Improve Component

Analyze specific files or components for modularity, reusability, and hygiene. Propose focused refactors that make components easier to test, reuse, and maintain.

## Contract

**Format:** task file — see `contracts/task-contract.md` (schema_version: `"1"`)
**Role:** producer

**Step-0 — validate output before returning:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/task-schema.json \
  <output-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not write the file.

This skill operates on individual files or small clusters of related files.

## Principles

Apply these in order of impact. Not every principle applies to every file. Flag only what matters.

### 1. Business Logic Separation

Business logic and presentation must live in separate layers. A component that fetches data, transforms it, manages state, AND renders UI is doing too much.

**Pattern:** Extract business logic into custom hooks or plain functions. The component becomes a thin rendering layer that receives data and callbacks.

**Test:** Can you test the business logic without rendering anything? Can you test the presentation with stub data and no real API calls? If no to either, the layers are coupled.

**Signals of violation:**
- `useEffect` with fetch/mutation logic inside a component that also renders UI
- State transformations (filtering, sorting, aggregation) mixed with JSX
- Event handlers that contain business rules, not just delegation
- Components that import API clients or service modules directly

### 2. Design System Adherence

Presentation layers should use design system components and tokens wherever possible. Every one-off styled element is a drift risk.

**Before creating any UI element, check:**
- Does a design system component already handle this? (`/src/design-system`, shadcn, or whatever the project uses)
- Does a shared UI component already exist? (`/src/shared-ui` or equivalent)
- Are spacing values using theme tokens, not raw px/rem?
- Are colors, typography, and shadows using theme values?

**Signals of violation:**
- Inline styles with hardcoded values
- Styled-components or CSS that duplicate design system primitives
- Custom button/input/modal/card when a DS version exists
- Raw HTML elements (`<div>`, `<span>`) used for layout instead of DS layout primitives
- Magic numbers for spacing, font sizes, or breakpoints

### 3. Single Responsibility

Each component does one thing. If describing it requires "and", it should split.

**Test:** Describe the component in one sentence. If you need a conjunction, identify the two responsibilities and evaluate whether splitting improves clarity.

**Signals of violation:**
- Component name includes "And" or describes two behaviors
- Multiple unrelated `useEffect` hooks
- Render function has distinct visual sections with independent data needs

### 4. Props Interface Minimality

Fewer props = more reusable. Pass only what the component needs.

**Signals of violation:**
- Receiving a large object but only using 2-3 fields
- Boolean props that toggle between two entirely different render paths (should be two components)
- More than ~6 props (consider grouping or splitting)
- Props passed only to forward to a child (prop drilling)

### 5. Composition Over Configuration

Components with many boolean/enum props that toggle behavior should split into composable pieces.

**Prefer:** `<Card><CardHeader /><CardBody /></Card>`
**Over:** `<Card showHeader showFooter variant="sidebar" />`

**Signals of violation:**
- Multiple boolean props that control which sections render
- `variant` or `type` prop with a switch statement in the render
- Conditional rendering blocks that are each 20+ lines

### 6. Custom Hook Extraction

When a component has complex state logic or effects, extract to a named hook.

**Benefits:** Hook is independently testable. Component becomes declarative. Logic is reusable across components.

**When to extract:**
- 3+ `useState` calls that interact with each other
- `useEffect` + `useState` patterns (loading/error/data)
- State machine logic (multi-step forms, wizards)
- Debounce/throttle/polling patterns

**When NOT to extract:** Single `useState` for UI toggle. Simple form with 2 fields. Extraction adds a file but no clarity.

### 7. Naming Clarity

Names describe what, not how. Names describe what the thing IS, not where it's used.

**Components:** Describe what they render. `InvoiceLineItem` not `TableRow3`. `UserAvatar` not `SmallImage`.
**Hooks:** Describe what state they manage. `useInvoiceForm` not `useFormStuff`.
**Variables:** `isLoading` not `flag`. `selectedUserId` not `id`. `handleSubmit` not `doIt`.
**Files:** Match the primary export. `InvoiceLineItem.tsx` not `row.tsx`.

### 8. Colocation

Things used by one component live next to it. Things shared across components live at the nearest common ancestor.

**Signals of violation:**
- Types defined in a central `types/` folder but used by one component
- Helper functions in `utils/` used by one file
- Constants in a global file but relevant to one feature

### 9. Test Coverage Existence

Check whether a colocated test file exists for the target component.

**Check for:** `ComponentName.test.tsx`, `ComponentName.spec.tsx`, or a `__tests__/` directory containing a matching test file.

**If no test file exists:** Flag it as a structural finding. A component without tests is a refactoring hazard.

**If a test file exists:** Glance at it. Flag if it only tests trivial rendering ("renders without crashing") and lacks coverage of meaningful behavior, props, or edge cases.

**Scope:** This principle surfaces the gap. Tests are written in the execution step (step 7).

### 10. File Size as a Signal

Not a hard limit, but files over ~200 lines usually contain extractable pieces. The question is always: does splitting improve clarity, or just scatter code?

**Split when:** Distinct responsibilities exist within the file. Independent testability would improve. A section is reusable elsewhere.
**Don't split when:** The pieces only make sense together. Splitting just creates a file-hopping burden. The file is long but linear and readable.

### 11. TypeScript Hygiene

Type safety is structural, not cosmetic. Loose types mask bugs and break refactor confidence.

**Signals of violation:**
- `any` casts that are not explicitly justified
- Missing generics where type parameters would narrow behavior
- Overly broad union types (`string | object | undefined`) where a discriminated union would work
- Props typed as `object` or `Record<string, any>` instead of a named interface
- Return types omitted on exported functions and hooks
- Type assertions (`as Foo`) instead of narrowing

### 12. Accessibility

React components must be usable by keyboard and screen reader without extra work.

**Check:**
- Interactive elements use semantic HTML (`<button>`, `<a>`, `<input>`) not `<div onClick>`
- Images have `alt` text (empty string `alt=""` for decorative images is correct)
- Form inputs have associated `<label>` elements or `aria-label`
- Modals and dialogs trap focus and restore it on close
- Dynamic content changes are announced via `aria-live` where appropriate
- Color is not the only means of conveying information

**Signals of violation:**
- `onClick` on a `<div>` or `<span>` with no `role` or `tabIndex`
- `<img>` without `alt`
- `<input>` without a label association
- Focus lost after a modal closes

## Process

### 1. Receive target

User provides one or more file paths. If no paths given, ask: "Which files or components should I look at?"

If the user points at a directory, list the files in it and ask which to focus on. Don't analyze an entire directory unprompted.

### 2. Read and understand

Issue all of the following in the same turn (parallel — none depend on each other):
- Read the target file(s) fully
- Read direct imports (one level deep) to understand dependencies
- Read sibling files (`ComponentName.css`, `ComponentName.types.ts`, `ComponentName.utils.ts`)
- Read the test file if one exists
- **Spawn `context-loader`** (`subagent_type: context-loader`, repo root)

Wait for all reads and the context-loader result before step 3. Use `vocabulary` terms throughout your analysis for domain concepts. From `adrs[]`, deep-read full text only for those whose `path` is relevant to this component's area — they explain intentional trade-offs that look like violations. Do not glob `docs/adr/` directly. From `sources[]`, deep-read any typed sources relevant to this component (e.g. `design-system`, `brand-colors`, `brand-typography`).

Understand what the component does before evaluating how it's structured. An ADR may explain why a principle appears violated.

### 3. Identify the design system

Use `sources[]` from the context-loader payload first (type `design-system`). If a pointer was returned, Read the file it points to for available components. If no `design-system` source was returned, locate the design system manually by checking these locations in order:
1. `/src/design-system/` or `/src/ds/`
2. shadcn component directory (often `/src/components/ui/`)
3. A component library in `package.json` (e.g. `@radix-ui`, `@chakra-ui`, `@mui`)
4. `/src/shared-ui/` or `/src/common/`

If found via either path, scan available components so you can recommend specific replacements. If not found, skip design system checks and note that no DS was detected.

### 4. Analyze against principles

Evaluate each principle. For each violation found, note:
- Which principle is violated
- The specific code location (file:line)
- Why it matters (impact on testability, reusability, or maintainability)
- What the fix looks like (concrete, not vague)

Skip principles that don't apply. Don't manufacture findings.

### 5. Present findings

Group findings by severity:

**Structural** (affects testability or reusability):
- Business logic mixed with presentation
- Missing design system usage where DS components exist
- Components doing multiple unrelated things
- TypeScript `any` casts or missing generics on public interfaces
- Accessibility violations on interactive elements

**Hygiene** (affects readability and maintainability):
- Naming issues
- Colocation violations
- File size concerns
- Prop interface issues
- Missing or trivial test coverage

Format each finding as:

```
<file>:L<line> - <principle>: <problem>. <fix>.
```

End with a prioritized recommendation: "Start with X because it unlocks Y."

**Clean bill of health path:** If analysis finds no structural or hygiene violations worth flagging, check for test coverage before ending.

1. Look for a colocated test file (`ComponentName.test.tsx`, `ComponentName.spec.tsx`, or `__tests__/ComponentName.*`).
2. If no test file exists or the file only has trivial "renders without crashing" coverage: use `AskUserQuestion` to ask: "Component looks good. No meaningful test coverage found. Want me to generate a task to backfill tests via `/build-code`?" If yes, go to step 7 with one task: backfill characterization tests. If no, end.
3. If adequate test coverage exists: note it and end.

### 6. Discuss and refine

Don't start refactoring immediately. Ask the user which findings they want to act on. Some may be intentional trade-offs. An ADR you found may already justify a finding.

Present findings as a numbered list and ask: "Which of these do you want to act on?"

### 7. Write tasks file

After the user approves changes, write a tasks file before touching any code.

**Step 7a: Branching.** **gxcheck pre-flight:** Before asking the user, run `~/.dotfiles/.scripts/gxcheck` and surface its output as a brief status block (e.g. `Branch check: OK: branch looks clean`). This is advisory only — the skill continues regardless of the output. Then run `git branch --show-current` and ask:

```
Branching strategy:
1. Single branch for all tasks (you provide the name) — best for a focused refactor or if you want to stay on your current branch
2. Per-task branches (auto-generated) — best for independent improvements reviewed separately

Which do you prefer?
```

If single: ask "Branch name?" and suggest `refactor/<component-slug>-improvements` as a default. If the user is already on a feature or fix branch, note that they can stay on it. If per-task: derive branch names per branching-strategy.md.

**Wait for user response before continuing.**

See `~/.dotfiles/claude-code-shared/resources/branching-strategy.md` for branch naming rules, derivation format, and JSON recording format.

**Step 7b: Write the file.** Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/next-task-id.sh docs/tasks/` and `~/.dotfiles/claude-code-shared/scripts/scaffolding/task-filename.sh improve-<component-slug>`.

Write one task per approved finding (or group tightly coupled findings into one task with compound acceptance criteria). Each task must be self-contained. Embed the specific file path, line numbers, principle violated, and exact fix approach in the description.

Follow the canonical schema in `~/.dotfiles/claude-code-shared/contracts/task-schema.json`. Do not define the JSON structure inline.

Key field values for improve-component tasks:
- `schema_version`: `"2"`
- `producer`: `"improve-component"`
- `source`: `{"type": "session", "ref": null}` (no upstream document for component improvement runs)

HITL tasks (rare — e.g. "rotate the API key before refactoring the credential helper") must be hands-only: a keyboard action the AI cannot perform. Never emit a decision-review HITL task.
- `branching`: use the strategy and branch from Step 7a (see branching-strategy.md for JSON format)
- `description`: `"Principle violated: <name>. Location: <file:line>. Problem: <what is wrong and why it matters>. Fix: <concrete steps>."`
- `acceptance_criteria[0]`: always a test criterion (`"Characterization test exists at <path> covering the behavior being changed before any refactor"` for refactors, or `"Failing test exists at <path>"` for new behavior)

After writing the file, output:

```
Tasks written: docs/tasks/<filename>
Tasks: <T-XXXX list>
```

### 8. Hand off to build-code

Tell the user:

```
Next steps:
```

Then run:
```bash
python3 ~/.dotfiles/claude-code-shared/scripts/shared/print-skill-next-steps.py improve-component
```

Append that output under the Next steps header. Do not hardcode skill names. Do not invoke `/tdd` directly. Do not make any code changes. All execution happens in `/dispatch-tasks`.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `improve-component`.
<!-- skill-done: improve-component -->
  - `/dispatch-tasks` — tasks file is written and ready to execute
<!-- learning-capture:end -->
