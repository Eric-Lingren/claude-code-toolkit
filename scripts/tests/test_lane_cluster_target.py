import importlib.util
import json
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "learnings-lane"
spec = importlib.util.spec_from_file_location("cluster_target", d / "cluster_target.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

ROWS = [{"id": "a", "improves": "t"}, {"id": "b", "improves": "t"}]
GOOD = {"clusters": [{"id": "c1", "target": "t", "learning_ids": ["a", "b"],
                      "root_cause": "rc", "fix": "fx"}]}


def stub(outputs):
    calls = []

    def agent(req):
        calls.append(req)
        return outputs[min(len(calls), len(outputs)) - 1]
    agent.calls = calls
    return agent


def test_valid_returned_unchanged(tmp_path):
    ag = stub([GOOD])
    ev = tmp_path / "ev.jsonl"
    assert ct.cluster_target("t", ROWS, ag, ev) == GOOD
    assert len(ag.calls) == 1 and not ev.exists()


def test_invalid_retries_once_then_skips_and_logs(tmp_path):
    bad = {"clusters": [{"id": "c1", "target": "t", "learning_ids": ["a"],
                         "root_cause": "rc", "fix": "fx"}]}
    ag = stub([bad])
    ev = tmp_path / "ev.jsonl"
    assert ct.cluster_target("t", ROWS, ag, ev) is None
    assert len(ag.calls) == 2
    assert ag.calls[1]["errors"]
    rec = json.loads(ev.read_text().splitlines()[0])
    assert rec["event"] == "cluster_skipped" and rec["target"] == "t"


def test_retry_can_recover(tmp_path):
    ag = stub([{"clusters": []}, GOOD])
    assert ct.cluster_target("t", ROWS, ag, tmp_path / "ev.jsonl") == GOOD
    assert len(ag.calls) == 2


def test_escape_point_rejected(tmp_path):
    c = dict(GOOD["clusters"][0], escape_point={"path": "x"})
    ag = stub([{"clusters": [c]}])
    ev = tmp_path / "ev.jsonl"
    assert ct.cluster_target("t", ROWS, ag, ev) is None
    assert "escape_point" in ev.read_text()


def test_invalid_request_blocks_before_any_agent_call(tmp_path):
    ag = stub([GOOD])
    import pytest
    with pytest.raises(ValueError, match="cluster-contract.json"):
        ct.cluster_target("", ROWS, ag, tmp_path / "ev.jsonl")
    with pytest.raises(ValueError, match="cluster-contract.json"):
        ct.cluster_target("t", [{"id": "a"}], ag, tmp_path / "ev.jsonl")
    assert ag.calls == []


def test_wrong_target_in_output_is_rejected(tmp_path):
    bad = {"clusters": [dict(GOOD["clusters"][0], target="other")]}
    ev = tmp_path / "ev.jsonl"
    ag = stub([bad, GOOD])
    assert ct.cluster_target("t", ROWS, ag, ev) == GOOD
    assert "must equal request target" in json.dumps(ag.calls[1]["errors"])
