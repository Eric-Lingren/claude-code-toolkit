---
name: to-spec
description: Render a seed .json file into an interactive HTML PRD and save it to docs/prd/. Use when user wants to create a PRD from a seed file with rich interactive output.
model: sonnet
effort: high
invokedBy: human
---

## Contract

**Consumes:** seed file — see `contracts/seed-contract.md` (schema_version: `"2"`)
**Produces:** HTML PRD (`docs/prd/<filename>`, filename via `scripts/scaffolding/doc-filename.sh <slug> html`)

The HTML PRD embeds the seed JSON verbatim. `to-tasks` can read the HTML PRD as an alternate input via `scripts/extract-prd-json.sh`, making the chain `seed → HTML PRD → tasks` valid.

**Step-0 — validate seed input before processing:**
```bash
bash ~/.dotfiles/claude-code-shared/scripts/shared/validate-schema.sh \
  --instance ~/.dotfiles/claude-code-shared/contracts/seed-schema.json \
  <input-path>
```
On non-zero exit: STOP. Report stderr to the user. Do not process the file.

---

This skill renders a seed `.json` file into an interactive HTML PRD. It does NOT synthesize from conversation — the seed is the source of truth.

## Process

### 1. Resolve the seed path

If a seed path was passed as an argument (e.g. `/to-spec docs/seeds/20260602-1234-my-feature.json`), use it directly.

Otherwise, list `docs/seeds/*.json` and ask the user to choose one.

Route the seed path through resolve-ref.sh before reading (see `resources/resolve-ref-pattern.md`): Run `bash ~/.dotfiles/claude-code-shared/scripts/scaffolding/resolve-ref.sh $(basename <seed-path>)`. On archive hit (output starts with `ARCHIVE:`), use the extracted content in place of the file. On not-found (exit non-zero), surface the diagnostic and ask "Continue anyway?" — bypass rebuilds context from conversation.

Run `~/.dotfiles/claude-code-shared/skills/to-tasks/scripts/extract-prd-json.sh <seed-path>` to validate and read the seed JSON (use active path or write archive content to a temp file if archived). Fail if the script exits non-zero.

### 2. Ask: work or personal?

Ask before proceeding. Do not skip. Do not infer:

```
Is this PRD for work or personal?
1. Work
2. Personal
```

This determines the color theme:
- **Work**: use `assets/theme-work.css` from this skill's directory. Dark, professional, blue-anchored palette.
- **Personal**: use `assets/theme-personal.css`. Dark, warm, ochre/earth-tone palette.

Read the chosen theme file and replace the `:root` CSS custom properties block in the template with the values from the theme file. Also update `font-family` declarations on `body` and heading elements to use `var(--font-body)` and `var(--font-heading)`.

### 3. Ask where to write

**gxcheck pre-flight:** Before asking the user, run `~/.dotfiles/.scripts/gxcheck` and surface its output as a brief status block (e.g. `Branch check: OK: branch looks clean`). This is advisory only — the skill continues regardless of the output.

```
Where should this PRD go?
1. Current branch
2. New spike branch
3. New feat branch
4. New fix branch
```

If the user picks **2**, **3**, or **4**:
- Propose a branch name derived from the slug: `spike/{slug}`, `feat/{slug}`, or `fix/{slug}`.
- Let the user accept or type a custom name.
- Run `git switch -c {branch-name}`. If the switch fails (dirty working tree), tell the user to commit or stash first and stop.

If the user picks **1**, continue on the current branch.

### 4. Determine the output filename

Derive the slug from `seed.slug`. Run `~/.dotfiles/claude-code-shared/scripts/scaffolding/doc-filename.sh <slug> html` to get `YYYYMMDD-HHMM-{slug}.html`.

Resolve the absolute path of `docs/prd/` relative to the current working directory. Create it if it doesn't exist.

### 5. Write the HTML PRD

Use the template from `~/.dotfiles/claude-code-shared/skills/to-spec/resources/template.html` for the HTML shell.

**Section rendering rules:**
- Render a section ONLY if the corresponding seed field is present and non-empty.
- Do NOT render empty section blocks. A partial seed (core spine only) must produce zero empty feature-section blocks.
- Always render: title, summary, decisions, open_threads, next_action.
- Conditionally render (only if field exists and is non-empty): problem_statement, solution, evidence, success_metrics, user_stories, implementation_decisions, testing_decisions, out_of_scope, risks_and_tradeoffs, further_notes.

**Embedded JSON:**
Every HTML PRD MUST contain two metadata script blocks at the bottom of `<body>`:

1. `<script type="application/json" id="prd-data">` — the full seed JSON verbatim (copied from the seed file without modification). This ensures the output round-trips through `extract-prd-json.sh` unchanged.

2. `<script type="application/json" id="prd-provenance">` — provenance for this PRD document itself:
   ```json
   {
     "producer": "to-spec",
     "source": {"type": "seed", "ref": "<absolute-or-relative-path-to-seed-file>"}
   }
   ```
   Use the seed file path as passed to this skill (the same path from step 1). This enables reverse-tracing: a task file stamped with `source: {type: "prd", ref: <html-path>}` can follow the prd-provenance block to reach the originating seed.

**Never auto-commit.**

### 6. Confirm and suggest next step

Tell the user the output path. Suggest running `/to-tasks <seed-path>` next if they haven't already.

## HTML Output Rules

### Template usage

The template file (`resources/template.html`) provides the outer shell: CSS, JavaScript for interactivity, and layout skeleton. Fill in the content sections. Do NOT modify the shell CSS/JS unless content requires it.

### Mandatory interactive features

Every HTML PRD MUST include:

1. **Collapsible sections.** Each major section is collapsible via `<details>/<summary>`. All expanded by default.
2. **Tabbed views.** User Stories, Implementation Decisions, and Testing Decisions (when present) are presented as tabs.
3. **Color-coded tags.** Risk/complexity indicators on implementation decisions (red = high risk, amber = medium, green = low). Priority indicators on user stories if priorities differ. Module names as colored pills.
4. **Jump links / TOC.** Fixed or sticky table of contents with anchor links to every rendered section.
5. **Review checklist.** Each section has a checkbox. Progress bar shows "N/M sections reviewed." Visual only, resets on refresh.

### Conditional features

Include ONLY when the PRD content warrants them:

6. **Inline SVG architecture diagram.** Generate when 3+ modules with non-trivial relationships exist.
7. **Side-by-side option comparison.** Generate when the PRD presents alternatives or trade-offs.

### Design constraints

- Single self-contained `.html` file. No external dependencies.
- Dark mode. Colors from chosen theme file's `:root` variables. Do not hardcode hex values.
- Minimal aesthetic. No gradients, no glass morphism, no emoji headers, no decorative elements.
- Serif or clean sans-serif body text. 60-75ch line width. Generous spacing.
- Mobile responsive. Include viewport meta tag.
- Functional color only: risk tags, status indicators, module pills.

### Content rules

- Tone: clear, direct confidence. No passive hedging, no filler.
- User stories: format: As a `<actor>`, I want `<feature>`, so that `<benefit>`
- Implementation decisions: modules, interfaces, architecture, schema changes, API contracts. No file paths or code snippets.
- `evidence`: one or two sentences max. A concrete data point or user signal.
- `success_metrics`: specific and measurable.
- `risks_and_tradeoffs`: what breaks or regresses if this goes wrong.

<!-- learning-capture:start -->
Read and execute `~/.dotfiles/claude-code-shared/resources/learning-capture.md`.
This skill's slug is `to-spec`.
<!-- skill-done: to-spec -->
  - `/to-tasks` — PRD is approved and ready to break into tasks
<!-- learning-capture:end -->
