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

``OWNERSHIP_KEYS`` extends the same rules to the rest of the ownership envelope
(managed-by, binding, org, app, env, cluster, isolation). Those keys carry no
billing weight, but a driver that reads back a spelling it never wrote decides
it does not own its own resource, which fails every reconcile after the first
create (#1431).
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
    # ``astrolift.io/managed_service_id`` was written by five Azure drivers but
    # is not listed: ARM rejects ``/`` in a tag name, so it never reached a
    # resource and reading it would only mask the bug (#1431). Both spellings
    # below are blob-container and file-share metadata, which does land.
    "azure": (
        "astrolift_io_managed_service_id",
        "astrolift_managed_service_id",
    ),
}


#: The rest of the ownership envelope, per cloud. Drivers read these back to
#: decide whether a name collision is a resource they already own, and must read
#: the string the write path actually produced. On Azure that is what
#: ``azure/managed/tags.py::arm_tags_for`` emits after ARM serialization.
#:
#: There is no legacy ledger here, unlike the id above. The only other spelling
#: ever written was the pre-serialization ``astrolift.io/<name>`` form, and ARM
#: rejects ``/`` in a tag name, so no live resource can carry it (#1431). A
#: spelling that does reach resources needs a ledger shaped like ``LEGACY_KEYS``.
OWNERSHIP_KEYS: dict[str, dict[str, str]] = {
    "azure": {
        "managed_by": "astrolift-managed-by",
        "binding": "astrolift-binding",
        "org": "astrolift-org",
        "app": "astrolift-app",
        "env": "astrolift-env",
        "cluster": "astrolift-cluster",
        "isolation": "astrolift-isolation",
        # Adoption markers (#1365). Written only by the authorized adoption
        # operation, never by a driver's provision path, and read by teardown
        # guards that treat an adopted resource as more dangerous to delete
        # than one the platform created. Declared here so the two spellings
        # have one owner rather than living as a literal in the one driver
        # that first needed them.
        "adopted": "astrolift-adopted",
        "adopted_at": "astrolift-adopted-at",
    },
}


#: Convenience alias for the GCP label key, which is what a driver writing a
#: labels dict needs at the call site. Named for the cloud it belongs to so a
#: driver cannot reach for the wrong one by autocomplete.
MANAGED_SERVICE_ID_LABEL = CANONICAL_KEYS["gcp"]
MANAGED_SERVICE_ID_TAG_AWS = CANONICAL_KEYS["aws"]
MANAGED_SERVICE_ID_TAG_AZURE = CANONICAL_KEYS["azure"]

#: The pre-serialization platform spelling, which is what ``core.cloud_tags``
#: and ``serialize_azure_arm_tags`` take as *input*. AWS happens to ship this
#: form unchanged; on Azure it is not a tag name at all, because ARM rejects
#: ``/``. Named so an Azure serializer can say what it consumes without the
#: guard scan mistaking it for a driver sending an unserialized key (#1431).
PLATFORM_MANAGED_SERVICE_ID_KEY = "astrolift.io/managed_service_id"


class UnknownCloud(KeyError):
    """A cloud with no declared managed-service-id key."""


class UnknownOwnershipTag(KeyError):
    """A cloud/field pair with no declared ownership tag key."""


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


def ownership_key(cloud: str, field: str) -> str:
    """The key both sides of an ownership check must use for ``field``."""
    try:
        return OWNERSHIP_KEYS[cloud][field]
    except KeyError:
        raise UnknownOwnershipTag(
            f"no declared {field!r} ownership tag key for cloud {cloud!r}; "
            f"declare one in providers/_sdk/managed_service_tags.py so a driver "
            f"cannot read back a spelling it never wrote"
        ) from None


def read_ownership_tag(tags: Mapping[str, str] | None, field: str, cloud: str) -> str:
    """One ownership tag's value, or ``""`` when the resource does not carry it.

    Empty means "no such tag", which an ownership check must treat as not ours,
    never as a match against an also-empty expectation.
    """
    key = ownership_key(cloud, field)
    if not tags:
        return ""
    return str(tags.get(key) or "")


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
