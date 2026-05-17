"""Tests for the form schema / payload validation helpers (#453).

Covers the boundary checks in
``astrolift_forms.schema.mutations._validate_schema`` and
``_validate_payload_against_schema``.

We don't reach for a full JSON-Schema validator on purpose — the FE
builder enforces field shape, and validating every JSON-Schema rule
on every submit would block adding new widget kinds without a backend
deploy. The tests pin the minimum shape we actually rely on at the
boundary so the renderer can trust what it loads.
"""

from __future__ import annotations

import pytest

from astrolift_forms.schema.mutations import (
    _validate_payload_against_schema,
    _validate_schema,
)

# ---- _validate_schema -----------------------------------------------


def test_schema_must_be_object():
    assert _validate_schema("nope") == "schema must be a JSON object"
    assert _validate_schema(123) == "schema must be a JSON object"
    assert _validate_schema([1, 2, 3]) == "schema must be a JSON object"


def test_schema_properties_must_be_object_when_present():
    assert _validate_schema({"type": "object", "properties": []}) == ("schema.properties must be an object")
    assert _validate_schema({"properties": "blah"}) == "schema.properties must be an object"


def test_schema_required_must_be_list_when_present():
    assert _validate_schema({"required": "name"}) == "schema.required must be a list"


def test_minimal_valid_schemas_pass():
    assert _validate_schema({}) is None
    assert _validate_schema({"type": "object"}) is None
    assert _validate_schema({"properties": {"x": {}}, "required": ["x"]}) is None


# ---- _validate_payload_against_schema --------------------------------


def _schema(*required, properties=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
    }


def test_payload_must_be_object():
    ok, msg, field = _validate_payload_against_schema("nope", _schema())
    assert not ok
    assert "payload must be a JSON object" in msg
    assert field is None


def test_missing_required_field_reported():
    ok, msg, field = _validate_payload_against_schema({}, _schema("name"))
    assert not ok
    assert "missing required field: name" in msg
    assert field == "name"


def test_empty_string_in_required_field_treated_as_missing():
    ok, msg, field = _validate_payload_against_schema({"name": ""}, _schema("name"))
    assert not ok
    assert field == "name"


def test_null_in_required_field_treated_as_missing():
    ok, msg, field = _validate_payload_against_schema({"name": None}, _schema("name"))
    assert not ok
    assert field == "name"


def test_zero_in_required_field_is_an_explicit_answer():
    """``0`` and ``False`` are real submitter answers, not missing values."""
    schema = _schema("score", properties={"score": {"type": "number"}})
    ok, _, _ = _validate_payload_against_schema({"score": 0}, schema)
    assert ok


def test_false_in_required_field_is_an_explicit_answer():
    schema = _schema("agreed", properties={"agreed": {"type": "boolean"}})
    ok, _, _ = _validate_payload_against_schema({"agreed": False}, schema)
    assert ok


def test_empty_list_in_required_array_field_is_missing():
    schema = _schema(
        "tags",
        properties={"tags": {"type": "array", "items": {"type": "string"}}},
    )
    ok, msg, field = _validate_payload_against_schema({"tags": []}, schema)
    assert not ok
    assert field == "tags"


def test_non_empty_list_in_required_array_field_passes():
    schema = _schema(
        "tags",
        properties={"tags": {"type": "array", "items": {"type": "string"}}},
    )
    ok, _, _ = _validate_payload_against_schema({"tags": ["alpha"]}, schema)
    assert ok


def test_required_can_be_missing_when_schema_has_no_requirements():
    ok, _, _ = _validate_payload_against_schema({}, _schema())
    assert ok


def test_unknown_required_entries_are_ignored():
    """A non-string in ``required`` shouldn't crash the validator —
    it just gets skipped. Keeps the boundary resilient to garbage
    that snuck past the FE builder."""
    schema = {"properties": {}, "required": [None, 42, "name"]}
    ok, _, field = _validate_payload_against_schema({"name": "x"}, schema)
    assert ok
    ok, _, field = _validate_payload_against_schema({}, schema)
    assert not ok
    assert field == "name"


@pytest.mark.parametrize(
    "schema",
    [
        # required not a list → treat as no requirements, payload accepted.
        {"properties": {}, "required": "name"},
    ],
)
def test_required_not_a_list_is_tolerant(schema):
    ok, _, _ = _validate_payload_against_schema({}, schema)
    assert ok
