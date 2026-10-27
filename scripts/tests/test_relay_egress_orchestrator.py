"""Tests for relay skill refactoring to channel-agnostic egress orchestrator (T-0087).

Phase A — Characterization tests: verify stable behaviors that must survive the refactor.
         All tests in this section are GREEN before any changes are made.

Phase B — Target architecture tests: verify the desired state after refactoring.
         These tests start RED and turn GREEN when the refactor is complete.

Phase C — E2E smoke test: verify dispatch-tasks -> relay -> post-github chain consistency.
"""

import pathlib

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"

RELAY_SKILL = SHARED / "skills" / "relay" / "SKILL.md"
POST_GITHUB = SHARED / "agents" / "egress" / "github" / "post-github.md"
DISPATCH_TASKS = SHARED / "skills" / "dispatch-tasks" / "SKILL.md"


# ===========================================================================
# Phase A — Characterization tests (GREEN before AND after refactoring)
# ===========================================================================


class TestRelayCharacterization:
    """Capture stable relay behaviors that must survive the refactor."""

    def test_relay_skill_exists(self):
        assert RELAY_SKILL.exists(), f"relay SKILL.md not found at {RELAY_SKILL}"

    def test_relay_validates_schema_before_processing(self):
        """relay must run validate-schema.sh before processing any task file."""
        text = RELAY_SKILL.read_text()
        assert "validate-schema.sh" in text, (
            "relay SKILL.md must describe schema validation (validate-schema.sh) before processing"
        )

    def test_relay_filters_reply_tasks_by_task_type(self):
        """relay selects only task_type == 'reply' items from the task file."""
        text = RELAY_SKILL.read_text()
        assert "task_type" in text, "relay SKILL.md must reference task_type for filtering"
        assert "reply" in text, "relay SKILL.md must filter on 'reply' task type"

    def test_relay_checks_blocked_by_status(self):
        """relay skips reply tasks whose blocking code task is not yet done/merged."""
        text = RELAY_SKILL.read_text()
        assert "blocked_by" in text, (
            "relay SKILL.md must describe checking blocked_by before processing each reply task"
        )

    def test_relay_has_hitl_approval_gate(self):
        """relay presents combined drafts to the operator for approval before any post."""
        text = RELAY_SKILL.read_text()
        # The HITL gate is described as presenting drafts for approval
        has_approval = any(
            kw in text
            for kw in ("approve", "HITL", "approval", "final review")
        )
        assert has_approval, (
            "relay SKILL.md must describe a HITL approval gate before posting"
        )

    def test_relay_maintains_copy_only_invariant(self):
        """relay enforces NO EXTERNAL CALLS MADE — nothing is posted without explicit approval."""
        text = RELAY_SKILL.read_text()
        assert "NO EXTERNAL CALLS MADE" in text, (
            "relay SKILL.md must include the NO EXTERNAL CALLS MADE invariant banner"
        )

    def test_relay_mentions_thread_id_fields(self):
        """relay is aware of thread_id and thread_id_type task fields for future posting."""
        text = RELAY_SKILL.read_text()
        assert "thread_id" in text, (
            "relay SKILL.md must reference thread_id (needed for threaded replies)"
        )

    def test_relay_has_learning_capture_tail(self):
        """All skills must end with the learning-capture tail block."""
        text = RELAY_SKILL.read_text()
        assert "learning-capture:start" in text, (
            "relay SKILL.md missing learning-capture tail block (required for all skills)"
        )
        assert "relay" in text[text.find("learning-capture:start"):], (
            "relay SKILL.md learning-capture block must reference the 'relay' slug"
        )

    def test_dispatch_tasks_routes_reply_branch_to_relay(self):
        """dispatch-tasks must describe routing the reply branch to the relay skill."""
        text = DISPATCH_TASKS.read_text()
        # dispatch-tasks should mention relay as the runner for the reply branch
        assert "relay" in text, (
            "dispatch-tasks SKILL.md must reference relay as the reply branch runner"
        )

    def test_post_github_stub_exists(self):
        """post-github.md must exist as the GitHub channel adapter."""
        assert POST_GITHUB.exists(), f"post-github.md not found at {POST_GITHUB}"


# ===========================================================================
# Phase B — Target architecture tests (RED before refactor, GREEN after)
# ===========================================================================


class TestRelayTargetArchitecture:
    """Describe the desired state after refactoring relay into a channel-agnostic orchestrator."""

    def test_relay_routes_by_reply_url(self):
        """relay reads reply_url to determine which channel adapter to use."""
        text = RELAY_SKILL.read_text()
        assert "reply_url" in text, (
            "relay SKILL.md must describe using reply_url to determine the channel"
        )
        # relay must mention routing by URL — "github.com" domain detection or adapter dispatch
        has_routing = any(
            kw in text
            for kw in ("github.com", "channel", "adapter", "agents/egress")
        )
        assert has_routing, (
            "relay SKILL.md must describe routing by reply_url domain to a channel adapter"
        )

    def test_relay_dispatches_to_egress_adapters(self):
        """relay mentions the agents/egress/ channel adapter directory."""
        text = RELAY_SKILL.read_text()
        assert "agents/egress" in text, (
            "relay SKILL.md must reference agents/egress/ channel adapters for dispatch"
        )

    def test_relay_dispatches_to_post_github_for_github_urls(self):
        """relay explicitly routes GitHub reply_url values to the post-github adapter."""
        text = RELAY_SKILL.read_text()
        assert "post-github" in text, (
            "relay SKILL.md must name post-github as the adapter for GitHub-hosted reply_urls"
        )

    def test_relay_does_not_directly_construct_commit_permalinks(self):
        """relay must NOT directly construct commit permalinks — that logic moved to post-github."""
        text = RELAY_SKILL.read_text()
        # The PR-specific permalink pattern "/commit/" should not appear in relay
        assert "/commit/" not in text, (
            "relay SKILL.md must not directly construct commit permalinks "
            "(this logic belongs in agents/egress/github/post-github.md)"
        )

    def test_post_github_handles_commit_permalink_construction(self):
        """post-github must describe constructing a commit permalink from SHA + repo URL."""
        text = POST_GITHUB.read_text()
        has_permalink = any(
            kw in text
            for kw in ("/commit/", "commit permalink", "fixing commit", "commit sha", "SHA")
        )
        assert has_permalink, (
            "post-github.md must describe commit permalink construction "
            "(<repo-url>/commit/<sha>) — this was moved from relay"
        )

    def test_post_github_handles_thread_id_keying(self):
        """post-github must describe thread_id / thread_id_type for future write-back."""
        text = POST_GITHUB.read_text()
        assert "thread_id" in text, (
            "post-github.md must reference thread_id for threaded reply keying"
        )
        assert "thread_id_type" in text, (
            "post-github.md must reference thread_id_type to disambiguate thread key type"
        )

    def test_post_github_accepts_commit_and_pr_inputs(self):
        """post-github must document commit and pr as optional inputs for permalink stitching."""
        text = POST_GITHUB.read_text()
        assert "commit" in text, (
            "post-github.md must accept 'commit' as input for permalink construction"
        )


# ===========================================================================
# Phase C — E2E smoke test: dispatch-tasks -> relay -> post-github chain
# ===========================================================================


class TestDispatchRelayPostGithubChain:
    """Verify the full chain of references is internally consistent."""

    def test_dispatch_tasks_has_reply_branch(self):
        """dispatch-tasks must describe the reply branch in its routing table."""
        text = DISPATCH_TASKS.read_text()
        assert "reply" in text, "dispatch-tasks must describe a reply branch"

    def test_relay_references_channel_adapters(self):
        """relay must reference channel adapters — completing the dispatch->relay->adapter chain."""
        relay_text = RELAY_SKILL.read_text()
        post_github_text = POST_GITHUB.read_text()
        # relay must dispatch to something, and post-github must exist and be a valid adapter
        assert "agents/egress" in relay_text, (
            "relay must reference agents/egress/ adapters to complete the dispatch chain"
        )
        assert "NO EXTERNAL CALLS MADE" in post_github_text, (
            "post-github must have the copy-only invariant banner — it is the terminal adapter"
        )

    def test_post_github_returns_schema_valid_egress_result(self):
        """post-github's example output must be a schema-valid egress-result."""
        import json
        import re
        import jsonschema

        schema_path = SHARED / "contracts" / "egress-result-schema.json"
        assert schema_path.exists(), f"egress-result-schema.json not found at {schema_path}"

        text = POST_GITHUB.read_text()
        pattern = r"```json\s*([\s\S]*?)```"
        match = re.search(pattern, text)
        assert match, "post-github.md must contain a ```json block with an example egress-result"

        result = json.loads(match.group(1).strip())
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(result, schema)  # raises ValidationError if invalid
        assert result["status"] == "copy-only", (
            f"post-github.md example must have status='copy-only', got {result['status']!r}"
        )
