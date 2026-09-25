"""Astrolift-side orphan detection + reaper (#995 — the verify-clean primitive).

Every teardown in the platform is DB-keyed: it resolves the cloud resource
to delete *from* a platform row. So if a delete silently no-ops / partially
fails (e.g. #993), or a row's owner vanishes while the resource lingers, the
cloud resource is stranded with no live platform owner.

This module has two halves:

* **Detection** (``scan_orphans``) — read-only. It enumerates platform-owned
  resources two ways and reports those with no live owner:
    - *cloud-enumerated* (IAM roles): drivers' ``list_owned_*`` surfaces list
      what the platform actually owns on the cloud side; we diff against the
      FORWARD mapping (the same naming functions provisioning uses) for every
      live app, so the scanner stays in lockstep with provisioning by
      construction (no reverse parser to drift) and is robust to #994 name
      hash-truncation.
    - *db-enumerated* (managed services): a ``ManagedService`` row that still
      holds a backend resource handle (``backend_ref``) but whose owning app /
      environment was soft-deleted out from under it — teardown started but
      never reaped the cloud side.
  Each ``OrphanResource`` carries enough identity to reap it: the cloud
  handle, the kind, which cluster can reap it, the reap key the reaper
  dispatches on, and a human ``reason``. This is the campaign's verify-clean
  assertion: "after teardown, are there orphans?".

* **Reaper** (``reap_orphan``) — deprovisions a *detected* orphan THROUGH the
  provider drivers, never a raw API delete. It REUSES the now-idempotent
  deprovision path from #1034 (``managed_service_lifecycle._deprovision_sync``
  + ``_finalize_sync``, with the "already gone -> success" backstop) for
  managed services, and the capability driver's idempotent
  ``delete_identity_role`` for IAM roles. It refuses to touch anything that
  still has a live owner (only detection-proven orphans are reapable), and is
  idempotent — an already-gone resource reports success, so a re-run converges.
  ``force_destroy`` carries through to the driver for deletion-protected
  resources.
"""

from __future__ import annotations

import dataclasses
import logging

log = logging.getLogger("astrolift_operations.orphan_reaper")

# Resource kinds the scanner understands. More land here as their owned-
# enumeration surfaces ship (ECR repos, S3 buckets, RDS, DNS zones).
KIND_IAM_ROLE = "iam_role"
KIND_MANAGED_SERVICE = "managed_service"
ALL_KINDS = frozenset({KIND_IAM_ROLE, KIND_MANAGED_SERVICE})

# classifications
_DANGLING = "dangling"  # platform owns the cloud resource, no owner row at all
_ORPHANED_OWNER = "orphaned-owner"  # owner row exists but its app/env was deleted


@dataclasses.dataclass(frozen=True)
class OrphanResource:
    kind: str
    identifier: str  # cloud handle / human-readable id
    classification: str
    # --- reap identity (#995): enough to drive the reaper -------------
    cluster_slug: str = ""  # which managed cluster's driver can reap it
    reap_key: str = ""  # key the reaper dispatches on (role name | str(pk))
    reason: str = ""  # operator-facing "why orphaned"


@dataclasses.dataclass(frozen=True)
class OrphanReport:
    orphans: list[OrphanResource]
    scanned_kinds: list[str]
    # Kinds whose enumeration failed or isn't supported on any cluster —
    # surfaced explicitly so a partial scan is never read as "all clean".
    incomplete_kinds: list[str]

    @property
    def complete(self) -> bool:
        return not self.incomplete_kinds


@dataclasses.dataclass(frozen=True)
class ReapResult:
    ok: bool
    kind: str
    identifier: str
    message: str
    already_gone: bool = False
    refused: bool = False


# ---- detection -----------------------------------------------------


def _live_role_names() -> set[str]:
    """Every IAM role name a LIVE app legitimately owns.

    A platform-owned role is an orphan only if NO live app maps to it under
    *any* of the per-app role-naming functions. The runtime workload-identity
    role is just one of four roles the platform mints per app — the build
    (#978), CI-push (#994/#1026) and static-build (#1010) roles are owned and
    tagged too, so they must all be in the live set or the reaper would treat
    a running app's build role as garbage and wipe it.
    """
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import (
        build_identity_role_name,
        ci_push_role_name,
        static_build_role_name,
        workload_identity_role_name,
    )

    names: set[str] = set()
    for app in RegisteredApp.objects.filter(
        deleted_at__isnull=True,
    ).select_related("organization"):
        names.add(workload_identity_role_name(app))
        names.add(build_identity_role_name(app))
        names.add(ci_push_role_name(app))
        names.add(static_build_role_name(app))
    return names


def _managed_service_owner_gone(svc) -> bool:
    """True when a managed service's owning app or environment is soft-deleted.

    Symmetric across detection and the reaper: detection flags a live service
    whose owner is gone; the reaper refuses to reap a service whose owner is
    still live. ``registered_app`` / ``app_environment`` must be loaded via
    ``select_related`` so a soft-deleted owner is still readable (the default
    manager would otherwise hide it).
    """
    app = svc.registered_app
    env = svc.app_environment
    app_gone = app is not None and app.deleted_at is not None
    env_gone = env is not None and env.deleted_at is not None
    return app_gone or env_gone


def _scan_iam_roles(managed_clusters, *, orphans, scanned, incomplete) -> None:
    from _sdk import UnsupportedOperationError

    from core.app_deploy import driver_for_capability

    live = _live_role_names()
    # Track the first managed cluster that reported each role so the reaper
    # knows which cluster's identity driver to delete it through.
    owned_by_cluster: dict[str, str] = {}
    enumerated = False
    errored = False
    for cluster in managed_clusters:
        try:
            driver = driver_for_capability(cluster, "identity")
            for name in driver.list_owned_roles():
                owned_by_cluster.setdefault(name, cluster.slug)
            enumerated = True
        except UnsupportedOperationError:
            continue  # this cloud can't enumerate — not an error
        except Exception:
            log.warning(
                "scan_orphans: list_owned_roles failed on cluster %s",
                cluster.slug,
                exc_info=True,
            )
            errored = True
    if enumerated and not errored:
        scanned.append(KIND_IAM_ROLE)
        for name in sorted(set(owned_by_cluster) - live):
            orphans.append(
                OrphanResource(
                    kind=KIND_IAM_ROLE,
                    identifier=name,
                    classification=_DANGLING,
                    cluster_slug=owned_by_cluster[name],
                    reap_key=name,
                    reason="platform-owned IAM role with no live app mapping to it",
                ),
            )
    else:
        # Couldn't fully enumerate (no supporting driver, or a list error) —
        # report incomplete rather than implying clean.
        incomplete.append(KIND_IAM_ROLE)


def _scan_managed_services(*, orphans, scanned) -> None:
    """DB-side scan: a live managed service holding a backend handle whose
    owning app/environment was soft-deleted. Pure DB query — always complete.
    """
    from astrolift_services.models import ManagedService

    scanned.append(KIND_MANAGED_SERVICE)
    candidates = (
        ManagedService.all_objects.filter(deleted_at__isnull=True)
        .exclude(backend_ref="")
        .select_related(
            "registered_app",
            "app_environment",
            "app_environment__tenant_cluster",
        )
    )
    for svc in candidates:
        if not _managed_service_owner_gone(svc):
            continue
        cluster = getattr(svc.app_environment, "tenant_cluster", None)
        orphans.append(
            OrphanResource(
                kind=KIND_MANAGED_SERVICE,
                identifier=svc.backend_ref or "",
                classification=_ORPHANED_OWNER,
                cluster_slug=cluster.slug if cluster is not None else "",
                reap_key=str(svc.pk),
                reason=(
                    f"managed service {svc.kind!r} (handle {svc.backend_ref!r}) "
                    "whose owning app/environment was deleted"
                ),
            ),
        )


def scan_orphans(*, kinds: set[str] | None = None) -> OrphanReport:
    """Enumerate platform-owned resources and report those with no live owner.

    Pure detection — no deletes. ``kinds`` restricts the scan to a subset
    (the reaper passes the single kind it's about to reap to re-verify).
    """
    from astrolift_clusters.models import TenantCluster

    kinds = (kinds or set(ALL_KINDS)) & ALL_KINDS
    orphans: list[OrphanResource] = []
    scanned: list[str] = []
    incomplete: list[str] = []

    if KIND_IAM_ROLE in kinds:
        managed_clusters = list(
            TenantCluster.objects.filter(
                deleted_at__isnull=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            ),
        )
        _scan_iam_roles(
            managed_clusters,
            orphans=orphans,
            scanned=scanned,
            incomplete=incomplete,
        )

    if KIND_MANAGED_SERVICE in kinds:
        _scan_managed_services(orphans=orphans, scanned=scanned)

    return OrphanReport(
        orphans=orphans,
        scanned_kinds=scanned,
        incomplete_kinds=incomplete,
    )


# ---- reaper --------------------------------------------------------


def reap_orphan(
    *,
    kind: str,
    reap_key: str,
    cluster_slug: str = "",
    force_destroy: bool = False,
) -> ReapResult:
    """Deprovision a single detected orphan THROUGH the provider drivers.

    Refuses anything that still has a live owner (only detection-proven
    orphans are reapable). Idempotent: an already-gone resource reports
    success. ``force_destroy`` carries through to the driver for
    deletion-protected resources.
    """
    if kind not in ALL_KINDS:
        return ReapResult(
            ok=False,
            kind=kind,
            identifier=reap_key,
            message=f"unknown orphan kind {kind!r}",
            refused=True,
        )
    if kind == KIND_MANAGED_SERVICE:
        return _reap_managed_service(reap_key, force_destroy=force_destroy)
    return _reap_iam_role(reap_key, cluster_slug=cluster_slug)


def _reap_managed_service(reap_key: str, *, force_destroy: bool) -> ReapResult:
    from astrolift_services.models import ManagedService
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _deprovision_sync,
        _finalize_sync,
    )

    try:
        pk = int(reap_key)
    except (TypeError, ValueError):
        return ReapResult(
            ok=False,
            kind=KIND_MANAGED_SERVICE,
            identifier=str(reap_key),
            message=f"invalid managed-service reap key {reap_key!r}",
            refused=True,
        )

    svc = (
        ManagedService.all_objects.select_related(
            "registered_app",
            "app_environment",
        )
        .filter(pk=pk)
        .first()
    )
    if svc is None or svc.deleted_at is not None:
        # Already finalized / never existed — idempotent success.
        return ReapResult(
            ok=True,
            kind=KIND_MANAGED_SERVICE,
            identifier=getattr(svc, "backend_ref", "") or reap_key,
            message="managed service already reaped",
            already_gone=True,
        )
    if not _managed_service_owner_gone(svc):
        # The owning app/environment is still live — reaping would delete a
        # running app's backing service. Refuse; only orphans are reapable.
        return ReapResult(
            ok=False,
            kind=KIND_MANAGED_SERVICE,
            identifier=svc.backend_ref or reap_key,
            message="refusing to reap: managed service has a live owning app/environment",
            refused=True,
        )

    handle = svc.backend_ref or ""
    # Reuse the #1034 idempotent deprovision path: it resolves the bound
    # driver, calls ``deprovision`` with the safety flags, and treats an
    # already-gone backend resource (raised OR reported) as soft-success.
    result = _deprovision_sync(pk, delete_data=True, force_destroy=force_destroy)
    if not result["ok"]:
        return ReapResult(
            ok=False,
            kind=KIND_MANAGED_SERVICE,
            identifier=handle,
            message=result.get("message") or "driver deprovision returned ok=False",
        )
    # Driver call succeeded (or was an idempotent no-op) — soft-delete the row.
    _finalize_sync(pk)
    already = "already deprovisioned" in (result.get("message") or "")
    return ReapResult(
        ok=True,
        kind=KIND_MANAGED_SERVICE,
        identifier=handle,
        message=result.get("message") or "reaped via driver deprovision",
        already_gone=already,
    )


def _reap_iam_role(role_name: str, *, cluster_slug: str) -> ReapResult:
    from _sdk import UnsupportedOperationError

    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import driver_for_capability

    # Refuse if the name maps to a LIVE app under any role-naming function.
    if role_name in _live_role_names():
        return ReapResult(
            ok=False,
            kind=KIND_IAM_ROLE,
            identifier=role_name,
            message="refusing to reap: IAM role belongs to a live app",
            refused=True,
        )

    clusters = TenantCluster.objects.filter(
        deleted_at__isnull=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    if cluster_slug:
        clusters = clusters.filter(slug=cluster_slug)

    last_err: Exception | None = None
    attempted = False
    for cluster in clusters:
        try:
            driver = driver_for_capability(cluster, "identity")
        except Exception as exc:  # noqa: BLE001 — try the next cluster
            last_err = exc
            continue
        delete = getattr(driver, "delete_identity_role", None)
        if not callable(delete):
            continue
        # Re-run detection on this cluster before deleting (#1985): only a role
        # the cluster's identity driver reports as one it owns is an orphan
        # candidate. Any other name (cluster or node roles, other accounts'
        # roles the credentials can reach) is never deleted, and the delete
        # goes through the cluster that actually owns the role.
        try:
            owned = set(driver.list_owned_roles())
        except Exception as exc:  # noqa: BLE001 — cannot verify, do not delete here
            last_err = exc
            continue
        if role_name not in owned:
            continue
        attempted = True
        try:
            # delete_identity_role is idempotent — it swallows NoSuchEntity
            # (#998), so reaping an already-deleted role is a clean no-op.
            delete(role_name)
            return ReapResult(
                ok=True,
                kind=KIND_IAM_ROLE,
                identifier=role_name,
                message=f"reaped IAM role via identity driver on cluster {cluster.slug}",
            )
        except UnsupportedOperationError:
            continue
        except Exception as exc:  # noqa: BLE001 — surface after the loop
            last_err = exc
            continue

    if not attempted and last_err is None:
        return ReapResult(
            ok=False,
            kind=KIND_IAM_ROLE,
            identifier=role_name,
            message=f"refusing to reap: no managed cluster reports {role_name!r} as an Astrolift-owned role",
            refused=True,
        )
    msg = f"no managed cluster could reap IAM role {role_name!r}"
    if last_err is not None:
        msg += f": {last_err}"
    return ReapResult(
        ok=False,
        kind=KIND_IAM_ROLE,
        identifier=role_name,
        message=msg,
    )
