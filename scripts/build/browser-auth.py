#!/usr/bin/env python3
"""
browser-auth.py — manage browser auth state for browser verification.

Usage:
  browser-auth.py ensure --repo <Org/Repo> --role <role-name> [--repo-dir <path>]

Reads the Auth Profile for the repo from claude-code-shared/resources/repo-policy.json.
Runs a Freshness probe: if the stored state file is recent and still valid, exits
immediately with the cached path.  Otherwise regenerates state and prints the path.

State files are written to ~/.cache/claude-browser-auth/<repo-label>/<role>.json.

Strategies:
  project_setup (with playwright_projects)
      Run the project's own Playwright setup project(s) via npx, copy output.
      Used by SpawnedSapien active/admin roles.

  project_setup (with login_endpoint, test-bypass login style)
      POST to Django TESTING_BYPASS_LOGIN endpoints to obtain session cookies,
      build a Playwright storageState from the response cookies.

  api_login (Supabase)
      Call Supabase REST signInWithPassword, construct a Playwright storageState
      with the session token written to localStorage.  No local server required.

Exit codes:
  0  success (absolute state-file path printed to stdout)
  1  unrecoverable failure (SKIPPED: <reason> printed to stderr)

Dependencies: Python 3.8+, playwright[chromium] (optional for freshness probe)
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_POLICY = SCRIPT_DIR.parents[1] / "resources" / "repo-policy.json"
AUTH_CACHE_BASE = Path.home() / ".cache" / "claude-browser-auth"
FRESHNESS_TTL = 8 * 3600  # seconds before a state file is considered stale
LOGIN_MARKERS = ["/login", "/signin", "/auth/login", "/accounts/login"]


# ── Error handling ────────────────────────────────────────────────────────────

def die_skipped(reason: str = "auth_expired") -> None:
    """Print SKIPPED: <reason> to stderr and exit 1."""
    print(f"SKIPPED: {reason}", file=sys.stderr)
    sys.exit(1)


# ── Policy loading ────────────────────────────────────────────────────────────

def load_policy() -> dict:
    """Load and return repo-policy.json."""
    try:
        with open(REPO_POLICY) as f:
            return json.load(f)
    except FileNotFoundError:
        die_skipped(f"repo-policy.json not found at {REPO_POLICY}")
    except json.JSONDecodeError as exc:
        die_skipped(f"invalid repo-policy.json: {exc}")


def get_entry(policy: dict, repo: str) -> tuple[dict, dict]:
    """Return (policy_entry, auth_profile) for the given repo key."""
    entry = policy.get(repo)
    if not entry:
        die_skipped(f"repo {repo!r} not found in repo-policy.json")
    auth_profile = entry.get("browser_auth")
    if not auth_profile:
        die_skipped(f"no browser_auth configured for {repo!r}")
    return entry, auth_profile


# ── Freshness probe ───────────────────────────────────────────────────────────

def is_fresh(state_path: Path, auth_profile: dict) -> bool:
    """
    Return True when the cached state file is recent enough to reuse.

    Primary check : file modification time within FRESHNESS_TTL.
    Secondary check: optional headless Playwright probe to probe_url declared
                     in the auth profile.  Falls back to timestamp on failure.
    """
    if not state_path.exists():
        return False

    age = time.time() - state_path.stat().st_mtime
    if age > FRESHNESS_TTL:
        return False

    # api_login sessions (e.g. Supabase) expire well before FRESHNESS_TTL.
    if _stored_session_expired(state_path):
        return False

    probe_url = auth_profile.get("probe_url")
    if probe_url:
        try:
            return _playwright_freshness_check(state_path, probe_url)
        except Exception:
            # Server unreachable, Playwright not installed, timeout, etc.
            # Trust the timestamp result (file is recent).
            return True

    return True


SESSION_EXPIRY_MARGIN = 120  # seconds of validity required before reusing a session


def _stored_session_expired(state_path: Path) -> bool:
    """
    Return True when any localStorage session in the state file carries an
    `expires_at` (epoch seconds) that has passed or is within the margin.
    State files without such a field are left to the other checks.
    """
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True

    for origin_entry in state.get("origins", []):
        for item in origin_entry.get("localStorage", []):
            try:
                session = json.loads(item.get("value", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            expires_at = session.get("expires_at") if isinstance(session, dict) else None
            if isinstance(expires_at, (int, float)) and expires_at < time.time() + SESSION_EXPIRY_MARGIN:
                return True
    return False


def _playwright_freshness_check(state_path: Path, probe_url: str) -> bool:
    """
    Navigate to probe_url with stored state; return True if not redirected
    to a login page.  Raises on any non-login failure so is_fresh can fall back.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(state_path))
        page = ctx.new_page()
        try:
            page.goto(probe_url, timeout=10_000, wait_until="domcontentloaded")
            final_url = page.url
            redirected = any(m in final_url for m in LOGIN_MARKERS)
            return not redirected
        finally:
            browser.close()


# ── Regeneration dispatcher ───────────────────────────────────────────────────

def regen(
    entry: dict,
    auth_profile: dict,
    role: str,
    repo_dir: Path,
    state_path: Path,
) -> None:
    """Regenerate auth state and write it to state_path (chmod 600)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)

    roles = auth_profile.get("roles")

    if isinstance(roles, dict):
        # Per-role config (SpawnedSapien style)
        role_cfg = roles.get(role)
        if not role_cfg:
            die_skipped(f"role {role!r} not found in browser_auth.roles")
        strategy = role_cfg["strategy"]
    elif isinstance(roles, list):
        # Single strategy for multiple roles (test-bypass login style)
        if role not in roles:
            die_skipped(f"role {role!r} not in browser_auth.roles list")
        strategy = auth_profile.get("strategy")
        role_cfg = auth_profile
    else:
        die_skipped("browser_auth.roles must be a list or dict")

    if strategy == "project_setup":
        _regen_project_setup(auth_profile, role, role_cfg, repo_dir, state_path)
    elif strategy == "api_login":
        _regen_api_login(role, role_cfg, repo_dir, state_path)
    else:
        die_skipped(f"unknown auth strategy {strategy!r}")

    state_path.chmod(0o600)


# ── project_setup strategy ────────────────────────────────────────────────────

def _regen_project_setup(
    auth_profile: dict,
    role: str,
    role_cfg: dict,
    repo_dir: Path,
    state_path: Path,
) -> None:
    """
    Dispatch to the right project_setup implementation:
      - playwright_projects in role_cfg → SpawnedSapien npx runner
      - login_endpoint in auth_profile  → test-bypass login POST
    """
    if "playwright_projects" in role_cfg:
        _regen_playwright_projects(role_cfg, repo_dir, state_path)
    else:
        _regen_bypass_login(auth_profile, role, state_path)


def _regen_playwright_projects(
    role_cfg: dict,
    repo_dir: Path,
    state_path: Path,
) -> None:
    """
    Run `npx playwright test --project=<name>` for each setup project,
    then copy the declared output file to state_path.
    """
    for project_name in role_cfg["playwright_projects"]:
        result = subprocess.run(
            ["npx", "playwright", "test", f"--project={project_name}"],
            cwd=str(repo_dir),
        )
        if result.returncode != 0:
            die_skipped(
                f"playwright --project={project_name} exited {result.returncode}"
            )

    outputs = role_cfg.get("outputs", [])
    if not outputs:
        die_skipped("no outputs declared for project_setup role")

    src = repo_dir / outputs[0]
    if not src.exists():
        die_skipped(f"playwright setup did not produce expected file: {src}")

    shutil.copy2(str(src), str(state_path))


def _regen_bypass_login(
    auth_profile: dict,
    role: str,
    state_path: Path,
) -> None:
    """
    POST to the app's TESTING_BYPASS_LOGIN endpoints in order, collect
    Set-Cookie headers via http.cookiejar, then write Playwright storageState.
    """
    base_url = auth_profile.get("base_url", "http://127.0.0.1:8000")
    login_endpoints: list[str] = auth_profile.get("login_endpoint", [])

    _check_server_reachable(base_url)

    # Build ordered call list: setup endpoint first, then role-specific auth
    setup_ep = "/user/setup-test-users-and-entities/"
    role_ep = f"/user/authenticate-test-{role}-user/"

    ordered: list[str] = []
    if setup_ep in login_endpoints:
        ordered.append(setup_ep)
    if role_ep in login_endpoints:
        ordered.append(role_ep)
    elif login_endpoints:
        # Fallback: first non-setup endpoint in the list
        for ep in login_endpoints:
            if ep != setup_ep:
                ordered.append(ep)
                break

    if not ordered:
        die_skipped(f"no login endpoints configured for role {role!r}")

    # POST each endpoint, tracking cookies across the session
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

    for ep in ordered:
        url = base_url.rstrip("/") + ep
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            if exc.code >= 400:
                die_skipped(f"endpoint {ep} returned HTTP {exc.code}")
        except Exception as exc:
            die_skipped(f"endpoint {ep} failed: {exc}")

    # Serialize cookies to Playwright storageState format
    parsed = urllib.parse.urlparse(base_url)
    pw_cookies = []
    for cookie in jar:
        raw_domain = cookie.domain or ""
        domain = raw_domain.lstrip(".") if raw_domain else (parsed.hostname or "")
        pw_cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": domain,
                "path": cookie.path or "/",
                "expires": int(cookie.expires) if cookie.expires else -1,
                "httpOnly": False,
                "secure": bool(cookie.secure),
                "sameSite": "Lax",
            }
        )

    storage_state = {"cookies": pw_cookies, "origins": []}
    with open(str(state_path), "w") as f:
        json.dump(storage_state, f, indent=2)


# ── api_login strategy (Supabase) ─────────────────────────────────────────────

def _regen_api_login(
    role: str,
    role_cfg: dict,
    repo_dir: Path,
    state_path: Path,
) -> None:
    """
    Authenticate via Supabase REST signInWithPassword.
    Credentials are read from the project's .env.local.
    The resulting session token is written directly to a Playwright
    storageState (no local server or Playwright browser required).
    """
    env_file = repo_dir / role_cfg.get("env_file", ".env.local")
    if not env_file.exists():
        die_skipped(f".env file not found: {env_file}")

    env = _parse_env_file(env_file)

    email = env.get(role_cfg["email_env"])
    password = env.get(role_cfg["password_env"])
    if not email or not password:
        die_skipped(
            f"missing {role_cfg['email_env']} or {role_cfg['password_env']} "
            f"in {env_file.name}"
        )

    supabase_url = (
        env.get("VITE_SUPABASE_URL")
        or env.get("NEXT_PUBLIC_SUPABASE_URL")
        or env.get("SUPABASE_URL")
    )
    anon_key = (
        env.get("VITE_SUPABASE_ANON_KEY")
        or env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        or env.get("SUPABASE_ANON_KEY")
    )
    if not supabase_url or not anon_key:
        die_skipped(
            "missing SUPABASE_URL or SUPABASE_ANON_KEY in .env.local"
        )

    session = _supabase_signin(supabase_url, anon_key, email, password)
    _write_supabase_storage_state(session, supabase_url, state_path)


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env / .env.local file and return a {key: value} dict."""
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip surrounding single or double quotes
            if (
                len(val) >= 2
                and val[0] == val[-1]
                and val[0] in ('"', "'")
            ):
                val = val[1:-1]
            env[key] = val
    return env


def _supabase_signin(
    supabase_url: str,
    anon_key: str,
    email: str,
    password: str,
) -> dict:
    """POST to Supabase /auth/v1/token?grant_type=password and return session."""
    url = f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
    payload = json.dumps({"email": email, "password": password}).encode()
    headers = {
        "apikey": anon_key,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        die_skipped(f"supabase signIn failed ({exc.code}): {body[:200]}")
    except Exception as exc:
        die_skipped(f"supabase signIn error: {exc}")


def _write_supabase_storage_state(
    session: dict,
    supabase_url: str,
    state_path: Path,
) -> None:
    """
    Construct a Playwright storageState with the Supabase session token
    set in localStorage for the SpawnedSapien Vite app origin.

    Supabase JS v2 uses the key sb-<project-ref>-auth-token.
    """
    parsed = urllib.parse.urlparse(supabase_url)
    hostname = parsed.hostname or "project"
    project_ref = hostname.split(".")[0]
    storage_key = f"sb-{project_ref}-auth-token"

    session_json = json.dumps(
        {
            "access_token": session.get("access_token"),
            "token_type": "bearer",
            "expires_in": session.get("expires_in", 3600),
            "expires_at": session.get("expires_at"),
            "refresh_token": session.get("refresh_token"),
            "user": session.get("user"),
        }
    )

    # SpawnedSapien Vite app default origin
    app_origin = "http://localhost:5173"

    storage_state = {
        "cookies": [],
        "origins": [
            {
                "origin": app_origin,
                "localStorage": [
                    {"name": storage_key, "value": session_json}
                ],
            }
        ],
    }

    with open(str(state_path), "w") as f:
        json.dump(storage_state, f, indent=2)


# ── Seed hooks ────────────────────────────────────────────────────────────────

def run_seed_hooks(auth_profile: dict) -> None:
    """POST each seed_hook URL declared in the auth profile (non-fatal)."""
    hooks: list[str] = auth_profile.get("seed_hooks", [])
    if not hooks:
        return

    base_url = auth_profile.get("base_url", "http://127.0.0.1:8000")

    for hook in hooks:
        url = base_url.rstrip("/") + hook
        try:
            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # seed hooks are best-effort


# ── Server connectivity helper ────────────────────────────────────────────────

def _check_server_reachable(base_url: str) -> None:
    """
    Verify the server is accepting connections.
    Calls die_skipped("auth_expired") on failure so callers get the right exit.
    Any HTTP error code is fine — that means the server is up.
    """
    try:
        req = urllib.request.Request(base_url, method="HEAD")
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError:
        pass  # HTTP error = server is running
    except Exception:
        die_skipped("auth_expired")


# ── ensure subcommand ─────────────────────────────────────────────────────────

def cmd_ensure(args: argparse.Namespace) -> None:
    policy = load_policy()
    entry, auth_profile = get_entry(policy, args.repo)

    label = entry["label"]
    state_path = AUTH_CACHE_BASE / label / f"{args.role}.json"
    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else Path.cwd()

    # Freshness probe: skip regen when state is still valid
    if is_fresh(state_path, auth_profile):
        print(str(state_path))
        return

    # Regenerate auth state
    regen(entry, auth_profile, args.role, repo_dir, state_path)

    # Run seed hooks (non-fatal; run after first successful regen)
    run_seed_hooks(auth_profile)

    print(str(state_path))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage browser auth state for verification checks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ensure = sub.add_parser(
        "ensure",
        help="Ensure a valid auth state file exists for a repo/role.",
    )
    ensure.add_argument(
        "--repo",
        required=True,
        metavar="ORG/REPO",
        help='Repo key from repo-policy.json, e.g. "Eric-Lingren/SpawnedSapien"',
    )
    ensure.add_argument(
        "--role",
        required=True,
        help="Role name to authenticate (e.g. active, admin, firm, company)",
    )
    ensure.add_argument(
        "--repo-dir",
        default=None,
        metavar="PATH",
        help="Project root directory (default: current working directory). "
             "Required for project_setup roles that run npx playwright test.",
    )

    args = parser.parse_args()
    if args.cmd == "ensure":
        cmd_ensure(args)


if __name__ == "__main__":
    main()
