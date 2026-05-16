"""Cloud-side cleanup activities for ``DecommissionClusterWorkflow`` (#354).

When a cluster is decommissioned the platform must also reap the
per-app cloud satellite resources astrolift created over the cluster's
lifetime — IRSA roles, ECR repos, Route53 records, ACM certs. Without
these the AWS side leaks IAM principals, container repositories, DNS
entries, and certificates indefinitely; security debt + billing leak
on every cluster turnover.

Each activity is a thin per-cluster fan-out wrapper that walks the
apps bound to the cluster and dispatches the per-app delete through
the standard provider driver. Failures are *collected* rather than
fatal — a single failed delete shouldn't strand the rest of the sweep
since the operator can re-fire decommission to converge.

Pattern, identical across all four:

  1. Resolve every app on the cluster (default_tenant_cluster +
     AppEnvironment.tenant_cluster — apps may be bound either way).
  2. Resolve the capability driver via ``driver_for_capability``.
  3. For each per-app target, call the driver's ``delete_*`` method.
  4. Catch ``NotImplementedError`` at the driver level → return
     ``{"skipped": True, "reason": ...}`` so non-AWS clouds no-op.
  5. Catch any other ``Exception`` per-target, append to ``errors``,
     keep going.

Returned dict shape (one per activity):

  ``{
       "ok": True,
       "deleted": <int>,    # successful deletes
       "errors": [<str>],   # operator-facing per-target failures
       "skipped": False,    # True when the driver doesn't implement
       "reason": "...",     # populated only when skipped=True
       "cluster_slug": ...,
   }``

Activity ordering in the workflow matters and is captured at the
workflow site (decommission_cluster.py): DNS first so records don't
outlive the cert, certs next so we don't pay for orphans, then ECR
to free image storage, then IRSA last because workloads may be
authenticating during the drain.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.cluster_decommission_cleanup")


# ---- Shared helpers -------------------------------------------------


def _resolve_capability_driver(cluster, capability: str):
    """Resolve a non-cluster driver for ``cluster``'s provider plugin.

    Mirrors the pattern in ``capability_deprovision`` — surfaces the
    driver-not-registered case as ``NotImplementedError`` so the
    cleanup activity treats non-AWS plugins (which simply don't ship
    these capabilities yet) as a clean skip rather than a hard error.
    """
    from core.app_deploy import AppDeployError, driver_for_capability

    try:
        return driver_for_capability(cluster, capability)
    except AppDeployError as exc:
        # The plugin doesn't register this capability — equivalent to
        # the driver method being NotImplemented. Surface as the same
        # exception type so the activity's outer handler treats both
        # paths uniformly (skipped=True).
        raise NotImplementedError(str(exc)) from exc


def _apps_on_cluster(cluster_id: int) -> list:
    """Every active ``RegisteredApp`` whose default cluster is this
    one OR which has at least one active ``AppEnvironment`` bound to
    this cluster. Apps may be bound either way; the cleanup needs
    both so a re-bound app doesn't leak its old satellite resources.

    Returned as a Django queryset materialized to a list so the
    caller can iterate without re-querying.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    direct_ids = set(
        RegisteredApp.objects.filter(
            default_tenant_cluster_id=cluster_id,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True),
    )
    env_bound_ids = set(
        AppEnvironment.objects.filter(
            tenant_cluster_id=cluster_id,
            deleted_at__isnull=True,
        ).values_list("registered_app_id", flat=True),
    )
    all_ids = direct_ids | env_bound_ids
    if not all_ids:
        return []
    return list(
        RegisteredApp.all_objects.filter(pk__in=all_ids).select_related(
            "organization",
            "default_tenant_cluster__provider_plugin",
        ),
    )


def _identity_role_name_for(app) -> str:
    """Canonical IAM/identity role name astrolift issues per app.

    Mirrors the convention IRSA / GKE Workload Identity / AKS
    federated creds use: ``astrolift-<org-slug>-<app-slug>``. Same
    string ``deprovision_app_identity_role`` builds — keep them in
    lockstep so the cluster-wide sweep targets the same roles the
    per-app deprovision would.
    """
    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    if org_slug:
        return f"astrolift-{org_slug}-{app.slug}"
    return f"astrolift-{app.slug}"


def _empty_skipped(cluster_slug: str, reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "deleted": 0,
        "errors": [],
        "skipped": True,
        "reason": reason,
        "cluster_slug": cluster_slug,
    }


# ---- IRSA roles ----------------------------------------------------


def _cleanup_irsa_roles_sync(cluster_id: int) -> dict[str, Any]:
    """Delete every per-app IRSA role bound to apps on the cluster.

    For each app we ask the cluster's identity driver to delete the
    canonical role name. Per-app failures are recorded but don't
    abort the sweep — the operator can re-fire decommission to
    converge on a half-cleaned cluster.
    """
    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(pk=cluster_id)
    apps = _apps_on_cluster(cluster_id)
    if not apps:
        return {
            "ok": True,
            "deleted": 0,
            "errors": [],
            "skipped": False,
            "reason": "no apps bound to cluster",
            "cluster_slug": cluster.slug,
        }
    try:
        identity_driver = _resolve_capability_driver(cluster, "identity")
    except NotImplementedError as exc:
        return _empty_skipped(cluster.slug, f"identity capability not implemented for plugin: {exc}")
    delete_role = getattr(identity_driver, "delete_identity_role", None)
    if not callable(delete_role):
        return _empty_skipped(
            cluster.slug,
            "identity driver does not implement delete_identity_role",
        )
    deleted = 0
    errors: list[str] = []
    for app in apps:
        role = _identity_role_name_for(app)
        try:
            delete_role(role)
            deleted += 1
        except NotImplementedError as exc:
            # Per-app NotImplemented (rare — would mean the driver
            # exposes the method but raises) — treat the whole sweep
            # as skipped since every other app would behave the same.
            return _empty_skipped(
                cluster.slug,
                f"identity driver delete_identity_role not implemented: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort sweep
            errors.append(f"{role}: {exc}")
    return {
        "ok": True,
        "deleted": deleted,
        "errors": errors,
        "skipped": False,
        "cluster_slug": cluster.slug,
    }


@activity.defn(name="astrolift.cluster.cleanup_irsa_roles")
async def cleanup_cluster_irsa_roles(cluster_id: int) -> dict[str, Any]:
    """Delete every per-app IRSA / Workload-Identity role on the cluster."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_cleanup_irsa_roles_sync)(cluster_id)
    log.info(
        "cleanup_cluster_irsa_roles cluster=%s deleted=%d errors=%d skipped=%s",
        summary["cluster_slug"],
        summary["deleted"],
        len(summary["errors"]),
        summary["skipped"],
    )
    return summary


# ---- ECR repos -----------------------------------------------------


def _repo_name_from_uri(uri: str, fallback_slug: str) -> str:
    """Drivers speak repo *names*; the platform stores full URIs
    (``123.dkr.ecr.us-west-2.amazonaws.com/astrolift/acme-hello``).
    Strip the registry host. Fall back to the app slug when no URI is
    recorded — matches what ``ensure_repo`` would have created.
    """
    uri = (uri or "").strip()
    if "/" in uri:
        return uri.split("/", 1)[1]
    return uri or fallback_slug


def _cleanup_ecr_repos_sync(cluster_id: int) -> dict[str, Any]:
    """Archive (or delete) every per-app image repo on the cluster's
    registry. Defaults to ``archive=True`` so existing image history
    survives — operators can flip the policy out-of-band if they need
    a hard delete.
    """
    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(pk=cluster_id)
    apps = _apps_on_cluster(cluster_id)
    if not apps:
        return {
            "ok": True,
            "deleted": 0,
            "errors": [],
            "skipped": False,
            "reason": "no apps bound to cluster",
            "cluster_slug": cluster.slug,
        }
    try:
        registry_driver = _resolve_capability_driver(cluster, "registry")
    except NotImplementedError as exc:
        return _empty_skipped(cluster.slug, f"registry capability not implemented for plugin: {exc}")
    delete_repo = getattr(registry_driver, "delete_repo", None)
    if not callable(delete_repo):
        return _empty_skipped(
            cluster.slug,
            "registry driver does not implement delete_repo",
        )
    deleted = 0
    errors: list[str] = []
    for app in apps:
        repo = _repo_name_from_uri(getattr(app, "registry_repo_uri", "") or "", app.slug)
        try:
            try:
                delete_repo(repo, archive=True)
            except TypeError:
                # Older drivers may not accept the ``archive`` kwarg —
                # fall back to the no-kwarg form so cleanup still
                # progresses.
                delete_repo(repo)
            deleted += 1
        except NotImplementedError as exc:
            return _empty_skipped(
                cluster.slug,
                f"registry driver delete_repo not implemented: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort sweep
            errors.append(f"{repo}: {exc}")
    return {
        "ok": True,
        "deleted": deleted,
        "errors": errors,
        "skipped": False,
        "cluster_slug": cluster.slug,
    }


@activity.defn(name="astrolift.cluster.cleanup_ecr_repos")
async def cleanup_cluster_ecr_repos(cluster_id: int) -> dict[str, Any]:
    """Archive every per-app container repo on the cluster's registry."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_cleanup_ecr_repos_sync)(cluster_id)
    log.info(
        "cleanup_cluster_ecr_repos cluster=%s deleted=%d errors=%d skipped=%s",
        summary["cluster_slug"],
        summary["deleted"],
        len(summary["errors"]),
        summary["skipped"],
    )
    return summary


# ---- DNS records ---------------------------------------------------


def _split_hostname(hostname: str) -> tuple[str, str]:
    """Return ``(name, zone)`` for a hostname.

    Reuses ``hostname_parent_zone`` so the split matches what
    deprovision and the per-record activity use.
    """
    from astrolift_lifecycle.custom_domain_handshake import hostname_parent_zone

    zone = hostname_parent_zone(hostname)
    if hostname == zone:
        return ("", zone)
    return (hostname.removesuffix("." + zone), zone)


def _cleanup_dns_records_sync(cluster_id: int) -> dict[str, Any]:
    """Delete every DNS record astrolift created for apps on the
    cluster — the CustomDomain CNAME(s) the operator pointed at the
    cluster, plus the per-app subdomain IngressRule records the
    platform issued under platform-managed zones.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import CustomDomain
    from astrolift_lifecycle.models.ingress import IngressRule

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(pk=cluster_id)
    apps = _apps_on_cluster(cluster_id)
    if not apps:
        return {
            "ok": True,
            "deleted": 0,
            "errors": [],
            "skipped": False,
            "reason": "no apps bound to cluster",
            "cluster_slug": cluster.slug,
        }
    try:
        dns_driver = _resolve_capability_driver(cluster, "dns")
    except NotImplementedError as exc:
        return _empty_skipped(cluster.slug, f"dns capability not implemented for plugin: {exc}")
    delete_record = getattr(dns_driver, "delete_record", None)
    if not callable(delete_record):
        return _empty_skipped(
            cluster.slug,
            "dns driver does not implement delete_record",
        )
    app_ids = [app.pk for app in apps]
    # CustomDomains: operator-supplied hostnames pointed at the
    # cluster. Each gets a CNAME (per the handshake) — delete it.
    custom_domains = list(
        CustomDomain.objects.filter(
            registered_app_id__in=app_ids,
            deleted_at__isnull=True,
        ),
    )
    # IngressRules: platform-issued subdomain entries under managed
    # zones. ``hostname`` is the FQDN we wrote.
    ingress_rules = list(
        IngressRule.objects.filter(
            registered_app_id__in=app_ids,
            deleted_at__isnull=True,
        ),
    )
    deleted = 0
    errors: list[str] = []

    def _attempt(zone: str, name: str, record_type: str, label: str) -> bool:
        nonlocal deleted
        try:
            delete_record(zone, name, record_type)
            deleted += 1
            return True
        except NotImplementedError as exc:
            raise NotImplementedError(f"dns driver delete_record not implemented: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — best-effort sweep
            errors.append(f"{label}: {exc}")
            return False

    try:
        for d in custom_domains:
            name, zone = _split_hostname(d.hostname)
            _attempt(zone, name, "CNAME", d.hostname)
        for r in ingress_rules:
            name, zone = _split_hostname(r.hostname)
            _attempt(zone, name, "CNAME", r.hostname)
    except NotImplementedError as exc:
        return _empty_skipped(cluster.slug, str(exc))

    return {
        "ok": True,
        "deleted": deleted,
        "errors": errors,
        "skipped": False,
        "cluster_slug": cluster.slug,
    }


@activity.defn(name="astrolift.cluster.cleanup_dns_records")
async def cleanup_cluster_dns_records(cluster_id: int) -> dict[str, Any]:
    """Delete every DNS record astrolift created for apps on the cluster."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_cleanup_dns_records_sync)(cluster_id)
    log.info(
        "cleanup_cluster_dns_records cluster=%s deleted=%d errors=%d skipped=%s",
        summary["cluster_slug"],
        summary["deleted"],
        len(summary["errors"]),
        summary["skipped"],
    )
    return summary


# ---- ACM certificates ---------------------------------------------


def _cleanup_acm_certs_sync(cluster_id: int) -> dict[str, Any]:
    """Revoke every auto-issued cert bound to a CustomDomain on the
    cluster's apps.

    Skips CustomDomains that don't carry a ``certificate_id`` (BYO or
    never-issued). The platform doesn't track per-row certs anywhere
    else — the CustomDomain row IS the cert ledger.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import CustomDomain

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(pk=cluster_id)
    apps = _apps_on_cluster(cluster_id)
    if not apps:
        return {
            "ok": True,
            "deleted": 0,
            "errors": [],
            "skipped": False,
            "reason": "no apps bound to cluster",
            "cluster_slug": cluster.slug,
        }
    try:
        tls_driver = _resolve_capability_driver(cluster, "tls")
    except NotImplementedError as exc:
        return _empty_skipped(cluster.slug, f"tls capability not implemented for plugin: {exc}")
    revoke = getattr(tls_driver, "revoke_certificate", None)
    if not callable(revoke):
        return _empty_skipped(
            cluster.slug,
            "tls driver does not implement revoke_certificate",
        )
    app_ids = [app.pk for app in apps]
    domains = list(
        CustomDomain.objects.filter(
            registered_app_id__in=app_ids,
            deleted_at__isnull=True,
        ),
    )
    deleted = 0
    errors: list[str] = []
    for d in domains:
        cert_id = (d.certificate_id or "").strip()
        if not cert_id:
            continue
        try:
            revoke(cert_id)
            deleted += 1
        except NotImplementedError as exc:
            return _empty_skipped(
                cluster.slug,
                f"tls driver revoke_certificate not implemented: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort sweep
            errors.append(f"{cert_id}: {exc}")
    return {
        "ok": True,
        "deleted": deleted,
        "errors": errors,
        "skipped": False,
        "cluster_slug": cluster.slug,
    }


@activity.defn(name="astrolift.cluster.cleanup_acm_certs")
async def cleanup_cluster_acm_certs(cluster_id: int) -> dict[str, Any]:
    """Revoke every auto-issued cert bound to a CustomDomain on the cluster."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_cleanup_acm_certs_sync)(cluster_id)
    log.info(
        "cleanup_cluster_acm_certs cluster=%s deleted=%d errors=%d skipped=%s",
        summary["cluster_slug"],
        summary["deleted"],
        len(summary["errors"]),
        summary["skipped"],
    )
    return summary


__all__ = [
    "_apps_on_cluster",
    "_cleanup_acm_certs_sync",
    "_cleanup_dns_records_sync",
    "_cleanup_ecr_repos_sync",
    "_cleanup_irsa_roles_sync",
    "_identity_role_name_for",
    "_repo_name_from_uri",
    "_split_hostname",
    "cleanup_cluster_acm_certs",
    "cleanup_cluster_dns_records",
    "cleanup_cluster_ecr_repos",
    "cleanup_cluster_irsa_roles",
]
