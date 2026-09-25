"""Tests for gcp/_raw_fields.py — the cross-driver raw-field passthrough guard."""

from __future__ import annotations

from gcp._raw_fields import raw_field_conflicts

PROTECTED = {"apiConfig", "managedService", "labels"}


def test_proto_spelled_name_is_flagged_even_when_not_itself_protected() -> None:
    """A proto-spelled name is refused outright: it is never a valid raw
    field name, whether or not its camelCase counterpart happens to be in the
    protected set (#1981)."""
    proto_names, forbidden = raw_field_conflicts({"api_config": "x"}, PROTECTED)
    assert proto_names == ["api_config"]
    assert forbidden == []


def test_camelcase_protected_name_is_forbidden() -> None:
    proto_names, forbidden = raw_field_conflicts({"apiConfig": "x"}, PROTECTED)
    assert proto_names == []
    assert forbidden == ["apiConfig"]


def test_protected_match_is_case_blind() -> None:
    proto_names, forbidden = raw_field_conflicts({"ApiConfig": "x", "APICONFIG": "y"}, PROTECTED)
    assert proto_names == []
    assert forbidden == ["APICONFIG", "ApiConfig"]


def test_unrelated_camelcase_name_passes() -> None:
    proto_names, forbidden = raw_field_conflicts({"futureKnob": 3}, PROTECTED)
    assert proto_names == []
    assert forbidden == []


def test_accepts_a_list_as_well_as_a_dict() -> None:
    """clear_fields is a list of names, not a dict of name/value pairs."""
    proto_names, forbidden = raw_field_conflicts(["labels", "future_knob"], PROTECTED)
    assert proto_names == ["future_knob"]
    assert forbidden == ["labels"]


def test_empty_input_conflicts_with_nothing() -> None:
    assert raw_field_conflicts({}, PROTECTED) == ([], [])
    assert raw_field_conflicts(None, PROTECTED) == ([], [])


def test_results_are_sorted_and_deduplicated() -> None:
    proto_names, forbidden = raw_field_conflicts(
        ["z_field", "a_field", "z_field", "labels", "labels"],
        PROTECTED,
    )
    assert proto_names == ["a_field", "z_field"]
    assert forbidden == ["labels"]
