# Skill Directory Conventions

Standard directory structure for skills in `~/.dotfiles/claude-code-shared/skills/`.

## Core rule

Every non-SKILL.md file goes in a canonical subdirectory based on its type. Skills with only `SKILL.md` need no subdirectories. Don't create empty dirs.

## Canonical directories

| Directory | Purpose | File types |
|-----------|---------|------------|
| `scripts/` | Executable helpers, automation | `.sh`, `.py`, `.js`, `.ts` |
| `resources/` | Reference docs, templates, format guides, prompt templates, schemas | `.md` (non-SKILL.md), `.html` (templates), `.json` (schemas/configs) |
| `assets/` | Static files the skill embeds or copies but doesn't interpret | `.svg`, `.png`, `.jpg`, `.gif`, `.css`, `.woff`, `.ttf` |

### Optional directory

| Directory | Purpose | When to use |
|-----------|---------|-------------|
| `runs/` | Execution artifacts, scores, logs, learnings | Only for skills that maintain persistent execution state across runs |

## File placement rules

- `SKILL.md` always lives at the skill root. Never in a subdirectory.
- Shell scripts (`.sh`) go in `scripts/`, even if there's only one.
- Markdown reference files, templates, format guides, and prompt templates go in `resources/`.
- Images, SVGs, CSS, and fonts go in `assets/`.
- Runtime artifacts (generated during execution, not authored) go in `runs/`.

## Example structures

### Minimal skill (no subdirs needed)
```
grill-me/
└── SKILL.md
```

### Skill with helpers
```
to-prd-md/
├── SKILL.md
├── scripts/
│   └── next-prefix.sh
└── resources/
    └── template.md
```

### Skill with assets
```
to-spec/
├── SKILL.md
├── scripts/
│   └── next-prefix.sh
├── resources/
│   └── template.html
└── assets/
    ├── logo-work.svg
    ├── theme-personal.css
    └── theme-work.css
```

### Stateful skill
```
improve-skill-benchmarks/
├── SKILL.md
├── scripts/
│   └── scaffold-evals.sh
├── resources/
│   ├── builtin-assertions.md
│   ├── judge-prompt.md
│   ├── consistency-auditor-prompt.md
│   └── scores-json-schema.md
└── runs/
    └── <skill-name>/
        ├── eval.json
        ├── scores.json
        └── learnings.md
```

## Cross-references

When referencing files from SKILL.md, always use the full subdirectory path:
- `resources/template.md`, not `template.md`
- `scripts/next-prefix.sh`, not `next-prefix.sh`
- `assets/logo-work.svg`, not `logo-work.svg`

When referencing files across skills, use absolute paths:
- `~/.dotfiles/claude-code-shared/skills/grill-with-docs/resources/ADR-FORMAT.md`

## Enforcement

The `improve-skill-benchmarks` evaluator checks directory hygiene as part of its structural analysis (Step 5). It flags misplaced files and suggests moves with a single confirmation prompt. All references are updated automatically when files move.
