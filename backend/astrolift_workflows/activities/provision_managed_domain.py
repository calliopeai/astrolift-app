"""Activities for ``ProvisionManagedDomainWorkflow`` (#781).

Five-step provisioning sequence for a platform-managed DNS zone:

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
5. ``mark_managed_domain_active`` — Flip ``is_active=True`` after the
   workflow's health-check step confirms end-to-end resolution.

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


def _provision_dns_zone_sync(cluster_id: int, zone: str) -> dict[str, Any]:
    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)
    result = dns_driver.provision_zone(zone)
    return {
        "zone_id": result.get("zone_id", ""),
        "nameservers": result.get("nameservers", []),
    }


@activity.defn(name="astrolift.managed_domain.provision_dns_zone")
async def provision_dns_zone(cluster_id: int, zone: str) -> dict[str, Any]:
    """Create the hosted zone via the cluster's DnsDriver. Returns
    ``{"zone_id": str, "nameservers": list[str]}``."""
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
    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)
    result = dns_driver.request_wildcard_cert(zone, zone_id)
    cert_id: str = result.get("cert_id", "")

    # Write each DNS-01 CNAME validation record the cert authority
    # requires. The driver returns them as a list of
    # {"name": str, "value": str} dicts (CNAME target).
    for record in result.get("validation_records", []):
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

    return cert_id


@activity.defn(name="astrolift.managed_domain.request_wildcard_cert")
async def request_wildcard_cert_for_zone(
    cluster_id: int, zone: str, zone_id: str
) -> str:
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
        existing.save(update_fields=["dns_driver", "dns_config", "is_wildcard_managed", "updated_at", "version"])
        return existing.pk

    domain = ManagedDomain.objects.create(
        zone=zone,
        dns_driver=plugin_slug,
        dns_config=dns_config,
        is_wildcard_managed=True,
        default_for=ManagedDomain.DefaultFor.NONE,
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
    pk = await sync_to_async(_register_managed_domain_row_sync)(
        cluster_id, zone, zone_id, cert_id
    )
    log.info(
        "register_managed_domain_row zone=%s pk=%d",
        zone,
        pk,
        extra={"cluster_id": cluster_id},
    )
    return pk


def _mark_active_sync(cluster_id: int, zone: str) -> None:
    from astrolift_clusters.models import ManagedDomain

    domain = ManagedDomain.objects.filter(
        zone=zone,
        deleted_at__isnull=True,
    ).get()
    # ManagedDomain inherits BaseCoreModel which doesn't have is_active —
    # the field is is_wildcard_managed + dns_config for capability; the
    # "active" semantic is modelled in dns_config or caller convention.
    # Use dns_config["active"] = True as the flag the workflow sets.
    cfg = dict(domain.dns_config or {})
    cfg["active"] = True
    domain.dns_config = cfg
    domain.save(update_fields=["dns_config", "updated_at", "version"])


@activity.defn(name="astrolift.managed_domain.mark_active")
async def mark_managed_domain_active(cluster_id: int, zone: str) -> None:
    """Set ``dns_config["active"] = True`` on the ManagedDomain row so
    the platform treats the zone as live."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_active_sync)(cluster_id, zone)
    log.info(
        "mark_managed_domain_active zone=%s",
        zone,
        extra={"cluster_id": cluster_id},
    )
