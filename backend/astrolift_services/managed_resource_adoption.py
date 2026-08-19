"""Carry out an explicitly authorized adoption and record it (#1365).

The GraphQL mutation authorizes and scopes; this module does the work and
writes the evidence. Three properties are the whole point:

**Separately authorized.** The only caller is the mutation behind
``Permission.MANAGED_SERVICE_ADOPT``. Nothing in a driver lifecycle path,
no workflow activity, and no config flag reaches it. That is asserted by a
test rather than left to reading, because an implicit route back into this
function un-does #1443 and #1446 in one line.

**Recorded before it is done.** The prior markers are read off the live
resource and persisted before the envelope is merged. After the write the
cloud no longer knows what was there, so a record built afterwards could only
report what the platform intended, not what it displaced.

**Synchronous, not a workflow.** Every other lifecycle operation here starts a
Temporal workflow because it waits on a cloud resource that takes minutes to
converge. Adoption is one read and one tag merge, and the operator needs to see
the classification of what they just took over in the response -- a fire-and-
forget workflow would return "queued" for the one operation whose whole value
is telling the caller what it found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction

from astrolift_services.models import ManagedResourceAdoption
from core.events import Event

if TYPE_CHECKING:
    from astrolift_services.models import ManagedService

#: Clouds whose drivers stamp an ownership envelope adoption knows how to
#: write. AWS and GCP carry the same hazard and no shared verifier yet; naming
#: the supported set keeps a caller from getting a success for a resource
#: nothing was written to.
SUPPORTED_CLOUDS = ("azure",)


class AdoptionUnsupported(ValueError):
    """The service's cloud or driver has no adoption path."""


class AdoptionRefused(ValueError):
    """The adoption was authorized but the evidence does not permit it."""


class AdoptionFailed(RuntimeError):
    """The cloud call failed. Distinct from a refusal: worth retrying."""


@dataclass(frozen=True)
class AdoptionOutcome:
    record: ManagedResourceAdoption
    classification: str
    prior_markers: dict[str, str]
    stamped_markers: dict[str, str]


def adopt_managed_resource(
    *,
    svc: ManagedService,
    resource_id: str,
    reason: str,
    acknowledged_prior_owner: str = "",
    actor: Any = None,
    adopter: Any = None,
) -> AdoptionOutcome:
    """Stamp the platform identity envelope onto ``resource_id`` for ``svc``.

    ``adopter`` is the injection seam the tests drive; production leaves it
    ``None`` and gets one built from the driver's own install config, which is
    what confines adoption to the subscription that managed service already
    provisions into.
    """
    from _sdk.azure_adoption import AzureAdoptionRefused
    from azure.managed.adoption import (
        AzureAdoptionConfig,
        AzureAdoptionError,
        AzureResourceAdopter,
        adopt_azure_resource,
    )

    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
    from astrolift_workflows.activities.managed_service_lifecycle import build_provision_spec
    from core.cluster_observability import managed_config_for

    if not reason.strip():
        raise AdoptionRefused("an adoption must state a reason")

    cluster = svc.effective_cluster
    if cluster is None:
        raise AdoptionUnsupported(f"managed service {svc.guid} has no tenant cluster bound")

    variant = getattr(svc, "variant", "") or ""
    try:
        resolved = resolve_managed_driver(
            cluster_plugin_slug=cluster.provider_plugin.slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound as exc:
        raise AdoptionUnsupported(str(exc)) from exc

    if resolved.plugin_slug not in SUPPORTED_CLOUDS:
        raise AdoptionUnsupported(
            f"adoption is implemented for {', '.join(SUPPORTED_CLOUDS)}; "
            f"{svc.kind}/{variant or 'default'} resolves to the {resolved.plugin_slug} plugin",
        )

    driver_config = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    subscription_id = str(getattr(driver_config, "subscription_id", "") or "")
    if not subscription_id:
        raise AdoptionUnsupported(
            f"the {svc.kind} driver on cluster {cluster.slug} has no subscription_id configured, "
            f"so there is no subscription to adopt within",
        )

    spec = build_provision_spec(svc, cluster=cluster)
    if adopter is None:
        adopter = AzureResourceAdopter(config=AzureAdoptionConfig(subscription_id=subscription_id))

    try:
        outcome = adopt_azure_resource(
            adopter=adopter,
            resource_id=resource_id,
            spec=spec,
            acknowledged_prior_owner=acknowledged_prior_owner,
        )
    except AzureAdoptionRefused as exc:
        _record_attempt(
            svc=svc,
            actor=actor,
            resource_id=resource_id,
            reason=reason,
            acknowledged_prior_owner=acknowledged_prior_owner,
            status=ManagedResourceAdoption.Status.REFUSED,
            error=str(exc),
            evidence=_evidence_if_readable(adopter, resource_id),
        )
        raise AdoptionRefused(str(exc)) from exc
    except AzureAdoptionError as exc:
        _record_attempt(
            svc=svc,
            actor=actor,
            resource_id=resource_id,
            reason=reason,
            acknowledged_prior_owner=acknowledged_prior_owner,
            status=ManagedResourceAdoption.Status.FAILED,
            error=str(exc),
            evidence=None,
        )
        raise AdoptionFailed(str(exc)) from exc

    prior = outcome.plan.prior
    organization = _organization_of(svc)
    with transaction.atomic():
        record = ManagedResourceAdoption.objects.create(
            organization=organization,
            managed_service=svc,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_display=_actor_display(actor),
            cloud=resolved.plugin_slug,
            resource_id=outcome.resource_id,
            surface=str(outcome.surface),
            classification=outcome.classification,
            prior_markers=dict(prior.markers),
            prior_managed_by=prior.managed_by,
            prior_managed_service_id=prior.managed_service_id,
            prior_binding_id=prior.binding_id,
            acknowledged_prior_owner=acknowledged_prior_owner,
            reason=reason.strip(),
            stamped_markers=dict(outcome.stamped),
            status=ManagedResourceAdoption.Status.ADOPTED,
        )

    Event.emit(
        "managed_service.resource_adopted",
        {
            "managed_service_guid": str(svc.guid),
            "kind": svc.kind,
            "variant": variant,
            "cloud": resolved.plugin_slug,
            "resource_id": outcome.resource_id,
            "surface": str(outcome.surface),
            "classification": outcome.classification,
            "prior_managed_service_id": prior.managed_service_id,
            "prior_binding_id": prior.binding_id,
            "displaced_owner": bool(outcome.plan.displaces_owner),
            "reason": reason.strip(),
        },
        resource_kind="managed_service",
        resource_id=str(svc.guid),
        organization_id=organization.pk,
        actor_user_id=getattr(actor, "pk", None),
    )

    return AdoptionOutcome(
        record=record,
        classification=outcome.classification,
        prior_markers=dict(prior.markers),
        stamped_markers=dict(outcome.stamped),
    )


def _evidence_if_readable(adopter: Any, resource_id: str) -> Any:
    """``(markers, keys)`` for a refusal record, best-effort.

    A refusal usually happens *because* the markers were read and said no, so
    capturing them is what makes the record worth having. The key table travels
    with them: a blob container stores the same envelope under a different
    spelling, and parsing it with the ARM names would record a resource that
    plainly declares an owner as having declared nothing.

    A refusal raised before the read (an unparseable resource id) leaves this
    ``None`` rather than failing the record write on top of the refusal.
    """
    from azure.managed.adoption import parse_resource_id

    try:
        ref = parse_resource_id(resource_id)
        return adopter.read_markers(ref), ref.keys
    except Exception:  # noqa: BLE001 -- a record must never fail on its own evidence
        return None


def _record_attempt(
    *,
    svc: ManagedService,
    actor: Any,
    resource_id: str,
    reason: str,
    acknowledged_prior_owner: str,
    status: str,
    error: str,
    evidence: Any,
) -> ManagedResourceAdoption:
    """Persist a refused or failed attempt.

    Refusals are recorded, not only successes. "Somebody with the adopt grant
    tried to take a resource belonging to another managed service and was told
    no" is the single most interesting line this log will ever hold.
    """
    from _sdk.azure_adoption import read_prior_ownership

    markers, keys = evidence if evidence is not None else ({}, None)
    prior = read_prior_ownership(markers) if keys is None else read_prior_ownership(markers, keys)
    return ManagedResourceAdoption.objects.create(
        organization=_organization_of(svc),
        managed_service=svc,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_display=_actor_display(actor),
        cloud="azure",
        resource_id=resource_id,
        surface="",
        classification=(
            ManagedResourceAdoption.Classification.FOREIGN_OWNER
            if prior.conflict_token
            else ManagedResourceAdoption.Classification.UNMANAGED
        ),
        prior_markers=dict(prior.markers),
        prior_managed_by=prior.managed_by,
        prior_managed_service_id=prior.managed_service_id,
        prior_binding_id=prior.binding_id,
        acknowledged_prior_owner=acknowledged_prior_owner,
        reason=reason.strip(),
        stamped_markers={},
        status=status,
        error=error,
    )


def _organization_of(svc: ManagedService) -> Any:
    app = svc.registered_app
    return app.organization if app is not None else svc.project.organization


def _actor_display(actor: Any) -> str:
    if actor is None:
        return ""
    return str(getattr(actor, "email", "") or getattr(actor, "username", "") or "")
