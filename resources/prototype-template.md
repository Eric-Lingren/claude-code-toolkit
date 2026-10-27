---
name: prototype-template
description: Standard template for prototype findings docs written to docs/prototypes/. Used by the /prototype skill as the single source of truth for artifact structure.
---

# Prototype: <title>

## Question

What specific hypothesis or design question was this prototype answering?

## Verdict

The agreed-upon solution. This section is detailed and prominent. Include:

- The final design decision
- Key code snippets that capture the approach precisely
- UI/UX specifics (layout, interaction patterns, state shape) that anchor downstream work
- Any constraints that must carry forward into the PRD and TDD phases

The goal is to prevent drift. The next skill should not have to re-derive these decisions.

## Explorations

What was tried during the session. Brief entries. Focus on what was learned, not exhaustive detail.

## Rejected Paths

What was ruled out and why. Keep entries short. One line per rejection where possible.

## PRD Handoff Notes

Open questions, unresolved constraints, and things the PRD author needs to decide or be aware of.
