"""Route 53 zones read with a cluster's declared AWS credential (#2287).

The guided domain connection lists the hosted zones the selected cluster's
credential can see, the same identity that writes DNS for that cluster, and
binds one only to the exact reviewed domain (Leo, 2026-10-11). Like the
Cloudflare connection it is platform-operator only (``admitted``), and lists
through the organization's own clusters or shared ones, never another
organization's.

The binding is platform-written (``dns_cluster``, ``dns_cluster_zone_id``,
``dns_cluster_account_id``), never read from the tenant-editable
``dns_config``, and pins the account the zone was observed in.
"""

from __future__ import annotations

import json
import re
import time

from django.db import transaction
from django.db.models import Q

from astrolift_clusters.cloudflare_dns_transport import DnsConnectionError, hostname
from astrolift_clusters.dns_provider_connections import admitted, guid, rate_limit
from astrolift_clusters.models import ManagedDomain, TenantCluster
from core.cluster_credentials import credential_for_cluster

MAX_ZONES = 500
_ZONE_ID = re.compile(r"Z[A-Z0-9]{1,31}")
_DEADLINE_SECONDS = 30


def _cluster_snapshot(cluster) -> str:
    return json.dumps(
        (
            cluster.pk,
            cluster.organization_id,
            cluster.provider_plugin_id,
            cluster.provider_config,
            cluster.auth_config,
            cluster.region,
            cluster.cloud_account_id,
            cluster.is_active,
        ),
        sort_keys=True,
        default=str,
    )


def _cluster(org, actor, cluster_id) -> TenantCluster:
    cluster = (
        TenantCluster.objects.select_related("provider_plugin")
        .filter(guid=guid(cluster_id), is_active=True, provider_plugin__slug="aws")
        .first()
    )
    if cluster is None:
        raise DnsConnectionError("NOT_FOUND")
    if cluster.organization_id not in (None, org.pk):
        # Indistinguishable from a missing cluster on purpose.
        raise DnsConnectionError("NOT_FOUND")
    if not cluster.cloud_account_id or cluster.cloud_account_verified_at is None:
        raise DnsConnectionError("CLUSTER_ACCOUNT_UNVERIFIED")
    return cluster


def bindable_clusters(request) -> list[TenantCluster]:
    """AWS clusters whose credential the caller may list zones through."""

    with admitted(request) as (org, _actor):
        return list(
            TenantCluster.objects.filter(
                Q(organization=org) | Q(organization__isnull=True),
                is_active=True,
                provider_plugin__slug="aws",
                cloud_account_verified_at__isnull=False,
            )
            .exclude(cloud_account_id="")
            .order_by("name", "pk")[:200]
        )


def _checkpoint(request, cluster):
    saved, started = _cluster_snapshot(cluster), time.monotonic()

    def current():
        if time.monotonic() - started >= _DEADLINE_SECONDS:
            raise DnsConnectionError("DEADLINE_EXCEEDED")
        with admitted(request) as (org, actor):
            fresh = _cluster(org, actor, cluster.guid)
            if _cluster_snapshot(fresh) != saved:
                raise DnsConnectionError("CLUSTER_CHANGED")

    return current


def _route53(cluster, current):
    from aws.domain_route_clients import clients

    try:
        credential = credential_for_cluster(cluster)
    except Exception:
        raise DnsConnectionError("CLUSTER_CREDENTIAL_INVALID") from None
    return clients(
        region=cluster.region or "us-east-1",
        credential=credential,
        checkpoint=current,
        expected_account_id=cluster.cloud_account_id,
        services=("sts", "route53"),
    )


def _access_reason(exc) -> DnsConnectionError:
    if isinstance(exc, DnsConnectionError):
        return exc
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
        return DnsConnectionError("PROVIDER_ACCESS_DENIED")
    # DomainRouteError carries its code as the message.
    reason = str(exc)
    if re.fullmatch(r"NATIVE_[A-Z_]{1,60}", reason):
        return DnsConnectionError(reason)
    return DnsConnectionError("UPSTREAM_UNAVAILABLE")


def _zone_row(raw) -> dict:
    zone_id = str(raw.get("Id") or "").removeprefix("/hostedzone/")
    private = (raw.get("Config") or {}).get("PrivateZone")
    if not _ZONE_ID.fullmatch(zone_id) or type(private) is not bool:
        raise DnsConnectionError("PROVIDER_ZONE_INVALID")
    count = raw.get("ResourceRecordSetCount")
    return {
        "id": zone_id,
        "name": hostname(str(raw.get("Name") or "")),
        "private_zone": private,
        "record_count": count if type(count) is int and count >= 0 else None,
    }


def list_cluster_zones(request, cluster_id) -> tuple[list[dict], bool]:
    """The hosted zones the cluster's credential can see, by name, capped."""
    with admitted(request) as (org, actor):
        rate_limit(org, actor)
        cluster = _cluster(org, actor, cluster_id)
    current = _checkpoint(request, cluster)
    rows, truncated, marker = [], False, None
    try:
        with _route53(cluster, current) as (native, _account):
            while True:
                current()
                page = native["route53"].list_hosted_zones(
                    MaxItems="100", **({"Marker": marker} if marker else {})
                )
                current()
                rows.extend(_zone_row(raw) for raw in page.get("HostedZones") or [])
                if len(rows) > MAX_ZONES:
                    rows, truncated = rows[:MAX_ZONES], True
                    break
                if not page.get("IsTruncated"):
                    break
                next_marker = page.get("NextMarker")
                if not isinstance(next_marker, str) or not next_marker or next_marker == marker:
                    raise DnsConnectionError("PROVIDER_CURSOR_INVALID")
                marker = next_marker
    except Exception as exc:
        raise _access_reason(exc) from None
    current()
    return sorted(rows, key=lambda row: (row["name"], row["id"])), truncated


def attach_cluster_zone(request, domain_id, domain_version, cluster_id, zone_id, zone_name):
    """Bind one hosted zone, seen through ``cluster``, to the reviewed domain."""
    zone_name = hostname(zone_name)
    if not isinstance(zone_id, str) or not _ZONE_ID.fullmatch(zone_id):
        raise DnsConnectionError("INVALID_ID")
    with admitted(request, write=True) as (org, actor):
        rate_limit(org, actor)
        domain = ManagedDomain.objects.filter(guid=guid(domain_id), organization=org).first()
        if domain is None:
            raise DnsConnectionError("NOT_FOUND")
        if domain.version != domain_version or hostname(domain.zone) != zone_name:
            raise DnsConnectionError("DOMAIN_CHANGED")
        if domain.dns_driver != "route53" or domain.dns_provider_connection_id is not None:
            raise DnsConnectionError("DOMAIN_NOT_ROUTE53")
        cluster = _cluster(org, actor, cluster_id)
    current = _checkpoint(request, cluster)
    try:
        with _route53(cluster, current) as (native, account):
            current()
            observed = native["route53"].get_hosted_zone(Id=zone_id)
            current()
            zone = _zone_row(observed.get("HostedZone") or {})
    except Exception as exc:
        raise _access_reason(exc) from None
    if zone["id"] != zone_id or zone["name"] != zone_name:
        raise DnsConnectionError("PROVIDER_ZONE_IDENTITY_MISMATCH")
    with transaction.atomic():
        with admitted(request, write=True) as (org, actor):
            fresh = (
                ManagedDomain.objects.select_for_update()
                .filter(guid=guid(domain_id), organization=org)
                .first()
            )
            if (
                fresh is None
                or fresh.version != domain_version
                or (fresh.zone, fresh.dns_driver, fresh.dns_provider_connection_id)
                != (domain.zone, domain.dns_driver, domain.dns_provider_connection_id)
            ):
                raise DnsConnectionError("DOMAIN_CHANGED")
            current()
            fresh.dns_cluster = cluster
            fresh.dns_cluster_zone_id = zone_id
            fresh.dns_cluster_account_id = account
            fresh.save(
                update_fields=[
                    "dns_cluster",
                    "dns_cluster_zone_id",
                    "dns_cluster_account_id",
                    "version",
                    "updated_at",
                ]
            )
    return fresh, cluster, zone
