from uuid import UUID

import pytest

from _sdk.physical_naming import physical_name, recorded_resource_name

FIRST = "00000000-0000-4000-8000-000000000001"
SECOND = "00000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize("limit", [22, 24, 30, 33, 34, 63, 98])
@pytest.mark.parametrize("separator", ["-", "_", ""])
def test_identity_survives_cosmetic_prefix_truncation(limit, separator):
    first = physical_name(FIRST, prefix="1" + "cosmetic-prefix-" * 30, max_length=limit, separator=separator)
    second = physical_name(SECOND, prefix="1" + "cosmetic-prefix-" * 30, max_length=limit, separator=separator)
    assert first != second
    assert len(first) <= limit and first[0].isalpha()
    assert first == physical_name(FIRST, prefix="1" + "cosmetic-prefix-" * 30, max_length=limit, separator=separator)
    if limit >= 33 + len(separator):
        assert first.endswith(UUID(FIRST).hex)


@pytest.mark.parametrize(
    "identity", ["", "owner-guid", None, "00000000-0000-0000-0000-000000000000", FIRST.upper().replace("4000", "ABCD")]
)
def test_new_name_requires_actual_canonical_identity(identity):
    with pytest.raises(ValueError, match="identity"):
        physical_name(identity, prefix="pg", max_length=63)


def test_simple_recorded_handle_preserves_legacy_name_without_granting_ownership():
    assert recorded_resource_name("postgres/old-human-name", kind="postgres") == "old-human-name"
    assert recorded_resource_name("", kind="postgres") is None
    for handle in ["mysql/old-human-name", "postgres/", "postgres/project/instance", "postgres/../../other"]:
        with pytest.raises(ValueError):
            recorded_resource_name(handle, kind="postgres")
