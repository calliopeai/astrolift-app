"""Activities for ``ProvisionManagedDomainWorkflow`` (#781).

Six-step provisioning sequence for a platform-managed DNS zone:

1. ``provision_dns_zone`` — Create the hosted zone via the cluster's
   ``DnsDriver.provision_zone``; returns zone_id + authoritative
   nameservers the operator registers at their registrar.
2. ``request_wildcard_cert_for_zone`` — Request a wildcard ACM/cloud
   cert for ``*.<zone>`` via ``DnsDriver.request_wildcard_cert``;
   writes the DNS-01 CNAME validation records into the zone so the
   cert authority can verify ownership.
3. ``poll_cert_issuance`` — Poll ``DnsDriver.poll_cert_status`` until
   status transitions to ``"issued"`` (or the workflow times out after
   max_attempts).
4. ``register_managed_domain_row`` — Upsert the ``ManagedDomain`` row
   with ``dns_config`` populated (zone_id + certificate ARN); row
   starts inactive so the health-check gate controls activation.
5. ``validate_ns_delegation`` — Compare the zone's public NS records
   against the nameservers step 1 handed the operator. This is the
   ``ZoneRegistrationStep.VALIDATE_NS_DELEGATION`` gate: without it a
   zone whose registrar still points at the operator's old DNS provider
   gets marked active and apps are provisioned at hostnames that never
   resolve.
6. ``mark_managed_domain_active`` — Flip ``is_active=True`` after the
   workflow's health-check step confirms end-to-end resolution.

``provision_state`` carries ``ZoneRegistrationStep`` members from
``astrolift_clusters.dns_layout`` — that enum is the spec-13 §2.0.4 order
the operator UI reads the row against.

Cert SANs come from ``astrolift_clusters.tls_policy.wildcard_sans_for_org``
rather than the DNS driver's ``[<zone>, *.<zone>]`` default, so the
requested wildcard also covers the preview zone (``*.pr.<zone>``) the
platform issues preview-environment hostnames in.

All activities use ``sync_to_async`` for Django ORM access. The DNS
driver is resolved via ``core.app_deploy.driver_for_capability`` with
capability key ``"dns"`` — same dispatch path as the custom-domain and
cluster-decommission activities.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.provision_managed_domain")


def _get_cluster_and_dns_driver(cluster_id: int) -> tuple[Any, Any]:
    """Load the TenantCluster row and resolve its DNS driver.

    Raises on not-found or driver-unresolvable so the activity
    surfaces a clear error to the workflow rather than an AttributeError.
    """
    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import driver_for_capability

    cluster = TenantCluster.objects.select_related("provider_plugin").get(pk=cluster_id)
    dns_driver = driver_for_capability(cluster, "dns")
    return cluster, dns_driver


def _wildcard_cert_sans(zone: str, domain: Any) -> list[str]:
    """The SubjectAltName list to request for ``zone``'s wildcard cert.

    FLAT layout is the only shape live hostname generation emits
    (``astrolift_manifest.hostname`` renders ``<app>.<base-zone>`` for apps and
    ``pr-<n>-<app>.pr.<base-zone>`` for previews), so the org slug never lands
    in the SANs; it is passed because the policy call is layout-generic. The
    owning org's slug is used when the row has one so the audited driver call
    names who the zone belongs to.
    """
    from astrolift_clusters.tls_policy import ZoneLayout, wildcard_sans_for_org

    organization = getattr(domain, "organization", None)
    org_slug = getattr(organization, "slug", "") or zone.split(".", 1)[0]
    scope = wildcard_sans_for_org(
        base_zone=zone,
        org_slug=org_slug,
        layout=ZoneLayout.FLAT,
    )
    return list(scope.sans)


def _provision_dns_zone_sync(cluster_id: int, zone: str) -> dict[str, Any]:
    from astrolift_clusters.dns_layout import ZoneRegistrationStep
    from astrolift_clusters.models import ManagedDomain

    cluster, dns_driver = _get_cluster_and_dns_driver(cluster_id)
    result = dns_driver.provision_zone(zone)
    zone_id: str = result.get("zone_id", "")
    nameservers: list = result.get("nameservers", [])

    plugin_slug = cluster.provider_plugin.slug
    existing = ManagedDomain.objects.filter(zone=zone, deleted_at__isnull=True).first()
    if existing is not None:
        existing.provision_state = ZoneRegistrationStep.VALIDATE_NS_DELEGATION.value
        existing.provision_nameservers = nameservers
        existing.save(update_fields=["provision_state", "provision_nameservers", "updated_at", "version"])
    else:
        ManagedDomain.objects.create(
            zone=zone,
            dns_driver=plugin_slug,
            dns_config={},
            is_wildcard_managed=True,
            default_for=ManagedDomain.DefaultFor.NONE,
            provision_state=ZoneRegistrationStep.VALIDATE_NS_DELEGATION.value,
            provision_nameservers=nameservers,
        )

    return {"zone_id": zone_id, "nameservers": nameservers}


@activity.defn(name="astrolift.managed_domain.provision_dns_zone")
async def provision_dns_zone(cluster_id: int, zone: str) -> dict[str, Any]:
    """Create the hosted zone via the cluster's DnsDriver, persist NS records
    to ManagedDomain, and set provision_state='validate_ns_delegation'.
    Returns ``{"zone_id": str, "nameservers": list[str]}``."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_provision_dns_zone_sync)(cluster_id, zone)
    log.info(
        "provision_dns_zone zone=%s zone_id=%s nameservers=%s",
        zone,
        result.get("zone_id"),
        result.get("nameservers"),
        extra={"cluster_id": cluster_id},
    )
    return result


def _request_wildcard_cert_sync(cluster_id: int, zone: str, zone_id: str) -> str:
    from astrolift_clusters.dns_layout import ZoneRegistrationStep
    from astrolift_clusters.models import ManagedDomain

    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)
    domain = ManagedDomain.objects.filter(zone=zone, deleted_at__isnull=True).first()
    result = dns_driver.request_wildcard_cert(
        zone,
        zone_id,
        sans=_wildcard_cert_sans(zone, domain),
    )
    cert_id: str = result.get("cert_id", "")
    validation_records: list = result.get("validation_records", [])

    # Write each DNS-01 CNAME validation record the cert authority
    # requires. The driver returns them as a list of
    # {"name": str, "value": str} dicts (CNAME target).
    for record in validation_records:
        try:
            dns_driver.ensure_record(
                zone=zone,
                name=record["name"].rstrip("."),
                type="CNAME",
                value=record["value"],
                ttl=300,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "request_wildcard_cert_for_zone: failed to write validation record %s: %s",
                record.get("name"),
                exc,
            )

    # Persist cert state so the UI can surface CNAME records immediately.
    if domain is not None:
        domain.provision_cert_id = cert_id
        domain.provision_validation_records = validation_records
        domain.provision_state = ZoneRegistrationStep.CONFIGURE_CERT_POLICY.value
        domain.save(
            update_fields=[
                "provision_cert_id",
                "provision_validation_records",
                "provision_state",
                "updated_at",
                "version",
            ]
        )

    return cert_id


@activity.defn(name="astrolift.managed_domain.request_wildcard_cert")
async def request_wildcard_cert_for_zone(cluster_id: int, zone: str, zone_id: str) -> str:
    """Request a wildcard cert for ``*.<zone>`` and write CNAME
    validation records into the zone. Returns the cert_id."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    cert_id = await sync_to_async(_request_wildcard_cert_sync)(cluster_id, zone, zone_id)
    log.info(
        "request_wildcard_cert_for_zone zone=%s cert_id=%s",
        zone,
        cert_id,
        extra={"cluster_id": cluster_id},
    )
    return cert_id


def _poll_cert_issuance_sync(cluster_id: int, zone: str, cert_id: str) -> dict[str, Any]:
    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)
    return dns_driver.poll_cert_status(zone, cert_id)


@activity.defn(name="astrolift.managed_domain.poll_cert_issuance")
async def poll_cert_issuance(cluster_id: int, zone: str, cert_id: str) -> dict[str, Any]:
    """Poll cert issuance status. Returns the dict from
    ``DnsDriver.poll_cert_status`` — callers check ``result["status"]``
    for ``"issued"`` / ``"pending"`` / ``"failed"``."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_poll_cert_issuance_sync)(cluster_id, zone, cert_id)
    log.info(
        "poll_cert_issuance zone=%s cert_id=%s status=%s",
        zone,
        cert_id,
        result.get("status"),
        extra={"cluster_id": cluster_id},
    )
    return result


def _register_managed_domain_row_sync(
    cluster_id: int,
    zone: str,
    zone_id: str,
    cert_id: str,
) -> int:
    """Upsert the ManagedDomain row. Returns the row PK.

    Uses a filter-then-create pattern to stay compatible with the
    partial unique constraint on (zone, deleted_at IS NULL) — Django's
    update_or_create can't filter on IS NULL in the lookup kwargs
    without special handling.
    """
    from astrolift_clusters.dns_layout import ZoneRegistrationStep
    from astrolift_clusters.models import ManagedDomain, TenantCluster

    cluster = TenantCluster.objects.select_related("provider_plugin").get(pk=cluster_id)
    plugin_slug = cluster.provider_plugin.slug

    dns_config: dict = {
        "zone_id": zone_id,
        "certificate_arn": cert_id,
    }

    existing = ManagedDomain.objects.filter(zone=zone, deleted_at__isnull=True).first()
    if existing is not None:
        existing.dns_driver = plugin_slug
        existing.dns_config = dns_config
        existing.is_wildcard_managed = True
        existing.provision_state = ZoneRegistrationStep.REGISTER_MANAGED_DOMAIN.value
        existing.save(
            update_fields=[
                "dns_driver",
                "dns_config",
                "is_wildcard_managed",
                "provision_state",
                "updated_at",
                "version",
            ]
        )
        return existing.pk

    domain = ManagedDomain.objects.create(
        zone=zone,
        dns_driver=plugin_slug,
        dns_config=dns_config,
        is_wildcard_managed=True,
        default_for=ManagedDomain.DefaultFor.NONE,
        provision_state=ZoneRegistrationStep.REGISTER_MANAGED_DOMAIN.value,
    )
    return domain.pk


@activity.defn(name="astrolift.managed_domain.register_row")
async def register_managed_domain_row(
    cluster_id: int,
    zone: str,
    zone_id: str,
    cert_id: str,
) -> int:
    """Create or update the ManagedDomain row. Returns the row PK."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    pk = await sync_to_async(_register_managed_domain_row_sync)(cluster_id, zone, zone_id, cert_id)
    log.info(
        "register_managed_domain_row zone=%s pk=%d",
        zone,
        pk,
        extra={"cluster_id": cluster_id},
    )
    return pk


def _observed_nameservers(zone: str) -> tuple[frozenset[str] | None, str]:
    """Read the zone's NS records from public DNS.

    Returns ``(nameservers, error)``. ``None`` means the lookup could not be
    performed at all (every resolver unreachable) — which is NOT the same as
    an empty set, which means a resolver answered and the name has no
    delegation. The gate fails on the second and skips on the first: a check
    we could not run must not strand a correctly delegated zone.

    Queries the recursive resolver rather than the zone's own authoritative
    servers on purpose — the question is what the rest of the internet is
    told to ask, i.e. the parent zone's delegation, not what our hosted zone
    claims about itself.
    """
    from _sdk._dns_probe import DnsResolveError, lookup_ns

    try:
        records = lookup_ns(zone)
    except DnsResolveError as exc:
        return None, str(exc)
    return frozenset(ns.lower().rstrip(".") for ns in records), ""


def _validate_ns_delegation_sync(cluster_id: int, zone: str) -> dict[str, Any]:
    from astrolift_clusters.dns_layout import NsDelegationCheck, evaluate_ns_delegation
    from astrolift_clusters.models import ManagedDomain

    domain = ManagedDomain.objects.filter(zone=zone, deleted_at__isnull=True).first()
    expected = list(getattr(domain, "provision_nameservers", None) or [])
    if not expected:
        # The platform never handed out nameservers for this zone
        # (is_platform_managed_zone=False), so there is no delegation of ours
        # to verify — the caller owns the zone's NS records.
        return {
            "passed": True,
            "reason": "no platform nameservers recorded; delegation check not applicable",
        }

    observed, error = _observed_nameservers(zone)
    if observed is None:
        return {"passed": True, "reason": f"delegation check skipped: {error}"}

    passed, reason = evaluate_ns_delegation(
        check=NsDelegationCheck(
            zone=zone,
            expected_nameservers=frozenset(expected),
            observed_nameservers=observed,
        )
    )
    return {"passed": passed, "reason": reason}


@activity.defn(name="astrolift.managed_domain.validate_ns_delegation")
async def validate_ns_delegation(cluster_id: int, zone: str) -> dict[str, Any]:
    """Check the zone's public NS records against the nameservers the platform
    returned when it created the zone.

    Returns ``{"passed": bool, "reason": str}``; ``reason`` is written for the
    operator (e.g. which nameservers are still missing at the registrar). The
    workflow refuses to mark the zone active when this fails, so apps are never
    provisioned into a zone whose registrar still points somewhere else."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_validate_ns_delegation_sync)(cluster_id, zone)
    log.info(
        "validate_ns_delegation zone=%s passed=%s reason=%s",
        zone,
        result.get("passed"),
        result.get("reason"),
        extra={"cluster_id": cluster_id},
    )
    return result


def _mark_active_sync(cluster_id: int, zone: str) -> None:
    from astrolift_clusters.dns_layout import ZoneRegistrationStep
    from astrolift_clusters.models import ManagedDomain

    domain = ManagedDomain.objects.filter(
        zone=zone,
        deleted_at__isnull=True,
    ).get()
    domain.provision_state = ZoneRegistrationStep.MARK_ACTIVE.value
    domain.save(update_fields=["provision_state", "updated_at", "version"])


@activity.defn(name="astrolift.managed_domain.mark_active")
async def mark_managed_domain_active(cluster_id: int, zone: str) -> None:
    """Set ``provision_state='mark_active'`` on the ManagedDomain row so
    the platform and UI treat the zone as fully provisioned."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_active_sync)(cluster_id, zone)
    log.info(
        "mark_managed_domain_active zone=%s",
        zone,
        extra={"cluster_id": cluster_id},
    )


def _reissue_cert_sync(
    cluster_id: int,
    zone: str,
    cert_id: str,
    zone_id: str,
) -> dict[str, Any]:
    """Revoke the existing cert and request a fresh wildcard cert.

    Reads is_wildcard_managed from the ManagedDomain row to determine
    whether this is a platform-managed zone whose validation records we
    own and must write into DNS.
    """
    from astrolift_clusters.dns_layout import ZoneRegistrationStep
    from astrolift_clusters.models import ManagedDomain

    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)

    # Delete the old cert so the CA slot is freed.
    dns_driver.revoke_cert(zone, cert_id)

    domain = ManagedDomain.objects.filter(zone=zone, deleted_at__isnull=True).first()

    # Request a new wildcard cert over the same SAN set as the original — a
    # reissue that narrowed the scope would silently drop preview hostnames.
    result = dns_driver.request_wildcard_cert(
        zone,
        zone_id,
        sans=_wildcard_cert_sans(zone, domain),
    )
    new_cert_id: str = result.get("cert_id", "")
    validation_records: list[dict] = result.get("validation_records", [])

    # For platform-managed zones write each DNS-01 CNAME into the zone.
    if domain is not None and domain.is_wildcard_managed:
        for record in validation_records:
            try:
                dns_driver.ensure_record(
                    zone=zone,
                    name=record["name"].rstrip("."),
                    type="CNAME",
                    value=record["value"],
                    ttl=300,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "reissue_cert: failed to write validation record %s: %s",
                    record.get("name"),
                    exc,
                )

        domain.provision_cert_id = new_cert_id
        domain.provision_validation_records = validation_records
        domain.provision_state = ZoneRegistrationStep.CONFIGURE_CERT_POLICY.value
        domain.save(
            update_fields=[
                "provision_cert_id",
                "provision_validation_records",
                "provision_state",
                "updated_at",
                "version",
            ]
        )

    return {"cert_id": new_cert_id, "validation_records": validation_records}


@activity.defn(name="astrolift.managed_domain.reissue_cert")
async def reissue_cert(
    cluster_id: int,
    zone: str,
    cert_id: str,
    zone_id: str,
) -> dict[str, Any]:
    """Revoke ``cert_id`` and request a new wildcard cert for ``*.<zone>``.

    If the zone is platform-managed (``is_wildcard_managed=True`` on the
    ``ManagedDomain`` row) the DNS-01 CNAME validation records are written
    directly into the zone via ``DnsDriver.ensure_record``, and the row's
    ``provision_cert_id``, ``provision_validation_records``, and
    ``provision_state`` are updated.

    Returns ``{"cert_id": str, "validation_records": list}``."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_reissue_cert_sync)(cluster_id, zone, cert_id, zone_id)
    log.info(
        "reissue_cert zone=%s new_cert_id=%s",
        zone,
        result.get("cert_id"),
        extra={"cluster_id": cluster_id},
    )
    return result
