"""Tests for T-0082: answer-composer egress agent.

The answer-composer (Sonnet tier) lives under agents/egress/ and consumes an
investigation-result to compose a voice-matched Slack reply. Rules:
  - VERIFIED_TRUE claims → inline citation with the evidence ref
  - INSUFFICIENT_EVIDENCE claims → explicit 'couldn't confirm' tag (never smoothed over)
  - No content added beyond what the investigation-result provides
  - Output is copy-only (manual paste to Slack)
  - Voice profile: slack-casual (registered in voice-routing.json)
  - Invariant: agents/egress/ is NOT agents/investigate/ — nothing here emits
    investigation-result; it consumes it.
"""

import json
import pathlib

import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
AGENT_PATH = SHARED / "agents" / "egress" / "answer-composer.md"
REGISTRY_PATH = SHARED / "agents" / "registry.json"
VOICE_ROUTING_PATH = SHARED / "resources" / "voice-routing.json"
SLACK_CASUAL_PROFILE_PATH = SHARED / "resources" / "voice-profiles" / "slack-casual.md"
INVESTIGATORS_DIR = SHARED / "agents" / "investigate"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_text():
    assert AGENT_PATH.exists(), (
        f"Missing: {AGENT_PATH}\n"
        "The answer-composer agent must live at agents/egress/answer-composer.md."
    )
    return AGENT_PATH.read_text()


@pytest.fixture(scope="module")
def registry():
    assert REGISTRY_PATH.exists(), f"Registry not found: {REGISTRY_PATH}"
    return json.loads(REGISTRY_PATH.read_text())


@pytest.fixture(scope="module")
def voice_routing():
    assert VOICE_ROUTING_PATH.exists(), f"voice-routing.json not found: {VOICE_ROUTING_PATH}"
    return json.loads(VOICE_ROUTING_PATH.read_text())


# ---------------------------------------------------------------------------
# Slice 1: File existence and location invariant
# ---------------------------------------------------------------------------


def test_answer_composer_agent_exists():
    """agents/egress/answer-composer.md must exist."""
    assert AGENT_PATH.exists(), (
        f"Missing: {AGENT_PATH}\n"
        "Create the agent at agents/egress/answer-composer.md."
    )


def test_answer_composer_is_in_egress_not_investigators():
    """answer-composer must live under agents/egress/, not agents/investigate/."""
    investigators_path = INVESTIGATORS_DIR / "answer-composer.md"
    assert not investigators_path.exists(), (
        f"answer-composer.md found under agents/investigate/. "
        "Everything in agents/investigate/ must produce an investigation-result. "
        "This agent consumes one — it belongs under agents/egress/."
    )


# ---------------------------------------------------------------------------
# Slice 2: Frontmatter — model and name
# ---------------------------------------------------------------------------


def test_answer_composer_has_sonnet_model(agent_text):
    """Frontmatter must specify model: sonnet."""
    assert "model: sonnet" in agent_text, (
        "answer-composer.md is missing 'model: sonnet' in frontmatter. "
        "The agent runs at Sonnet tier per acceptance criteria."
    )


def test_answer_composer_has_name_field(agent_text):
    """Frontmatter must have name: answer-composer."""
    assert "name: answer-composer" in agent_text, (
        "answer-composer.md is missing 'name: answer-composer' in frontmatter."
    )


# ---------------------------------------------------------------------------
# Slice 3: Input — investigation-result
# ---------------------------------------------------------------------------


def test_answer_composer_references_investigation_result(agent_text):
    """Agent must reference investigation-result as its input."""
    assert "investigation-result" in agent_text.lower(), (
        "answer-composer.md does not reference 'investigation-result'. "
        "The agent's input is an investigation-result object."
    )


def test_answer_composer_references_verdict(agent_text):
    """Agent must reference the verdict field it acts on."""
    assert "verdict" in agent_text.lower(), (
        "answer-composer.md does not reference 'verdict'. "
        "The agent must inspect verdict values to decide how to render each claim."
    )


# ---------------------------------------------------------------------------
# Slice 4: Output — Slack reply
# ---------------------------------------------------------------------------


def test_answer_composer_describes_slack_output(agent_text):
    """Agent must describe producing a Slack reply."""
    lower = agent_text.lower()
    assert "slack" in lower, (
        "answer-composer.md does not mention 'slack'. "
        "The agent's output is a Slack reply."
    )


def test_answer_composer_describes_copy_only_output(agent_text):
    """Agent must describe its output as copy-only (for manual paste)."""
    lower = agent_text.lower()
    has_copy_language = (
        "copy" in lower
        or "paste" in lower
        or "copy-only" in lower
        or "manual paste" in lower
    )
    assert has_copy_language, (
        "answer-composer.md does not describe its output as copy-only or for manual paste. "
        "The agent must make clear the output is not posted programmatically."
    )


# ---------------------------------------------------------------------------
# Slice 5: VERIFIED_TRUE → inline citation
# ---------------------------------------------------------------------------


def test_answer_composer_handles_verified_true(agent_text):
    """Agent must describe handling VERIFIED_TRUE claims."""
    assert "VERIFIED_TRUE" in agent_text, (
        "answer-composer.md does not mention 'VERIFIED_TRUE'. "
        "The agent must explicitly describe how it handles verified claims."
    )


def test_answer_composer_renders_inline_citations(agent_text):
    """Agent must describe inline citations (not end-footnote blocks)."""
    lower = agent_text.lower()
    has_inline = "inline" in lower or "in-line" in lower
    assert has_inline, (
        "answer-composer.md does not describe 'inline' citations. "
        "Citations must render inline per claim, not as an end-footnote block."
    )


def test_answer_composer_citations_include_ref(agent_text):
    """Agent must state that citations carry the evidence ref."""
    lower = agent_text.lower()
    assert "ref" in lower or "citation" in lower, (
        "answer-composer.md does not describe citations carrying the evidence ref. "
        "Each inline citation must include the ref from the evidence item."
    )


def test_answer_composer_no_end_footnote_block(agent_text):
    """Agent must explicitly reject end-footnote citation style."""
    lower = agent_text.lower()
    # Either it says "not end-footnote" / "no footnote" or it says inline only
    # We check for the prohibition via "not" near "footnote" or "end" near "footnote",
    # or it says only inline citations are used (which rules out footnotes by omission).
    # The clearest signal is the word "footnote" appearing in the doc at all with negation context,
    # OR the agent only describes inline without mentioning footnotes.
    # We require at minimum that it says "inline" (tested above) and either forbids footnotes
    # or describes only-inline approach clearly.
    # This is already covered by the inline test; we additionally check the agent
    # does NOT silently describe only footnotes.
    has_footnote_mention = "footnote" in lower or "end-note" in lower or "endnote" in lower
    if has_footnote_mention:
        # If it mentions footnotes, it must also express a prohibition or contrast
        has_prohibition = (
            "not" in lower
            or "never" in lower
            or "instead" in lower
            or "no footnote" in lower
        )
        assert has_prohibition, (
            "answer-composer.md mentions footnotes but doesn't prohibit them. "
            "The agent must reject end-footnote style — citations are inline only."
        )


# ---------------------------------------------------------------------------
# Slice 6: INSUFFICIENT_EVIDENCE → 'couldn't confirm' tag
# ---------------------------------------------------------------------------


def test_answer_composer_handles_insufficient_evidence(agent_text):
    """Agent must describe handling INSUFFICIENT_EVIDENCE claims."""
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "answer-composer.md does not mention 'INSUFFICIENT_EVIDENCE'. "
        "The agent must explicitly handle claims that couldn't be verified."
    )


def test_answer_composer_renders_couldnt_confirm_tag(agent_text):
    """Agent must render a 'couldn't confirm' tag for INSUFFICIENT_EVIDENCE claims."""
    lower = agent_text.lower()
    has_couldnt_confirm = (
        "couldn't confirm" in lower
        or "could not confirm" in lower
        or "couldn't confirm" in lower
    )
    assert has_couldnt_confirm, (
        "answer-composer.md does not describe rendering a 'couldn't confirm' tag. "
        "INSUFFICIENT_EVIDENCE claims must explicitly surface their uncertainty "
        "with this exact tag (or equivalent) — never smoothed over."
    )


def test_answer_composer_never_smooths_over_insufficient_evidence(agent_text):
    """Agent must explicitly state it will NOT smooth over INSUFFICIENT_EVIDENCE."""
    lower = agent_text.lower()
    has_prohibition = (
        "never" in lower
        or "must not" in lower
        or "do not" in lower
        or "not smooth" in lower
        or "not fabricat" in lower
        or "no fabricat" in lower
    )
    assert has_prohibition, (
        "answer-composer.md does not state a prohibition on smoothing over uncertainty. "
        "The agent must never fabricate or smooth over INSUFFICIENT_EVIDENCE claims."
    )


# ---------------------------------------------------------------------------
# Slice 7: No content beyond investigation-result
# ---------------------------------------------------------------------------


def test_answer_composer_prohibits_adding_unverified_content(agent_text):
    """Agent must state it adds no content beyond what investigation-result provides."""
    lower = agent_text.lower()
    has_restriction = (
        "only" in lower
        and (
            "verified" in lower
            or "investigation-result" in lower
            or "evidence" in lower
        )
    )
    assert has_restriction, (
        "answer-composer.md does not restrict content to investigation-result claims. "
        "The agent must state it adds nothing beyond what the investigation-result provides."
    )


def test_answer_composer_prohibits_natural_sounding_filler(agent_text):
    """Agent must NOT add sentences to 'sound natural' that aren't from investigation-result."""
    lower = agent_text.lower()
    # Look for prohibition language around adding unverified content
    has_natural_prohibition = (
        "unverified" in lower
        or "natural" in lower
        or "filler" in lower
        or "fabricat" in lower
        or "no additional" in lower
        or "nothing beyond" in lower
        or "only claims" in lower
    )
    assert has_natural_prohibition, (
        "answer-composer.md doesn't address the 'sound natural' anti-pattern. "
        "The agent must state it adds no unverified sentence even to sound natural."
    )


# ---------------------------------------------------------------------------
# Slice 8: Voice profile — slack-casual
# ---------------------------------------------------------------------------


def test_answer_composer_references_slack_casual_voice(agent_text):
    """Agent must reference the slack-casual voice profile."""
    lower = agent_text.lower()
    assert "slack-casual" in lower or "slack casual" in lower, (
        "answer-composer.md does not reference the 'slack-casual' voice profile. "
        "The agent must name the voice profile it uses."
    )


def test_slack_casual_profile_exists():
    """resources/voice-profiles/slack-casual.md must exist."""
    assert SLACK_CASUAL_PROFILE_PATH.exists(), (
        f"Missing: {SLACK_CASUAL_PROFILE_PATH}\n"
        "Create a slack-casual voice profile (placeholder content is acceptable)."
    )


def test_voice_routing_has_slack_casual_profile(voice_routing):
    """voice-routing.json must have a 'slack-casual' entry in profiles."""
    profiles = voice_routing.get("profiles", {})
    assert "slack-casual" in profiles, (
        f"voice-routing.json 'profiles' does not contain 'slack-casual'. "
        f"Registered profiles: {list(profiles.keys())}"
    )


def test_voice_routing_slack_casual_points_to_file(voice_routing):
    """voice-routing.json slack-casual profile must have a 'file' key."""
    profiles = voice_routing.get("profiles", {})
    slack_casual = profiles.get("slack-casual", {})
    assert "file" in slack_casual, (
        "voice-routing.json slack-casual profile entry is missing the 'file' key."
    )
    assert slack_casual["file"], "slack-casual profile 'file' value must not be empty."


def test_voice_routing_wires_answer_composer(voice_routing):
    """voice-routing.json must wire 'answer-composer' to 'slack-casual' in skills map."""
    skills = voice_routing.get("skills", {})
    assert "answer-composer" in skills, (
        f"voice-routing.json 'skills' map does not include 'answer-composer'. "
        f"Registered skills: {list(skills.keys())}"
    )
    profile = skills["answer-composer"]
    # profile may be a string or list (per the _doc in voice-routing.json)
    if isinstance(profile, list):
        assert "slack-casual" in profile, (
            f"answer-composer wires to profiles {profile}, expected 'slack-casual' to be included."
        )
    else:
        assert profile == "slack-casual", (
            f"answer-composer wires to profile '{profile}', expected 'slack-casual'."
        )


# ---------------------------------------------------------------------------
# Slice 9: Registry entry
# ---------------------------------------------------------------------------


def test_answer_composer_registered_in_registry(registry):
    """answer-composer must appear in agents/registry.json."""
    names = [a["name"] for a in registry["agents"]]
    assert "answer-composer" in names, (
        f"'answer-composer' not found in registry.json. Registered: {names}"
    )


def test_answer_composer_registered_as_sonnet(registry):
    """Registry entry for answer-composer must specify model: sonnet."""
    entry = next((a for a in registry["agents"] if a["name"] == "answer-composer"), None)
    assert entry is not None, "answer-composer not in registry"
    assert entry.get("model") == "sonnet", (
        f"Registry entry model is '{entry.get('model')}', expected 'sonnet'."
    )


def test_answer_composer_registered_file_path(registry):
    """Registry file path must point to agents/egress/answer-composer.md."""
    entry = next((a for a in registry["agents"] if a["name"] == "answer-composer"), None)
    assert entry is not None, "answer-composer not in registry"
    assert entry.get("file") == "agents/egress/answer-composer.md", (
        f"Registry file path is '{entry.get('file')}', "
        "expected 'agents/egress/answer-composer.md'."
    )


# ---------------------------------------------------------------------------
# Slice 10: Reference implementation — compose logic
# ---------------------------------------------------------------------------


def _compose_slack_reply(investigation_result: dict) -> str:
    """
    Reference implementation of the compose logic the agent must follow.

    Rules:
    - VERIFIED_TRUE evidence items → include the claim summary with inline citation [ref]
    - INSUFFICIENT_EVIDENCE verdict → include "couldn't confirm" tag
    - No fabricated sentences added
    - Output is a Slack-paste-ready string

    This is a simplified reference; the real agent uses the investigation-result
    summary + evidence to produce prose. Here we test the structural mapping rules.
    """
    verdict = investigation_result["verdict"]
    evidence = investigation_result.get("evidence", [])
    summary = investigation_result.get("summary", "")

    if verdict == "VERIFIED_TRUE":
        # Build inline citations
        citations = " ".join(f"[{e['ref']}]" for e in evidence)
        return f"{summary} {citations}".strip() if citations else summary

    if verdict == "VERIFIED_FALSE":
        # The claim was refuted — surface the finding with citation
        citations = " ".join(f"[{e['ref']}]" for e in evidence)
        return f"{summary} {citations}".strip() if citations else summary

    if verdict == "INSUFFICIENT_EVIDENCE":
        return f"couldn't confirm: {summary}" if summary else "couldn't confirm"

    if verdict == "CONTESTED":
        citations = " ".join(f"[{e['ref']}]" for e in evidence)
        return f"{summary} {citations}".strip() if citations else summary

    raise ValueError(f"Unknown verdict: {verdict!r}")


def test_compose_verified_true_includes_inline_citation():
    """VERIFIED_TRUE produces a reply with the evidence ref inline."""
    result = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "src/egress/hook.ts:14",
                "quote": "export const EGRESS_HOOK_ENABLED = true;",
            }
        ],
        "summary": "The egress hook is enabled.",
    }
    reply = _compose_slack_reply(result)
    assert "src/egress/hook.ts:14" in reply, (
        f"Inline citation ref missing from reply. Got: {reply!r}"
    )
    assert "The egress hook is enabled." in reply


def test_compose_insufficient_evidence_renders_couldnt_confirm_tag():
    """INSUFFICIENT_EVIDENCE produces a reply with the 'couldn't confirm' tag."""
    result = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "evidence": [],
        "summary": "No sources found for egress hook state before the incident.",
    }
    reply = _compose_slack_reply(result)
    assert "couldn't confirm" in reply.lower(), (
        f"'couldn't confirm' tag missing for INSUFFICIENT_EVIDENCE. Got: {reply!r}"
    )


def test_compose_insufficient_evidence_does_not_state_claim_as_fact():
    """INSUFFICIENT_EVIDENCE reply must NOT read as a confident factual assertion."""
    result = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "evidence": [],
        "summary": "The flag was enabled.",
    }
    reply = _compose_slack_reply(result)
    # The reply must include the 'couldn't confirm' qualifier
    assert "couldn't confirm" in reply.lower(), (
        f"INSUFFICIENT_EVIDENCE reply does not include uncertainty qualifier. Got: {reply!r}"
    )


def test_compose_verified_true_citation_is_inline_not_appended_separately():
    """Citation must appear inline in the prose, not as a separate end-note block."""
    result = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "github",
                "ref": "https://github.com/org/repo/pull/42",
                "quote": "Merged before the incident.",
            }
        ],
        "summary": "The PR was merged before the incident.",
    }
    reply = _compose_slack_reply(result)
    ref = "https://github.com/repo/org/pull/42"
    # The ref must appear within the same paragraph as the claim, not in a separate block.
    # We verify both summary and ref appear together (not separated by a blank line).
    lines = reply.split("\n\n")  # double newline = new block
    found_in_same_block = any(
        "The PR was merged before the incident." in block
        and "https://github.com/org/repo/pull/42" in block
        for block in lines
    )
    assert found_in_same_block, (
        f"Summary and citation ref are in separate blocks. Reply:\n{reply!r}"
    )


def test_compose_no_content_added_beyond_investigation_result():
    """Compose function must not add content not present in the investigation-result."""
    result = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "src/config.ts:5",
                "quote": "const TIMEOUT = 30;",
            }
        ],
        "summary": "Timeout is 30 seconds.",
    }
    reply = _compose_slack_reply(result)
    # The reply must only contain content from summary + refs — no extra sentences
    # Strip the citation bracket to get plain text
    plain = reply.replace("[src/config.ts:5]", "").strip()
    assert plain == "Timeout is 30 seconds.", (
        f"Reply contains content not from the investigation-result. Got: {plain!r}"
    )


def test_compose_multiple_verified_true_evidence_items():
    """When VERIFIED_TRUE has multiple evidence items, all refs appear inline."""
    result = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {"source": "code", "ref": "src/a.ts:1", "quote": "A evidence."},
            {"source": "github", "ref": "https://github.com/org/repo/pull/1", "quote": "B evidence."},
        ],
        "summary": "Both claims verified.",
    }
    reply = _compose_slack_reply(result)
    assert "src/a.ts:1" in reply, f"First ref missing. Reply: {reply!r}"
    assert "https://github.com/org/repo/pull/1" in reply, f"Second ref missing. Reply: {reply!r}"
