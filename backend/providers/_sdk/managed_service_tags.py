"""The one key cost attribution groups on, per cloud (#1419).

Cost attribution groups billing data by a single tag or label key. A driver
that spells that key differently is invisible to the group-by, which is the
same failure as not tagging at all, only partial and therefore quieter: a share
of every bill lands in "Shared / untagged" and nothing reports an error.

The spellings drifted because `core/cloud_tags.py` exists to be the single
source and has no production consumers. Every driver hand-rolled its own dict.
This module is the narrow version of that fix, scoped to the one key that
attribution actually depends on, and importable from the providers subtree,
which cannot reach `core`.

Two rules:

*Write* the canonical key. It is what the billing client groups on and what
`core.cloud_tags` serializers produce for the same field.

*Read* the canonical key and every legacy spelling. Resources provisioned
before a driver was migrated carry the old label, and ownership checks read the
same key they once wrote. Dropping the legacy spellings from the read path
would make the platform stop recognising resources it created, which is a worse
failure than partial attribution: it orphans them.

The legacy lists are deliberately explicit rather than a fuzzy match. A new
undeclared spelling should fail the guard test, not be quietly tolerated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Canonical keys. These match what ``core.cloud_tags.to_aws`` / ``to_gcp`` /
#: ``to_azure`` emit for ``managed_service_id``, and what the per-cloud billing
#: clients group on. A guard test pins all three together.
CANONICAL_KEYS: dict[str, str] = {
    "aws": "astrolift.io/managed_service_id",
    "gcp": "astrolift_io_managed_service_id",
    "azure": "astrolift-managed-service-id",
}

#: Spellings found in shipped drivers that predate the canonical key. Read,
#: never written. Removing one silently orphans any resource still carrying it,
#: so an entry leaves this list only when nothing in the estate can still have
#: it, which in practice means never for a released variant.
LEGACY_KEYS: dict[str, tuple[str, ...]] = {
    "aws": (),
    "gcp": (
        "astrolift-io-managed-service-id",
        "astrolift-managed-service-id",
        "x-astrolift-managed-service-id",
    ),
    "azure": (
        "astrolift.io/managed_service_id",
        "astrolift_io_managed_service_id",
        "astrolift_managed_service_id",
    ),
}


#: Convenience alias for the GCP label key, which is what a driver writing a
#: labels dict needs at the call site. Named for the cloud it belongs to so a
#: driver cannot reach for the wrong one by autocomplete.
MANAGED_SERVICE_ID_LABEL = CANONICAL_KEYS["gcp"]
MANAGED_SERVICE_ID_TAG_AWS = CANONICAL_KEYS["aws"]
MANAGED_SERVICE_ID_TAG_AZURE = CANONICAL_KEYS["azure"]


class UnknownCloud(KeyError):
    """A cloud with no declared managed-service-id key."""


def canonical_key(cloud: str) -> str:
    """The key a driver must write, and the billing client groups on."""
    try:
        return CANONICAL_KEYS[cloud]
    except KeyError:
        raise UnknownCloud(
            f"no canonical managed-service-id key for cloud {cloud!r}; "
            f"declare one in providers/_sdk/managed_service_tags.py before "
            f"tagging anything, or its cost will never be attributed"
        ) from None


def readable_keys(cloud: str) -> tuple[str, ...]:
    """Every key an ownership read should accept, canonical first."""
    return (canonical_key(cloud), *LEGACY_KEYS.get(cloud, ()))


def read_managed_service_id(tags: Mapping[str, str] | None, cloud: str) -> str:
    """Recover the managed-service id from a resource's tags or labels.

    Canonical first, then each legacy spelling. Returns an empty string when
    none is present, which callers read as "not one of ours" and must not
    confuse with a resource whose id happens to be empty.
    """
    if not tags:
        return ""
    for key in readable_keys(cloud):
        value = tags.get(key)
        if value:
            return str(value)
    return ""


def is_owned_by(tags: Mapping[str, str] | None, managed_service_id: str, cloud: str) -> bool:
    """Whether this resource carries our id, under any spelling we ever wrote.

    Fails closed on an empty ``managed_service_id``: an unowned resource and a
    caller that forgot to pass an id look identical to a naive equality check,
    and the consequence of getting it wrong is deleting somebody else's
    resource.
    """
    if not managed_service_id:
        return False
    return read_managed_service_id(tags, cloud) == managed_service_id
