"""The one decision behind explicitly authorized Azure adoption (#1365).

``azure_ownership`` refuses every mutation against a resource that cannot
prove it belongs to the caller's managed service. That is the right default
and it has a consequence: anything provisioned before its driver stamped an
identity tag is now refused on *every* mutating path, teardown included. Those
resources can only be removed out of band, which on a metered subscription is
a bill the platform cannot stop. Adoption is how such a resource is brought
back under management, so it is a migration path first and a feature second.

Adoption is therefore the one operation allowed to write an identity envelope
onto a resource the platform cannot already prove it owns. Three properties
keep that from re-opening the hole the verifier closed:

*It is never reachable implicitly.* Nothing in a driver's provision, update,
snapshot, restore or delete path calls into this module. A config flag that
means "adopt if you find something" is exactly the hazard #1365 exists to
remove, which is why ``api_management``'s ``adopt_existing`` write path was
deleted rather than kept as a shortcut.

*It distinguishes what it is taking over.* A resource carrying no Astrolift
marker at all, one the platform created but never stamped with a
managed-service id, and one carrying a *different* managed service's id are
three different things to have approved. They are classified separately, the
classification is recorded, and only the last one is refused outright unless
the caller names the owner it is displacing (see ``acknowledged_prior_owner``).

*It records what was there before.* The prior markers are captured before the
envelope is merged, because after the write the evidence is gone.

This module is pure: it reads a marker map, classifies it, and returns the
envelope to write. Talking to ARM is ``azure/managed/adoption.py``'s job, and
persisting the record is the control plane's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from _sdk.azure_ownership import (
    ARM_TAG_KEYS,
    PLATFORM_MANAGED_BY,
    AzureOwner,
    AzureOwnershipKeys,
    owner_from_tags,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Result error code for an adoption the platform refuses to perform. Distinct
#: from ``OWNERSHIP_ERROR_CODE``: a refused *mutation* means "this is not
#: yours", a refused *adoption* means "taking this over needs more from you
#: than you supplied", and an operator runbook branches differently on the two.
ADOPTION_ERROR_CODE = "adoption_refused"


class AzureAdoptionRefused(ValueError):
    """An adoption cannot proceed on the evidence and authority supplied.

    ``ValueError`` for the same reason ``AzureOwnershipError`` is one: it is a
    permanent, operator-actionable refusal, and the lifecycle layer maps those
    to a non-retryable result rather than burning a retry budget on them.
    """


class AzureAdoptionClassification(StrEnum):
    """What the resource looked like before adoption touched it.

    The distinction is the point of the record. Approving "this resource has
    no Astrolift markers at all" is a different act from approving "this
    resource belongs to another managed service and I am taking it", and an
    auditor reading the log six months later cannot recover which one happened
    from a boolean.
    """

    UNMANAGED = "unmanaged"
    """No Astrolift ``managed-by`` marker. The platform did not create it, or
    created it through a driver that never stamped one."""

    UNSTAMPED = "unstamped"
    """Platform-created (``managed-by=platform``) but carrying no
    managed-service id. This is the #1443/#1446 blast radius: every resource
    predating its driver's identity stamp, refused on teardown today."""

    FOREIGN_OWNER = "foreign_owner"
    """Carries a *different* Astrolift managed service's identity. Adoption
    here displaces a live owner, so it needs the caller to name it."""

    ALREADY_OWNED = "already_owned"
    """Already carries the adopting service's identity. Adoption is a re-stamp
    and changes nothing, which keeps a retried adoption idempotent."""


@dataclass(frozen=True)
class AzurePriorOwnership:
    """Every Astrolift marker a resource carried before adoption.

    ``markers`` is the raw map rather than a parsed subset: a field added to
    the envelope later should appear in historical records without a schema
    change, and the record's whole job is to say what was actually there.
    """

    managed_by: str = ""
    binding_id: str = ""
    managed_service_id: str = ""
    markers: dict[str, str] = field(default_factory=dict)

    @property
    def conflict_token(self) -> str:
        """The owner identity an adoption has to name to displace it.

        The managed-service id when there is one. A binding id otherwise,
        because a resource can carry a binding marker without an id (the
        binding tag predates the id tag on several drivers) and displacing
        that is still displacing an owner.
        """
        return self.managed_service_id or self.binding_id


@dataclass(frozen=True)
class AzureAdoptionPlan:
    """The decision, plus the evidence it was made on."""

    classification: AzureAdoptionClassification
    prior: AzurePriorOwnership
    owner: AzureOwner
    """The identity the resource will carry once the envelope is written."""

    @property
    def displaces_owner(self) -> bool:
        return self.classification is AzureAdoptionClassification.FOREIGN_OWNER

    @property
    def is_noop(self) -> bool:
        return self.classification is AzureAdoptionClassification.ALREADY_OWNED


def read_prior_ownership(
    markers: Mapping[str, str] | None,
    keys: AzureOwnershipKeys = ARM_TAG_KEYS,
) -> AzurePriorOwnership:
    """Snapshot the Astrolift markers a live resource carries.

    Read through the same key table the verifier reads, including the legacy
    managed-service-id spellings, so a resource the platform *does* recognise
    is never classified as unmanaged just because it predates a rename.

    The recorded map is every ``astrolift*`` key found, not only the declared
    envelope fields: a marker an operator added by hand, or one a future
    release adds, still lands in the record without a schema change.
    """
    present = dict(markers or {})
    owner = owner_from_tags(present, keys)
    recorded = {key: value for key, value in present.items() if _is_astrolift_key(key)}
    return AzurePriorOwnership(
        managed_by=str(present.get(keys.managed_by) or ""),
        binding_id=owner.binding_id,
        managed_service_id=owner.managed_service_id,
        markers=recorded,
    )


def _is_astrolift_key(key: str) -> bool:
    """Whether a tag or metadata key belongs to the platform's namespace.

    Three surfaces, three spellings (``astrolift-`` ARM tags, ``astrolift_``
    file-share metadata, ``astrolift_io_`` blob metadata), and the record has
    to capture all of them without knowing which surface it is reading.
    """
    return key.casefold().startswith(("astrolift-", "astrolift_", "astrolift."))


def plan_azure_adoption(
    markers: Mapping[str, str] | None,
    owner: AzureOwner,
    *,
    resource: str,
    keys: AzureOwnershipKeys = ARM_TAG_KEYS,
    acknowledged_prior_owner: str = "",
) -> AzureAdoptionPlan:
    """Classify ``resource`` and refuse the adoptions that need more authority.

    ``acknowledged_prior_owner`` must equal the owner identity being displaced.
    A boolean "yes I am sure" would be satisfiable without ever looking at the
    resource; requiring the exact id means the caller has read what they are
    taking over, and the value they typed is what the audit record stores.
    """
    if not owner.managed_service_id:
        raise AzureAdoptionRefused(
            f"refusing to adopt {resource}: the caller supplied no managed-service identity "
            f"to stamp, so the resource would be no more owned afterwards than before",
        )

    prior = read_prior_ownership(markers, keys)
    classification = _classify(prior, owner)

    if classification is AzureAdoptionClassification.FOREIGN_OWNER:
        displaced = prior.conflict_token
        if acknowledged_prior_owner != displaced:
            raise AzureAdoptionRefused(
                f"refusing to adopt {resource}: it belongs to {displaced}, and adopting it away "
                f"requires naming that owner explicitly in the request",
            )

    return AzureAdoptionPlan(classification=classification, prior=prior, owner=owner)


def _classify(prior: AzurePriorOwnership, owner: AzureOwner) -> AzureAdoptionClassification:
    if prior.managed_service_id:
        if prior.managed_service_id == owner.managed_service_id:
            # A conflicting *binding* under a matching managed-service id is
            # still a foreign owner: the binding tag is the finer-grained
            # marker and two bindings of one service are two resources.
            if owner.binding_id and prior.binding_id and prior.binding_id != owner.binding_id:
                return AzureAdoptionClassification.FOREIGN_OWNER
            return AzureAdoptionClassification.ALREADY_OWNED
        return AzureAdoptionClassification.FOREIGN_OWNER
    if prior.binding_id and owner.binding_id and prior.binding_id != owner.binding_id:
        return AzureAdoptionClassification.FOREIGN_OWNER
    if prior.managed_by == PLATFORM_MANAGED_BY:
        return AzureAdoptionClassification.UNSTAMPED
    return AzureAdoptionClassification.UNMANAGED
