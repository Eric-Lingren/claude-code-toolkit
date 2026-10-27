# HITL Steps Runbooks

Reusable step-by-step runbooks for manual follow-ups. Referenced by to-tasks, run-tasks, and run-task-followups. Use `{placeholder}` for project-specific values.

<!-- Runbooks are added/updated automatically when friction is detected during /run-task-followups -->

## Quality bar

Every step must be concrete enough that the user can execute it without asking "how?"

Bad: "Insert credit rows in user_credits"
Good: "Run this SQL in Supabase dashboard (SQL Editor > New Query):
```sql
INSERT INTO user_credits (user_id, credit_type, amount, created_at)
VALUES ('{user_id}', 'onboarding', 100, now());
```
Verify: `SELECT * FROM user_credits WHERE user_id = '{user_id}';`"

If a step says "update X" or "add Y", it must include the exact command, exact SQL, exact config key, or exact dashboard click path.

---

## Debug cleanup and post-mortem

Steps:
1. Run full test suite and record baseline: `<N> passing, <M> failing`
2. Run: `grep -r '\[DEBUG-' .` to find all tagged instrumentation
3. Remove all `[DEBUG-...]` tagged instrumentation
4. Re-run test suite and confirm no new failures vs baseline
5. Delete any throwaway harness files created during diagnosis
6. Run: `find docs/browser-checks -mindepth 1 -maxdepth 1 -type d | sort` to list all browser-check run dirs from this session
7. Delete stale browser-check run dirs: `rm -rf docs/browser-checks/<run_dir>`
8. State the winning hypothesis in the PR description
9. If no correct test seam existed, open `/improve-codebase-architecture` with the specific coupling details

Enrichment:
- Find tagged logs: `grep -rn '\[DEBUG-' .` and list each file and prefix found.
- Find throwaway harness files: check for any `*.scratch.*`, `test-harness.*`, or similar files created during the session.

---

## Apply Supabase database migrations

Steps:
1. Verify CLI is linked to the correct project:
   `supabase link --project-ref {project-ref}`
   Get the ref from: Supabase dashboard > project > Settings > General > Reference ID.
2. Run: `supabase db push`
3. Verify migrations applied: `supabase migration list`
4. Lint new migration files: `supabase db lint --linked`

Notes:
- `db push` and `migration list` connect to the linked remote project automatically.
- `db lint` requires the `--linked` flag (it defaults to local DB, which may not be running).
- If `db push` fails with `permission denied to alter role` or `SUPABASE_DB_PASSWORD` error, skip the CLI and apply the SQL directly in the Supabase dashboard SQL Editor instead (see fallback below). This is a known CLI bug with certain project configurations.

Fallback (when `supabase db push` fails with permission error):
1. Open Supabase dashboard > your project > SQL Editor > New Query.
2. Paste the contents of the migration file and run it.
3. Verify with: `SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}';`

Enrichment:
- List migration files: `ls supabase/migrations/` and include the specific filenames being applied.
- If migration creates tables or columns, show the schema changes so the user can verify.
- Find project ref: check `supabase/.temp/project-ref` or ask user to get it from Supabase dashboard.

## Deploy Supabase edge function

Steps:
1. Run: `supabase functions deploy {function-name}`
2. Verify: `supabase functions list` (confirm {function-name} shows updated timestamp)
3. Smoke-test the affected flow in the UI or with a curl call to confirm the fix is live.

Notes:
- Any task that modifies files under `supabase/functions/{name}/` requires a deploy of that function before the fix is live in production.
- New helper files added alongside `index.ts` (e.g. `activateMembership.ts`) are bundled automatically by the deploy command. No extra steps needed.

Enrichment:
- Find function name: `ls supabase/functions/` and match against the trigger task's diff (look for changed files under `supabase/functions/{name}/`).
- Check for env vars the function uses: grep `Deno.env.get` in the function source. List each var and whether it's set in Supabase dashboard.
- Always include a smoke-test step describing the specific user flow the task fixes.

## Add env var to {platform}

Steps:
1. Go to {platform} dashboard > {app-name} > Settings > Environment Variables (Cloudflare Pages new nav: Build > Compute > Workers & Pages > project > Settings > Variables and secrets)
2. Check the existing variable list first. If `{VAR_NAME}` is already listed, click that row to edit it — do NOT use "Add variable", it will fail with "Name already in use".
3. Set/confirm Value: {value_source}
4. Redeploy or restart the service if required by the platform (Cloudflare Pages: env var changes only take effect on the next deployment)

Notes:
- Cloudflare's dashboard nav changed (circa 2026): "Workers & Pages" is no longer a top-level sidebar item. It now lives under Build > Compute > Workers & Pages.
- Cloudflare's "Add variable" flow does not check for existing names until after you type one, and only surfaces "Name already in use" as a red inline error under the Variable name field — easy to miss/confuse with an actual failure. Always check the existing table first.

Enrichment:
- Find var names: grep `.env*` files, `process.env.`, `Deno.env.get()`, `import.meta.env` in the diff and codebase.
- Find value sources: check `.env.example`, README, or provider docs. Name the specific provider dashboard and page.
- Find platform: check deployment config (wrangler.toml, supabase/config.toml, vercel.json, netlify.toml).

## Configure Cloudflare Email Routing to Worker

Steps:
1. Deploy the Worker first (routing UI only shows deployed Workers as targets):
   ```
   cd cloudflare/{worker-dir}
   wrangler deploy
   ```
2. Verify Worker appears in Cloudflare dashboard > Workers & Pages. Confirm `{worker-name}` is listed.
3. Go to Cloudflare dashboard > {domain} > Email > Email Routing > Routing Rules.
4. Click "Create address" or "Add custom address".
5. Set "To address": `{email-address}` (e.g. read@ericlingren.com).
6. Set "Action": Send to a Worker.
7. Select Worker: `{worker-name}`.
8. Click Save.
9. Verify DNS tab shows MX records added for Cloudflare Email Routing.
10. Verify Email Routing status shows "Enabled".

Notes:
- "Send to a Worker" action does NOT appear if the Worker has not been deployed yet. Deploy first.
- Requires Cloudflare Email Routing to be enabled for the domain (free feature).

Enrichment:
- Find worker name: read `cloudflare/{worker-dir}/wrangler.toml`, `name` field.
- Find email address: grep wrangler.toml for destination address comment or check task description.
- Find domain: check Cloudflare dashboard or grep `workers_dev = false` in wrangler.toml configs.

## Run database query (manual)

Steps:
1. Open {platform} dashboard > SQL Editor > New Query
2. Run:
```sql
{query}
```
3. Verify: `{verification_query}`

Enrichment:
- Find table/column names: read the relevant migration files or grep the schema.
- Build the exact SQL from the task context. Include column names, values, WHERE clauses.
- Build a SELECT verification query the user can run to confirm.

---

## Create gitignored local config before first run

Use when a tool loads a gitignored config file (e.g. `config.local.json`, `.env.local`) and ships only an example (`config.example.json`). Add this as a step BEFORE any "run it the first time" follow-up.

Steps:
1. `cd {repo_root}`
2. Confirm the target is ignored: `git check-ignore -v {local_config}`. It must print a matching `.gitignore` rule.
3. `cp {example_config} {local_config}`
4. Fill each required key in `{local_config}`. One row per key: `{key}`: value `{value}`, found via `{lookup_command_or_dashboard_path}`.
5. Validate: `{check_command}`. Expect `{check_success_line}`.

Notes:
- Real values (org, login, email, team key) go only in the gitignored file. Never in the example, tests, or committed docs.
- A config `check` command often validates shape only and never calls the remote API. Treat values as unverified until the first real run succeeds.
- Do not paste secrets into chat. If a key is a secret, route it through the wizard snippet (`ask_secret`), not this runbook.

Enrichment:
- Find the loader: grep for the example filename (e.g. `grep -rn "config.example" .`) and read the required-keys list (e.g. `REQUIRED_KEYS`). Also list optional keys the loader accepts that the example omits.
- Find the check command: grep the CLI for a `check`, `doctor`, or `validate` subcommand.
- Pre-fill values from local sources where possible: GitHub org via `gh org list` or a sibling work repo's `git remote get-url origin`; login via `gh api user --jq .login`; work email via `git log --author="{name}" --format=%ae | sort | uniq -c`; Linear team key via the most common `[A-Z]+-[0-9]+` prefix in work-repo branch names.
- Write values straight into the gitignored file. Report each value with its source and say whether it was verified.
