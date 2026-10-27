---
status: accepted
---

# Browser auth reuses each app's own login setup first

`browser-auth.py` obtains storage state using the strategy declared in the repo's Auth Profile, preferring the project's own auth setup (Playwright setup projects, test-login bypass endpoints) over generic API or form login. Shared tooling stores no credentials; creds stay in the project's `.env.local` or Keychain. A freshness probe runs before each check, and unrecoverable auth yields `skipped: auth_expired`, never a failure.

## Consequences

Scripted form login and human-seeded SSO sessions are designed but deferred until an app needs them. Both current apps (a work app's test bypass endpoints, SpawnedSapien Supabase setup files) bypass SSO already.
