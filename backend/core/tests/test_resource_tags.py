"""Portability rule for operator resource tags (#1505)."""

from __future__ import annotations

import pytest

from core.resource_tags import MAX_TAGS, ResourceTagError, validate_resource_tags


def test_a_portable_tag_is_returned_unchanged():
    tags = {"cost_center": "platform-rnd", "owner": "infra"}
    assert validate_resource_tags(tags) == tags


def test_an_empty_value_is_allowed():
    # GCP labels admit an empty value, and an operator setting a key with
    # nothing in it yet is a normal intermediate state.
    assert validate_resource_tags({"cost_center": ""}) == {"cost_center": ""}


@pytest.mark.parametrize(
    ("key", "why"),
    [
        ("Cost_Center", "uppercase is legal on AWS and Azure, not in a GCP label"),
        ("1cost", "GCP labels must start with a letter"),
        ("cost.center", "'.' is legal on AWS, not in a GCP label"),
        ("cost center", "spaces are legal on AWS, not in a GCP label"),
        ("cost/center", "'/' is legal on AWS, refused by Azure"),
        ("c" * 64, "GCP caps a label key at 63"),
        ("", "an empty key is a key nothing can carry"),
    ],
)
def test_a_key_no_cloud_trio_accepts_is_refused(key, why):
    with pytest.raises(ResourceTagError) as exc:
        validate_resource_tags({key: "x"})
    assert exc.value.key == key, why


@pytest.mark.parametrize(
    "value",
    ["Platform R&D", "team@example.com", "a" * 64, "has space"],
)
def test_a_value_no_cloud_trio_accepts_is_refused(value):
    # These are the ones that bite: "Platform R&D" is exactly what an
    # operator types for a cost centre, and it cannot be a GCP label.
    # Refusing it in front of them beats rewriting it into something their
    # billing export will not match.
    with pytest.raises(ResourceTagError) as exc:
        validate_resource_tags({"cost_center": value})
    assert exc.value.key == "cost_center"


def test_non_string_values_are_refused():
    with pytest.raises(ResourceTagError):
        validate_resource_tags({"count": 3})


def test_a_non_mapping_is_refused():
    with pytest.raises(ResourceTagError):
        validate_resource_tags([("cost_center", "x")])


def test_the_tag_budget_is_bounded():
    ok = {f"k{n}": "v" for n in range(MAX_TAGS)}
    assert validate_resource_tags(ok) == ok

    too_many = {f"k{n}": "v" for n in range(MAX_TAGS + 1)}
    with pytest.raises(ResourceTagError, match="at most"):
        validate_resource_tags(too_many)


def test_the_error_names_the_offending_key():
    # An operator with a dozen tags needs to be told which one, not that
    # "a tag" is wrong.
    with pytest.raises(ResourceTagError) as exc:
        validate_resource_tags({"good": "v", "Bad Key": "v"})
    assert exc.value.key == "Bad Key"
    assert "Bad Key" in str(exc.value)
