"""The draft record shapes in the learning agent docs validate against
learning-schema.json when escape_point is present."""

import json
import pathlib
import re
import uuid

import pytest

jsonschema = pytest.importorskip("jsonschema")

SHARED = pathlib.Path(__file__).resolve().parents[3] / "claude-code-shared"
SCHEMA = json.loads((SHARED / "contracts" / "learning-schema.json").read_text())
AGENTS = SHARED / "agents" / "learning"

SERVER = {
    "schema_version": "2",
    "id": str(uuid.uuid4()),
    "timestamp": "2026-11-03T00:00:00Z",
}

# Placeholder values in the docs ("<...>", "a|b") swapped for valid ones.
OVERRIDES = {
    "improves": "to-tasks",
    "cause": "requirement_lost_between_docs",
    "cause_label": None,
    "confidence": "confirmed",
    "trace": {},
}


def _record_block(doc_name, marker):
    text = (AGENTS / doc_name).read_text()
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        if marker in block:
            return json.loads(block)
    pytest.fail(f"no json block containing {marker!r} in {doc_name}")


def _concretize(draft):
    record = dict(draft)
    for key, value in OVERRIDES.items():
        if key in record:
            record[key] = value
    return {**SERVER, **record}


@pytest.mark.parametrize(
    "doc,marker",
    [
        ("attribution-tracer.md", '"type": "attribution"'),
        ("capture-learning.md", '"type": "self"'),
    ],
)
class TestDraftShape:
    def test_draft_has_escape_point(self, doc, marker):
        draft = _record_block(doc, marker)
        assert "escape_point" in draft

    def test_draft_with_escape_point_validates(self, doc, marker):
        record = _concretize(_record_block(doc, marker))
        jsonschema.Draft7Validator(SCHEMA).validate(record)

    def test_draft_with_null_section_validates(self, doc, marker):
        record = _concretize(_record_block(doc, marker))
        record["escape_point"] = {"path": record["escape_point"]["path"], "section": None}
        jsonschema.Draft7Validator(SCHEMA).validate(record)
