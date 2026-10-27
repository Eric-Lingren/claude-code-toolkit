"""Tests for investigator orchestrator agent (T-0078).

The orchestrator (Opus tier) decomposes a question into sub-claims, routes each
to the correct leaf agent, and aggregates results into a unified investigation-result.
It enforces the INSUFFICIENT_EVIDENCE propagation rule from the contract.
"""

import json
import pathlib
from typing import Optional

import jsonschema
import pytest

DOTFILES = pathlib.Path(__file__).resolve().parents[3]
SHARED = DOTFILES / "claude-code-shared"
SCHEMA_PATH = SHARED / "contracts" / "investigation-result-schema.json"
AGENT_PATH = SHARED / "agents" / "investigate" / "investigator.md"
REGISTRY_PATH = SHARED / "agents" / "registry.json"
INVESTIGATE_SKILL_PATH = SHARED / "skills" / "investigate" / "SKILL.md"

LEAF_AGENTS = [
    "investigator-web",
    "investigator-code",
    "investigator-linear",
    "investigator-github",
    "investigator-notion",
]


@pytest.fixture(scope="module")
def schema():
    assert SCHEMA_PATH.exists(), f"Schema file not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def agent_text():
    assert AGENT_PATH.exists(), f"Agent file not found: {AGENT_PATH}"
    return AGENT_PATH.read_text()


@pytest.fixture(scope="module")
def registry():
    assert REGISTRY_PATH.exists(), f"Registry file not found: {REGISTRY_PATH}"
    return json.loads(REGISTRY_PATH.read_text())


def validate(instance, schema):
    """Raise ValidationError if instance does not conform to schema."""
    jsonschema.validate(instance, schema)


# ---------------------------------------------------------------------------
# Slice 1: Agent file existence and Opus tier
# ---------------------------------------------------------------------------


def test_investigator_orchestrator_agent_exists():
    """agents/investigate/investigator.md must exist in the investigate/ lane directory."""
    assert AGENT_PATH.exists(), (
        f"Missing: {AGENT_PATH}\n"
        "The orchestrator must live at agents/investigate/investigator.md, in the agents/investigate/ lane."
    )


def test_investigator_orchestrator_has_opus_model(agent_text):
    """Orchestrator must be Opus tier — model: opus in frontmatter."""
    assert "model: opus" in agent_text, (
        "Agent missing 'model: opus' frontmatter. "
        "The orchestrator runs at Opus tier per acceptance criteria."
    )


def test_investigator_orchestrator_has_name_field(agent_text):
    """Frontmatter must have a name: investigator field."""
    assert "name: investigator" in agent_text, (
        "Agent missing 'name: investigator' frontmatter field."
    )


# ---------------------------------------------------------------------------
# Slice 2: Routing to leaf agents
# ---------------------------------------------------------------------------


def test_investigator_orchestrator_references_all_leaf_agents(agent_text):
    """Orchestrator must name all five leaf agents for routing."""
    lower = agent_text.lower()
    for leaf in LEAF_AGENTS:
        assert leaf in lower, (
            f"Orchestrator does not reference leaf agent: {leaf}. "
            "All five leaf agents must be listed for routing."
        )


def test_investigator_orchestrator_documents_decomposition(agent_text):
    """Orchestrator must describe decomposing the question into sub-claims."""
    lower = agent_text.lower()
    assert "decompos" in lower or "sub-claim" in lower or "sub_claim" in lower, (
        "Orchestrator must describe decomposing the question into sub-claims."
    )


def test_investigator_orchestrator_documents_routing(agent_text):
    """Orchestrator must describe routing sub-claims to the correct leaf agent."""
    lower = agent_text.lower()
    assert "rout" in lower or "spawn" in lower or "dispatch" in lower, (
        "Orchestrator must describe routing or spawning leaf agents per sub-claim."
    )


def test_investigator_orchestrator_routes_to_at_least_two_sources(agent_text):
    """Orchestrator must describe routing to multiple sources (at least two leaf types)."""
    # Count how many distinct leaf agent names appear in the text
    matched = [leaf for leaf in LEAF_AGENTS if leaf in agent_text.lower()]
    assert len(matched) >= 2, (
        f"Orchestrator only references {len(matched)} leaf agent(s): {matched}. "
        "Must route to at least two leaf agents."
    )


# ---------------------------------------------------------------------------
# Slice 3: INSUFFICIENT_EVIDENCE propagation rule
# ---------------------------------------------------------------------------


def test_investigator_orchestrator_documents_propagation_rule(agent_text):
    """Orchestrator must explicitly document the INSUFFICIENT_EVIDENCE propagation rule."""
    assert "INSUFFICIENT_EVIDENCE" in agent_text, (
        "Orchestrator must explicitly document the INSUFFICIENT_EVIDENCE propagation rule."
    )


def test_investigator_orchestrator_forbids_smoothing_uncertainty(agent_text):
    """Orchestrator must state it will NOT average or smooth over uncertainty."""
    lower = agent_text.lower()
    # The contract uses the phrase "smooth over" or "average" — either must appear
    assert "smooth" in lower or "average" in lower or "false confidence" in lower, (
        "Orchestrator must state that it will not smooth over or average uncertainty. "
        "This is the core of the propagation rule."
    )


def test_investigator_orchestrator_documents_evidence_merging(agent_text):
    """Orchestrator must state that evidence[] arrays from all leaves are merged."""
    lower = agent_text.lower()
    assert "merg" in lower or "aggregat" in lower or "combine" in lower, (
        "Orchestrator must describe merging/aggregating evidence[] from leaf results."
    )


# ---------------------------------------------------------------------------
# Slice 4: Schema-valid output documentation
# ---------------------------------------------------------------------------


def test_investigator_orchestrator_references_investigation_result_contract(agent_text):
    """Orchestrator must reference the investigation-result contract."""
    assert "investigation-result" in agent_text.lower(), (
        "Orchestrator does not reference investigation-result contract."
    )


def test_investigator_orchestrator_emits_summary(agent_text):
    """Orchestrator emits a summary (required by contract for orchestrators)."""
    lower = agent_text.lower()
    assert "summary" in lower, (
        "Orchestrator must document emitting a summary field (required by the contract)."
    )


# ---------------------------------------------------------------------------
# Slice 5: Registry entry
# ---------------------------------------------------------------------------


def test_investigator_registered_in_registry(registry):
    """investigator must appear in agents/registry.json."""
    names = [a["name"] for a in registry["agents"]]
    assert "investigator" in names, (
        f"'investigator' not found in registry.json. Registered agents: {names}"
    )


def test_investigator_registered_as_opus(registry):
    """Registry entry for investigator must specify model: opus."""
    entry = next((a for a in registry["agents"] if a["name"] == "investigator"), None)
    assert entry is not None, "investigator not in registry"
    assert entry.get("model") == "opus", (
        f"Registry entry model is '{entry.get('model')}', expected 'opus'."
    )


def test_investigator_registered_with_correct_consumers(registry):
    """Registry entry must list investigate, pr-revise, and pr-code-review as consumers."""
    entry = next((a for a in registry["agents"] if a["name"] == "investigator"), None)
    assert entry is not None, "investigator not in registry"
    consumers = entry.get("consumers", [])
    for expected in ["investigate", "pr-revise", "pr-code-review"]:
        assert expected in consumers, (
            f"Consumer '{expected}' missing from registry entry consumers: {consumers}"
        )


def test_investigator_registered_file_path(registry):
    """Registry file path must point to agents/investigate/investigator.md."""
    entry = next((a for a in registry["agents"] if a["name"] == "investigator"), None)
    assert entry is not None, "investigator not in registry"
    assert entry.get("file") == "agents/investigate/investigator.md", (
        f"Registry file path is '{entry.get('file')}', expected 'agents/investigate/investigator.md'."
    )


# ---------------------------------------------------------------------------
# Slice 6: investigate skill is no longer a stub
# ---------------------------------------------------------------------------


def test_investigate_skill_exists():
    """skills/investigate/SKILL.md must exist."""
    assert INVESTIGATE_SKILL_PATH.exists(), f"Missing: {INVESTIGATE_SKILL_PATH}"


def test_investigate_skill_is_not_a_stub(agent_text):
    """investigate skill must be functional (not just a stub)."""
    skill_text = INVESTIGATE_SKILL_PATH.read_text()
    assert "not yet functionally implemented" not in skill_text, (
        "investigate SKILL.md is still a stub. It must be updated to spawn the "
        "investigator orchestrator agent."
    )


def test_investigate_skill_references_investigator_agent():
    """investigate skill must reference or spawn the investigator orchestrator."""
    skill_text = INVESTIGATE_SKILL_PATH.read_text()
    lower = skill_text.lower()
    assert "investigator" in lower, (
        "investigate SKILL.md must reference the investigator agent."
    )


# ---------------------------------------------------------------------------
# Integration: simulate decompose -> route -> aggregate flow
# ---------------------------------------------------------------------------


def _aggregate(sub_results: list[dict]) -> dict:
    """
    Reference implementation of the orchestrator's aggregation logic.

    Rules (mirrors what the investigator.md agent documents):
    1. Merge all evidence[] arrays from leaf results.
    2. If ANY leaf returned INSUFFICIENT_EVIDENCE, the aggregate verdict is
       INSUFFICIENT_EVIDENCE — never smoothed over.
    3. If ALL leaves returned VERIFIED_TRUE, aggregate is VERIFIED_TRUE.
    4. If ANY leaf returned VERIFIED_FALSE (and none INSUFFICIENT), aggregate is VERIFIED_FALSE.
    5. If results are mixed TRUE/FALSE/CONTESTED, aggregate is CONTESTED.
    6. summary is required: explicitly names which sub-claim(s) caused uncertainty.
    """
    all_evidence = []
    for r in sub_results:
        all_evidence.extend(r.get("evidence", []))

    verdicts = [r["verdict"] for r in sub_results]

    # Propagation rule: any INSUFFICIENT_EVIDENCE wins
    if "INSUFFICIENT_EVIDENCE" in verdicts:
        insufficient_claims = [
            r.get("sub_claim", f"sub-claim {i+1}")
            for i, r in enumerate(sub_results)
            if r["verdict"] == "INSUFFICIENT_EVIDENCE"
        ]
        summary = (
            f"Sub-claim(s) could not be confirmed: {', '.join(insufficient_claims)}. "
            "The overall verdict cannot be VERIFIED_TRUE or VERIFIED_FALSE without this information."
        )
        return {
            "schema_version": "1",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "evidence": all_evidence,
            "summary": summary,
        }

    if all(v == "VERIFIED_TRUE" for v in verdicts):
        aggregate_verdict = "VERIFIED_TRUE"
    elif "VERIFIED_FALSE" in verdicts:
        aggregate_verdict = "VERIFIED_FALSE"
    else:
        aggregate_verdict = "CONTESTED"

    return {
        "schema_version": "1",
        "verdict": aggregate_verdict,
        "evidence": all_evidence,
        "summary": f"All {len(sub_results)} sub-claims resolved: {', '.join(verdicts)}.",
    }


def test_integration_all_verified_true_aggregates_to_verified_true(schema):
    """When all sub-claims resolve VERIFIED_TRUE, aggregate is VERIFIED_TRUE."""
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "The function exists in the codebase.",
            "evidence": [{"source": "code", "ref": "src/foo.py:10", "quote": "def my_func():"}],
        },
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "The PR that introduced it was merged.",
            "evidence": [
                {
                    "source": "github",
                    "ref": "https://github.com/org/repo/pull/42",
                    "quote": "Merged on 2026-07-01.",
                }
            ],
        },
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    assert result["verdict"] == "VERIFIED_TRUE"
    assert len(result["evidence"]) == 2  # merged from both leaves


def test_integration_one_insufficient_propagates_to_aggregate(schema):
    """
    CRITICAL: if any leaf returns INSUFFICIENT_EVIDENCE, the aggregate must be
    INSUFFICIENT_EVIDENCE — even if other leaves returned VERIFIED_TRUE.
    This is the core propagation rule.
    """
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "The feature flag exists in code.",
            "evidence": [
                {
                    "source": "code",
                    "ref": "src/flags.ts:5",
                    "quote": "const FEATURE_X = true;",
                }
            ],
        },
        {
            "schema_version": "1",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "sub_claim": "The flag was enabled before the incident.",
            "evidence": [],
        },
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    # The propagation rule: INSUFFICIENT_EVIDENCE wins
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE", (
        f"Expected INSUFFICIENT_EVIDENCE but got {result['verdict']}. "
        "The propagation rule must not be smoothed over."
    )
    # Summary must name the unresolved sub-claim
    assert "flag was enabled before the incident" in result["summary"], (
        f"Summary does not name the unresolved sub-claim. Got: {result['summary']}"
    )


def test_integration_aggregate_evidence_merges_all_leaves(schema):
    """Aggregate evidence[] must include evidence from all leaf results."""
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "Web claim.",
            "evidence": [
                {"source": "web", "ref": "https://example.com/a", "quote": "Web evidence A."}
            ],
        },
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "Code claim.",
            "evidence": [
                {"source": "code", "ref": "src/a.py:1", "quote": "code evidence A"}
            ],
        },
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "Linear claim.",
            "evidence": [
                {
                    "source": "linear",
                    "ref": "https://linear.app/team/issue/ENG-1",
                    "quote": "Linear evidence A.",
                }
            ],
        },
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    assert len(result["evidence"]) == 3, (
        f"Expected 3 merged evidence items, got {len(result['evidence'])}."
    )
    sources = {e["source"] for e in result["evidence"]}
    assert sources == {"web", "code", "linear"}


def test_integration_aggregate_result_has_required_summary(schema):
    """The orchestrator aggregate result must include a summary (required by contract)."""
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "Some claim.",
            "evidence": [
                {"source": "web", "ref": "https://example.com/b", "quote": "Evidence B."}
            ],
        }
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    assert "summary" in result and result["summary"], (
        "Aggregate result must include a non-empty summary."
    )


def test_integration_two_insufficient_both_named_in_summary(schema):
    """When two sub-claims are INSUFFICIENT, both must be named in the summary."""
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "sub_claim": "Deployment timestamp claim.",
            "evidence": [],
        },
        {
            "schema_version": "1",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "sub_claim": "Feature flag state claim.",
            "evidence": [],
        },
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "Deployment timestamp claim" in result["summary"], (
        "First unresolved sub-claim not named in summary."
    )
    assert "Feature flag state claim" in result["summary"], (
        "Second unresolved sub-claim not named in summary."
    )


def test_integration_aggregate_output_schema_valid_all_verdicts(schema):
    """Aggregate outputs for every verdict type must pass schema validation."""
    for verdict, evidence, has_sub_claims in [
        (
            "VERIFIED_TRUE",
            [{"source": "web", "ref": "https://example.com/c", "quote": "C."}],
            True,
        ),
        (
            "VERIFIED_FALSE",
            [{"source": "code", "ref": "src/b.py:2", "quote": "False evidence."}],
            True,
        ),
        ("INSUFFICIENT_EVIDENCE", [], False),
        (
            "CONTESTED",
            [
                {
                    "source": "linear",
                    "ref": "https://linear.app/team/issue/ENG-2",
                    "quote": "Contested evidence.",
                }
            ],
            True,
        ),
    ]:
        doc = {
            "schema_version": "1",
            "verdict": verdict,
            "evidence": evidence,
            "summary": f"Test summary for {verdict}.",
        }
        validate(doc, schema)  # Must not raise


def test_integration_verified_false_wins_over_true_no_insufficient(schema):
    """
    When no INSUFFICIENT_EVIDENCE exists but one leaf returns VERIFIED_FALSE,
    aggregate should reflect VERIFIED_FALSE (contradiction found).
    """
    sub_results = [
        {
            "schema_version": "1",
            "verdict": "VERIFIED_TRUE",
            "sub_claim": "Function exists.",
            "evidence": [{"source": "code", "ref": "src/c.py:3", "quote": "def exists():"}],
        },
        {
            "schema_version": "1",
            "verdict": "VERIFIED_FALSE",
            "sub_claim": "The PR was merged before the bug.",
            "evidence": [
                {
                    "source": "github",
                    "ref": "https://github.com/org/repo/pull/99",
                    "quote": "Merged 2026-08-01, after the incident on 2026-07-30.",
                }
            ],
        },
    ]
    result = _aggregate(sub_results)
    validate(result, schema)
    assert result["verdict"] == "VERIFIED_FALSE"


# ---------------------------------------------------------------------------
# Slice 7: Bounded expansion — adjacent material facts (T-0098)
# ---------------------------------------------------------------------------


def test_investigator_orchestrator_documents_bounded_expansion(agent_text):
    """Orchestrator must describe the bounded expansion step."""
    lower = agent_text.lower()
    assert "bounded expansion" in lower, (
        "Orchestrator missing 'bounded expansion' instruction. "
        "Step 4 must define this always-on behavior."
    )


def test_investigator_orchestrator_documents_adjacent_facts(agent_text):
    """Orchestrator must reference adjacent_facts as the output field."""
    assert "adjacent_facts" in agent_text, (
        "Orchestrator must document the adjacent_facts output field."
    )


def test_investigator_orchestrator_documents_relevance_gate(agent_text):
    """Orchestrator must describe the relevance-gate for adjacent facts."""
    lower = agent_text.lower()
    assert "relevance-gate" in lower or "relevance gate" in lower, (
        "Orchestrator must document relevance-gating for adjacent fact candidates."
    )


def test_investigator_orchestrator_documents_evidence_gate_drop(agent_text):
    """Orchestrator must state that facts with no evidence are dropped."""
    lower = agent_text.lower()
    assert "silently drop" in lower or "evidence gate" in lower or "drop" in lower, (
        "Orchestrator must state that adjacent facts with no evidence are dropped."
    )


def test_investigator_orchestrator_documents_blob_url_for_adjacent(agent_text):
    """Orchestrator must require GitHub blob URLs for adjacent fact code refs."""
    # blob-URL pattern must appear in the bounded-expansion section
    assert "github.com" in agent_text and "blob" in agent_text and "#L" in agent_text, (
        "Orchestrator must document GitHub blob URL construction for adjacent fact code refs."
    )


def test_investigator_orchestrator_expansion_always_on(agent_text):
    """Orchestrator must state bounded expansion applies to all callers (always-on)."""
    lower = agent_text.lower()
    assert "always-on" in lower or "all callers" in lower, (
        "Orchestrator must state bounded expansion is always-on for all callers."
    )


def test_schema_has_adjacent_facts_field(schema):
    """investigation-result-schema.json must include an adjacent_facts property."""
    assert "adjacent_facts" in schema.get("properties", {}), (
        "Schema missing 'adjacent_facts' property. "
        "The bounded expansion output must be defined in the schema."
    )


def test_schema_adjacent_facts_is_array(schema):
    """adjacent_facts must be typed as an array."""
    af = schema["properties"].get("adjacent_facts", {})
    assert af.get("type") == "array", (
        f"adjacent_facts.type is '{af.get('type')}', expected 'array'."
    )


def test_schema_adjacent_facts_items_have_required_fields(schema):
    """adjacent_facts items must require claim, verdict, and evidence."""
    af = schema["properties"]["adjacent_facts"]
    items = af.get("items", {})
    required = items.get("required", [])
    for field in ["claim", "verdict", "evidence"]:
        assert field in required, (
            f"adjacent_facts items missing required field '{field}'. Got: {required}"
        )


def test_schema_backward_compat_without_adjacent_facts(schema):
    """A result without adjacent_facts must still validate (field is optional)."""
    import jsonschema as _jschema
    doc = {
        "schema_version": "1",
        "verdict": "INSUFFICIENT_EVIDENCE",
        "evidence": [],
        "summary": "Nothing found.",
    }
    _jschema.validate(doc, schema)  # Must not raise


def test_schema_expansion_result_with_adjacent_facts_validates(schema):
    """A result with a populated adjacent_facts array must validate against the schema."""
    import jsonschema as _jschema
    doc = {
        "schema_version": "1",
        "verdict": "VERIFIED_TRUE",
        "evidence": [
            {
                "source": "code",
                "ref": "https://github.com/org/repo/blob/main/src/auth/middleware.ts#L88",
                "quote": "export const HOOK_ENABLED = true;",
            }
        ],
        "summary": "The hook is enabled.",
        "adjacent_facts": [
            {
                "claim": "Tests cover the hook enabled path.",
                "verdict": "VERIFIED_TRUE",
                "evidence": [
                    {
                        "source": "code",
                        "ref": "https://github.com/org/repo/blob/main/src/auth/middleware.test.ts#L22",
                        "quote": "it('enables hook when HOOK_ENABLED is true', ...)",
                    }
                ],
                "summary": "A test asserts the enabled path.",
            }
        ],
    }
    _jschema.validate(doc, schema)  # Must not raise


def test_adjacent_facts_evidence_gate_drops_insufficient():
    """
    Adjacent facts with INSUFFICIENT_EVIDENCE should not appear in the output.
    This test documents the expected filtering logic as a reference implementation.
    """
    candidate_results = [
        {
            "claim": "Tests cover the hook.",
            "verdict": "VERIFIED_TRUE",
            "evidence": [
                {
                    "source": "code",
                    "ref": "https://github.com/org/repo/blob/main/src/auth.test.ts#L10",
                    "quote": "it('hooks correctly', ...)",
                }
            ],
        },
        {
            "claim": "The hook was added in the last 30 days.",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "evidence": [],
        },
        {
            "claim": "The hook is consumed by the router.",
            "verdict": "VERIFIED_TRUE",
            "evidence": [
                {
                    "source": "code",
                    "ref": "https://github.com/org/repo/blob/main/src/router.ts#L5",
                    "quote": "if (HOOK_ENABLED) { applyHook(); }",
                }
            ],
        },
    ]

    # Reference implementation of evidence gate: drop INSUFFICIENT_EVIDENCE items
    gated = [
        r for r in candidate_results
        if r["verdict"] in ("VERIFIED_TRUE", "VERIFIED_FALSE") and r["evidence"]
    ]

    assert len(gated) == 2, (
        f"Expected 2 adjacent facts after gating, got {len(gated)}. "
        "INSUFFICIENT_EVIDENCE must be silently dropped."
    )
    claims_kept = {r["claim"] for r in gated}
    assert "The hook was added in the last 30 days." not in claims_kept, (
        "INSUFFICIENT_EVIDENCE fact must not survive the evidence gate."
    )
