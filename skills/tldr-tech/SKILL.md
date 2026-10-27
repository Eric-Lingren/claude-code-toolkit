---
name: tldr-tech
description: Summarize a technical document (code docs, API references, changelogs, config option lists, etc.) into four structured views: Engineer Concise, Engineer Full, Product Concise, Product Full. Use when user wants a structured breakdown of a technical document, config reference, or API spec.
model: sonnet
effort: high
invokedBy: human
---

Summarize the following technical content across four outputs. Use simple markdown throughout — bullets, bold, short lines. No long prose blocks. Everything must be skimmable at a glance.

**Input handling:**
- If the argument looks like a file path, read the file first.
- If the argument looks like a URL, fetch it first.
- Otherwise treat the argument as the raw content to summarize.

---

## ENGINEER - Concise

- Dense bullets only.
- **Bold** key terms: file names, functions, endpoints, config keys, flags, versions.
- What it is and what it does — nothing else.
- No narrative, no explanation of the obvious.

---

## ENGINEER - Full

- Bullets and **bold** throughout.
- Preserve everything: A vs B tradeoffs, edge cases, caveats, open questions.
- Use sub-bullets for detail, not paragraphs.
- Flag anything that affects implementation decisions.
- Scale depth to complexity — some things need real explanation, keep it in bullets.

---

## PRODUCT - Concise

- 2-4 bullets max.
- **Bold** the business or product impact.
- What changed or what this enables, and why it matters.
- Zero jargon, zero technical internals unless they affect scope or timeline.

---

## PRODUCT - Full

- Bullets and **bold** throughout.
- Cover: what it does, what it changes, what decisions it affects, risks and tradeoffs.
- Flag open questions or blockers a PM needs to know about.
- Skip implementation detail unless it creates a constraint.
- Plain language — not dumbed down, just jargon-free.

---

**Rules across all four:**

- No filler, no "this document outlines...", no throat-clearing.
- No long text blocks — if it is more than 2 lines, break it into bullets.
- Tradeoffs and caveats are never optional — always include them.
- Scale length to content complexity, not a fixed count.
- Flag ambiguities or open questions where relevant.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `tldr-tech`.
<!-- skill-done: tldr-tech -->
<!-- learning-capture:end -->
