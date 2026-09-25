"""Activities for ``DeprovisionManagedDomainWorkflow`` (#1673 follow-up).

The inverse of ``provision_managed_domain``: deleting a managed domain from
the UI used to soft-delete the row and nothing else, leaving the hosted zone
billing monthly and a pending or issued certificate orphaned in the account
(found live: ``myastrolift.net`` survived its own deletion, conflictastro
2026-08-29). One idempotent activity tears both down, in the order that
cannot strand a validation record: certificate first, zone second.

The DNS driver is resolved via ``core.app_deploy.driver_for_capability`` with
capability key ``"dns"`` — the same dispatch path the provisioning activities
use. The ManagedDomain row is read through ``_base_manager`` because by the
time this runs the row is already soft-deleted, and the default manager hides
exactly the row that knows which certificate to revoke.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async
from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.deprovision_managed_domain")


def _get_cluster_and_dns_driver(cluster_id: int) -> tuple[Any, Any]:
    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import driver_for_capability

    cluster = TenantCluster.objects.select_related("provider_plugin").get(pk=cluster_id)
    dns_driver = driver_for_capability(cluster, "dns")
    return cluster, dns_driver


def _deprovision_sync(cluster_id: int, zone: str) -> dict[str, Any]:
    from astrolift_clusters.models.managed_domain import ManagedDomain

    _, dns_driver = _get_cluster_and_dns_driver(cluster_id)

    # _base_manager on purpose: the row is soft-deleted before this workflow
    # starts, and the default manager would hide the certificate id.
    # The row being torn down is a soft-deleted one: a live row for the same
    # name is someone who registered the zone since, and its certificate is
    # not this teardown's to revoke (#1931).
    row = (
        ManagedDomain._base_manager.filter(zone=zone, deleted_at__isnull=False)
        .order_by("-deleted_at", "-created_at")
        .first()
    )

    cert_revoked = False
    cert_id = getattr(row, "provision_cert_id", "") if row else ""
    if cert_id:
        try:
            dns_driver.revoke_cert(zone, cert_id)
            cert_revoked = True
        except Exception:
            # An already-deleted certificate must not keep the zone alive;
            # the zone teardown below is the part that stops the billing.
            log.warning("revoke_cert failed for %s (%s); continuing", zone, cert_id, exc_info=True)

    # Only the hosted zone the platform created for this row is deleted, and
    # by id: by name it could be any same-named zone in the account, such as
    # one the install itself serves (#1931). A row without one (registered,
    # not provisioned) leaves the hosted zone to the operator.
    zone_id = str(getattr(row, "provision_zone_id", "") or "")
    if zone_id:
        from astrolift_workflows.activities.provision_managed_domain import pin_managed_zone

        pin_managed_zone(dns_driver, row)
        result = dns_driver.deprovision_zone(zone)
    else:
        log.warning("managed domain %s has no platform-created hosted zone; leaving DNS untouched", zone)
        result = {"deleted": False, "records_removed": 0, "skipped": "no platform-created hosted zone"}
    return {
        "zone": zone,
        "zone_deleted": bool(result.get("deleted")),
        "records_removed": result.get("records_removed", 0),
        "cert_revoked": cert_revoked,
    }


@activity.defn(name="astrolift.managed_domain.deprovision_resources")
async def deprovision_managed_domain_resources(cluster_id: int, zone: str) -> dict[str, Any]:
    """Delete the zone's certificate and hosted zone. Idempotent."""
    return await sync_to_async(_deprovision_sync)(cluster_id, zone)
