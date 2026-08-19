"""One fail-closed ownership gate for every Azure managed-resource driver (#1365).

Azure drivers derive deterministic resource names and then call
create-or-update / delete against whatever answers to that name. A name is not
a proof of ownership: a collision, a half-finished import, a stale row, or an
operator-built resource all answer to it too. Each driver grew its own
``_assert_owned``, and the copies disagreed about what "owned" means, so the
weakest one set the real safety floor.

Three concrete holes those copies shared:

*Absence read as consent.* ``if existing_id and existing_id != mine`` passes
when the resource carries no id at all, which is the unowned resource we most
need to refuse.

*Destructive paths checked less than provision.* Several drivers verified the
managed-service identity while provisioning and then called a weaker
platform-only check before delete, so a resource belonging to a different
binding was deletable as long as Astrolift had made it.

*Implicit adoption.* Matching ``org``/``app``/``env`` tags was treated as proof
of ownership, which is the collision case restated: two rows for the same app
and environment collide precisely because those tags agree.

The contract here is deliberately narrow. Ownership is proven from the ARM tags
the platform stamps at create, and nothing else. An absent marker and a
conflicting marker both refuse, before any cloud call.

Adoption of an unowned resource lives in ``_sdk.azure_adoption`` and reaches
the cloud only through the control plane's authorized ``adoptManagedResource``
operation, which carries its own permission and writes an audit record naming
the markers it displaced. Nothing in this module calls it, and no driver
lifecycle path may: a convenience version of adoption is the hazard this module
exists to remove.

Idempotency falls out of the rule rather than being special-cased. The check is
a pure comparison against tags the resource already carries, so replaying our
own operation with the same identity passes as many times as Temporal retries
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from _sdk.managed_service_tags import canonical_key, ownership_key, read_managed_service_id

if TYPE_CHECKING:
    from collections.abc import Mapping

PLATFORM_MANAGED_BY = "platform"

#: Result error code for an ownership refusal. Already the cross-cloud spelling
#: the AWS filesystem drivers use for the same situation, so the workflow layer
#: and any operator runbook keep one term for it.
OWNERSHIP_ERROR_CODE = "external_resource_collision"


@dataclass(frozen=True)
class AzureOwnershipKeys:
    """The names one Azure surface stores the ownership envelope under.

    Two surfaces, two vocabularies. ARM tags reject ``/`` and use
    ``astrolift-<name>``; blob-container and file-share *metadata* keys are C#
    identifiers and use ``astrolift_<name>``. The decision below is identical
    for both, so the spelling is data rather than a second implementation.
    """

    managed_by: str
    binding: str
    managed_service_id: str
    #: Written only by the authorized adoption operation (#1365), so every
    #: surface names them even though no driver's provision path emits them.
    adopted: str = ""
    adopted_at: str = ""


#: Post-serialization ARM tag names, taken from the same table the write path
#: serializes through. These are the ``astrolift-<name>`` forms
#: ``azure/managed/tags.py::arm_tags_for`` actually writes, never the canonical
#: ``astrolift.io/<name>`` input spelling (#1431).
ARM_TAG_KEYS = AzureOwnershipKeys(
    managed_by=ownership_key("azure", "managed_by"),
    binding=ownership_key("azure", "binding"),
    managed_service_id=canonical_key("azure"),
    adopted=ownership_key("azure", "adopted"),
    adopted_at=ownership_key("azure", "adopted_at"),
)

#: Blob-container and file-share metadata keys. ``read_managed_service_id``
#: already accepts ``astrolift_managed_service_id`` as a declared legacy Azure
#: spelling, so only the other two need naming here.
METADATA_KEYS = AzureOwnershipKeys(
    managed_by="astrolift_managed_by",
    binding="astrolift_binding",
    managed_service_id="astrolift_managed_service_id",
    adopted="astrolift_adopted",
    adopted_at="astrolift_adopted_at",
)

#: Blob-container metadata keys, a third spelling again: the flattened
#: ``astrolift_io_<name>`` form ``azure/managed/object_store_blob.py`` writes.
#: ``read_managed_service_id`` already declares
#: ``astrolift_io_managed_service_id`` as a legacy Azure spelling, so the id
#: below is only here to name the surface rather than to widen the read.
BLOB_METADATA_KEYS = AzureOwnershipKeys(
    managed_by="astrolift_io_managed_by",
    binding="astrolift_io_binding",
    managed_service_id="astrolift_io_managed_service_id",
    adopted="astrolift_io_adopted",
    adopted_at="astrolift_io_adopted_at",
)

#: Backwards-compatible aliases for the ARM names.
MANAGED_BY_TAG = ARM_TAG_KEYS.managed_by
BINDING_TAG = ARM_TAG_KEYS.binding
MANAGED_SERVICE_ID_TAG = ARM_TAG_KEYS.managed_service_id


class AzureOwnershipError(ValueError):
    """A resource cannot be proven to belong to the caller's managed service.

    ``ValueError`` because drivers that have no bespoke error type already
    raise ``ValueError`` for permanent, operator-actionable refusals, and the
    lifecycle activity maps those to a non-retryable result.
    """


class AzureOperation(StrEnum):
    """What the caller is about to do, which sets how much proof it needs.

    Every member except ``INSPECT`` demands a proven identity match. ``INSPECT``
    is for reads that cannot damage anything (``status``, ``binding``): it still
    refuses a resource Astrolift does not own and still refuses a conflicting
    marker, but it does not require the caller to name the owner, because a
    status poll holding only a handle is not a reason to fail a healthy row.

    Passing ``INSPECT`` from a call site that mutates re-opens the exact hole
    this module closes.
    """

    PROVISION = "provision"
    UPDATE = "update"
    SNAPSHOT = "snapshot"
    RESTORE = "restore"
    DELETE = "delete"
    INSPECT = "inspect"


MUTATING_OPERATIONS = frozenset(AzureOperation) - {AzureOperation.INSPECT}


@dataclass(frozen=True)
class AzureOwner:
    """The Astrolift identity of a managed resource."""

    managed_service_id: str = ""
    binding_id: str = ""


def metadata_of(resource: object) -> dict[str, str]:
    """The ``metadata`` map off a blob container or file share properties body."""
    if resource is None:
        return {}
    meta = resource.get("metadata") if isinstance(resource, dict) else getattr(resource, "metadata", None)
    if not meta:
        return {}
    return {str(key): str(value) for key, value in dict(meta).items()}


def arm_tags_of(resource: object) -> dict[str, str]:
    """The ARM ``tags`` map off whatever shape a describe call returned.

    Azure mgmt clients hand back generated models; the driver fakes and a few
    REST passthroughs hand back plain bodies. Reading the envelope wrong is
    indistinguishable from an absent envelope, and an absent envelope refuses,
    so this lives next to the rule instead of being retyped per driver.
    """
    if resource is None:
        return {}
    tags = resource.get("tags") if isinstance(resource, dict) else getattr(resource, "tags", None)
    if not tags:
        return {}
    return {str(key): str(value) for key, value in dict(tags).items()}


def owner_of(spec: object) -> AzureOwner:
    """The identity a ``ProvisionSpec`` / ``UpdateSpec`` / ``DeprovisionSpec`` /
    ``ServiceHandle`` claims.

    Read by attribute rather than by type so one call site shape serves every
    driver entry point; the four spec types carry the same two fields.
    """
    return AzureOwner(
        managed_service_id=str(getattr(spec, "managed_service_id", "") or ""),
        binding_id=str(getattr(spec, "binding_id", "") or ""),
    )


def owner_from_tags(
    tags: Mapping[str, str] | None,
    keys: AzureOwnershipKeys = ARM_TAG_KEYS,
) -> AzureOwner:
    """The identity a live resource's tags or metadata declare."""
    return AzureOwner(
        managed_service_id=read_managed_service_id(tags, "azure"),
        binding_id=str((tags or {}).get(keys.binding) or ""),
    )


def is_platform_owned(
    tags: Mapping[str, str] | None,
    keys: AzureOwnershipKeys = ARM_TAG_KEYS,
) -> bool:
    """Whether the tags carry Astrolift's platform marker at all."""
    return str((tags or {}).get(keys.managed_by) or "") == PLATFORM_MANAGED_BY


def verify_azure_ownership(
    tags: Mapping[str, str] | None,
    expected: AzureOwner,
    *,
    operation: AzureOperation,
    resource: str,
    keys: AzureOwnershipKeys = ARM_TAG_KEYS,
    error: type[Exception] = AzureOwnershipError,
) -> None:
    """Refuse ``operation`` on ``resource`` unless its tags prove it is ours.

    ``error`` lets a driver keep the exception type its own callers already
    catch and map to an error code; the decision stays here so no driver can
    soften it.
    """
    actual = owner_from_tags(tags, keys)

    if not is_platform_owned(tags, keys):
        raise error(
            f"refusing to {operation} {resource}: it carries no Astrolift "
            f"{keys.managed_by}={PLATFORM_MANAGED_BY} marker, so the platform did not create it",
        )

    if operation in MUTATING_OPERATIONS:
        if not expected.managed_service_id:
            raise error(
                f"refusing to {operation} {resource}: the caller supplied no managed-service "
                f"identity to check its ownership tags against",
            )
        if not actual.managed_service_id:
            raise error(
                f"refusing to {operation} {resource}: it carries no {keys.managed_service_id} marker, "
                f"so it cannot be proven to belong to managed service {expected.managed_service_id}; "
                f"adopting an untagged resource is a separate, explicitly authorized operation",
            )

    if (
        expected.managed_service_id
        and actual.managed_service_id
        and actual.managed_service_id != expected.managed_service_id
    ):
        raise error(
            f"refusing to {operation} {resource}: it belongs to managed service "
            f"{actual.managed_service_id}, not {expected.managed_service_id}",
        )

    if expected.binding_id and actual.binding_id and actual.binding_id != expected.binding_id:
        raise error(
            f"refusing to {operation} {resource}: it belongs to binding {actual.binding_id}, not {expected.binding_id}",
        )
