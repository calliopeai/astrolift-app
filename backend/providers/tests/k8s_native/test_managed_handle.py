"""Tests for the managed-service handle encoder (#366)."""

from __future__ import annotations

import pytest

from k8s_native.managed._handle import ParsedHandle, pack, unpack


def test_pack_round_trip() -> None:
    h = pack(
        kind="postgres",
        cluster_id="tenant-1",
        namespace="acme-api",
        name="api-prod",
    )
    assert h == "postgres/tenant-1/acme-api/api-prod"
    parsed = unpack(h)
    assert parsed == ParsedHandle(
        kind="postgres",
        cluster_id="tenant-1",
        namespace="acme-api",
        name="api-prod",
    )
    assert parsed.is_legacy is False


def test_unpack_legacy_two_segment() -> None:
    """Pre-#366 handles round-trip as ``is_legacy=True`` so callers
    can refuse to deprovision them rather than guessing locator."""
    parsed = unpack("postgres/api-prod")
    assert parsed.kind == "postgres"
    assert parsed.name == "api-prod"
    assert parsed.cluster_id == ""
    assert parsed.namespace == ""
    assert parsed.is_legacy is True


def test_pack_rejects_empty_locator() -> None:
    with pytest.raises(ValueError, match="non-empty cluster_id"):
        pack(
            kind="postgres",
            cluster_id="",
            namespace="acme-api",
            name="api-prod",
        )
    with pytest.raises(ValueError, match="non-empty cluster_id"):
        pack(
            kind="postgres",
            cluster_id="tenant-1",
            namespace="",
            name="api-prod",
        )


def test_pack_rejects_slash_in_segment() -> None:
    with pytest.raises(ValueError, match="may not contain"):
        pack(
            kind="postgres",
            cluster_id="tenant/1",
            namespace="acme-api",
            name="api-prod",
        )


@pytest.mark.parametrize(
    "bad_handle",
    [
        "",
        "postgres",
        "postgres/a/b",
        "postgres/a/b/c/d",
    ],
)
def test_unpack_rejects_other_arities(bad_handle: str) -> None:
    with pytest.raises(ValueError, match="must be 2-segment"):
        unpack(bad_handle)
