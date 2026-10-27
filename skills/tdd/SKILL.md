---
name: tdd
description: Test-driven development with red-green-refactor loop. Use when user wants to build features or fix bugs using TDD, mentions "red-green-refactor", wants integration tests, asks for test-first development, or wants to backfill tests for existing code.
model: sonnet
effort: high
invokedBy: human
---

# Test-Driven Development

## Entry check: task file detection

Before doing anything else, check how you were invoked.

If the user's input contains any of the following, pause and ask before proceeding:
- A path matching `docs/tasks/*.json`
- A task ID pattern like `T-XXXX`
- Content that looks like a pasted task JSON block (`"acceptance_criteria"`, `"blocked_by"`, `"status"`)

Ask:

> It looks like you're passing a task file directly to `/tdd`. Were you meaning to run `/build-code` instead?
>
> `/build-code` is the correct entry point for task files. It handles branching, status tracking, follow-up discovery, and PR creation. It also seeds `/tdd` with additional test requirements that do not apply when `/tdd` is called directly.
>
> - **Yes, use `/build-code`** — stop here. Tell the user to run `/build-code docs/tasks/<filename>`.
> - **No, I want raw `/tdd`** — continue, but warn: "Note: test requirements from build-code are not active. Tests are still mandatory."

If the input is a free-form feature description, bug report, or characterization request with no task file reference, skip this check and proceed normally.

## Philosophy

**Core principle**: Tests should verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the interface). The warning sign: your test breaks when you refactor, but behavior hasn't changed. If you rename an internal function and tests fail, those tests were testing implementation, not behavior.

See [mocking.md](resources/mocking.md) for mocking guidelines.

## Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" - treating RED as "write all tests" and GREEN as "write all code."

This produces **crap tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes - they pass when behavior breaks, fail when behavior is fine
- You outrun your headlights, committing to test structure before understanding the implementation

**Correct approach**: Vertical slices via tracer bullets. One test → one implementation → repeat. Each test responds to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matters and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Refactoring: Tests Come First

When the task involves refactoring, moving, or restructuring existing code, **check for existing test coverage before changing anything.**

If tests already cover the behavior being refactored, proceed. The existing tests are your safety net.

If no tests exist for the code being refactored:

1. **Write characterization tests first.** These capture current behavior as-is. Treat the existing code as a black box. Test through its public interface. One RED-GREEN cycle per behavior, same as new code.
2. **Get all characterization tests passing (GREEN).**
3. **Then refactor.** Every change should keep tests GREEN. If a test breaks, the refactor changed behavior, not just structure.

This is non-negotiable. Refactoring without tests is not refactoring. It's editing and hoping. The whole point of using TDD for refactoring is to prove behavior is preserved.

Characterization tests should:
- Exercise the component through its public API or rendered output
- Cover the critical paths the component handles today
- Be written as behavioral specs ("renders user name", "calls onSubmit with form data")
- Not test implementation details that will change during the refactor

## Characterization Mode: Backfilling Tests

Use this mode when adding tests to existing code with no planned changes. No new features, no refactoring. Just capturing current behavior in tests.

**When to use:** User says "add tests for X", "backfill tests", "cover this component", or points at existing code that has no tests and no planned changes.

### Characterization workflow

1. **Explore the target code.** Read the component, module, or function. Understand its public interface, props, arguments, return values, side effects, and rendered output.

2. **Identify behaviors to cover.** List what the code does today through its public interface. Group by:
   - Critical paths (happy path, main use cases)
   - Edge cases (empty state, error state, boundary values)
   - Integration points (callbacks, events, API calls)

3. **Confirm with user.** Present the behavior list. Ask which are highest priority. Skip the "what interface changes" question since there are none.

4. **Write tests one at a time.** Same vertical slice loop:
   - Write one test describing one behavior
   - Run it. It should pass immediately (the code already works)
   - If it fails, the test is wrong, not the code. Fix the test.
   - Move to the next behavior.

5. **No refactoring step.** The goal is coverage, not cleanup. If you spot refactor opportunities, note them but don't act. The user can refactor later with these tests as a safety net.

### Key differences from standard TDD

- Tests should pass on first run. You're documenting existing behavior, not driving new behavior.
- A failing test means your understanding of the code is wrong. Read the code again.
- No implementation step. The code exists. You're only writing tests.
- No refactor step. Leave the code untouched.

## Workflow

### 0. "No testable seam" claims

If the task's `acceptance_criteria[0]` says `"Visual regression verified manually — no automated test seam exists..."`, treat this as a claim to verify, not a directive to skip tests.

Before accepting it:

1. Read the files named in the task description.
2. Identify the conditional, flag, or prop that controls the behavior.
3. Try to write a test: RTL render with the relevant context/props, assert DOM output.

The only valid reason to skip writing a test is if the failure mode is a **CSS property difference** (e.g. `blur(3px)` vs `background: gray`) that the DOM cannot reflect. Conditional renders, query `enabled` flags, and prop threading are always testable.

If you find a seam, write the test regardless of what the acceptance criteria says. If you genuinely cannot find one, state explicitly why before proceeding without tests.

**A task is never done without at least one automated test. Manual verification does not satisfy the TDD loop.**

### 1. Planning

**AFK mode detection:** If the seed prompt contains `Type: AFK`, you are running inside build-runner with no human present. In AFK mode:
- Skip all user confirmation steps below. Do not ask questions or wait for approval.
- Derive the plan from `acceptance_criteria` in the seed. Treat each criterion as a behavior to test.
- Check existing test coverage, then proceed directly to the tracer bullet (step 2).

When exploring the codebase, use the project's domain glossary so that test names and interface vocabulary match the project's language, and respect ADRs in the area you're touching.

Before writing any code:

- [ ] **Check existing test coverage** for the code being changed. If coverage is missing, plan characterization tests first.
- [ ] Confirm with user what interface changes are needed (skip if characterization mode or AFK mode)
- [ ] Confirm with user which behaviors to test (prioritize) (skip if AFK mode)
- [ ] Identify opportunities for [deep modules](resources/deep-modules.md) (small interface, deep implementation)
- [ ] Design interfaces for [testability](resources/interface-design.md)
- [ ] List the behaviors to test (not implementation steps)
- [ ] Get user approval on the plan (skip if AFK mode)

Ask: "What should the public interface look like? Which behaviors are most important to test?" (For characterization mode, skip the interface question. Ask only: "Which behaviors are most important to cover?" For AFK mode, skip all questions.)

**You can't test everything.** Confirm with the user exactly which behaviors matter most. Focus testing effort on critical paths and complex logic, not every possible edge case.

### ENFORCEMENT: Pre-implementation gate

Before writing or editing any implementation file, answer this question:

> Does a currently-failing test exist for this specific behavior?

- **Yes (RED exists):** proceed.
- **No:** write the test first. Do not write any implementation. Run the test and confirm it fails before continuing.

This gate fires every time, for every file, for every behavior. There are no exceptions.

If you find yourself writing implementation code without a RED test, stop immediately. Delete the implementation. Write the test first.

**Confirm RED fails for the expected reason.** When the target is a new file (e.g. an agent or config file), structural tests (file exists, frontmatter) fail in RED. Schema or contract tests that run against fixtures may pass early. That split is expected. A structural test that passes before the file exists is a broken test.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

This is your tracer bullet - proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test → fails
GREEN: Minimal code to pass → passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior
- Add new imports in the same Edit that adds their first use. This applies to test files and implementation files. The lint hook runs noUnusedImports on every edited file and blocks imports that nothing uses yet.

### 4. Refactor

After all tests pass, look for [refactor candidates](resources/refactoring.md):

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Code is minimal for this test
[ ] If a TS interface/type/context shape changed: run type-check (yarn type-check or tsc --noEmit), exit 0. Test runners like vitest strip types, so GREEN is not type-safe.
[ ] If code under test debounces via setTimeout (e.g. inside useEffect): vi.useFakeTimers() before render, fire the event, vi.advanceTimersByTime() past the delay, then assert. Restore with vi.useRealTimers().
[ ] If code makes values unique by appending a suffix: test an input that already contains a generated suffix (e.g. [base, "base 2", base]). Assert the output has no duplicates.
[ ] No speculative features added
```

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `tdd`.
<!-- skill-done: tdd -->
<!-- learning-capture:end -->
