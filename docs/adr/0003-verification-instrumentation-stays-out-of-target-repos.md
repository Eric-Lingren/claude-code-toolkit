---
status: accepted
---

# Browser verification instrumentation never touches target repos

Check Specs, Auth Profiles, screenshots, and verification reports live in dotfiles (`repo-policy.json`, `~/.cache/`) or in `docs/` scaffolding excluded by `repo-policy.json`. Nothing is committed or pushed to the app repo, consistent with the existing scaffolding exclusion policy.

## Considered Options

- Promoting passing Check Specs to committed `.spec.ts` files. Rejected: pushes personal instrumentation into team repos. Committed e2e remains a deliberate product choice via `/to-e2e-tasks`.
- Per-repo `.claude/browser-auth.json`. Rejected: same reason.
- Hosting PR before/after images on an orphan branch or secret gist. Rejected: pushes to the repo, or leaks company data via unlisted URLs. Images are exported to `docs/visual-changes/<branch>/` and attached to the PR by hand.
