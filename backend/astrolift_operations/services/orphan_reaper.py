"""Astrolift-side orphan detection (#995 — scan phase, read-only).

Every teardown in the platform is DB-keyed: it resolves the cloud resource
to delete *from* a platform row. So if a delete silently no-ops / partially
fails but the rows are nonetheless soft-deleted (or rows vanish out-of-band),
the cloud resource is stranded with no handle a future teardown could key on.

This scan inverts that: it enumerates platform-owned cloud resources via the
drivers' ``list_owned_*`` surfaces and diffs them against live DB rows. A
resource the platform owns whose owner has no live row is an orphan.

Crucially, the diff uses the FORWARD mapping — the same naming function that
provisioning uses (``workload_identity_role_name``) — rather than parsing an
owner key back out of the cloud resource. That keeps the scanner in lockstep
with provisioning by construction (no parser to drift) and is robust to the
#994 hash-truncation of long names.

Read-only. Reaping (behind dry-run + confirm-token + elevation) is a separate
follow-up phase — this module never deletes anything.
"""

from __future__ import annotations

import dataclasses
import logging

log = logging.getLogger("astrolift_operations.orphan_reaper")

# Resource kinds the scanner understands. More land here as their
# list_owned_* driver surfaces ship (ECR repos, S3 buckets, RDS, DNS zones).
KIND_IAM_ROLE = "iam_role"
ALL_KINDS = frozenset({KIND_IAM_ROLE})


@dataclasses.dataclass(frozen=True)
class OrphanResource:
    kind: str
    identifier: str
    classification: str  # "dangling" (no owner row at all)


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


def scan_orphans(*, kinds: set[str] | None = None) -> OrphanReport:
    """Enumerate platform-owned cloud resources and report those with no
    live owning DB row. Pure detection — no deletes."""
    from _sdk import UnsupportedOperationError
    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import driver_for_capability, workload_identity_role_name

    kinds = (kinds or set(ALL_KINDS)) & ALL_KINDS
    orphans: list[OrphanResource] = []
    scanned: list[str] = []
    incomplete: list[str] = []

    managed_clusters = list(
        TenantCluster.objects.filter(
            deleted_at__isnull=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ),
    )

    if KIND_IAM_ROLE in kinds:
        # Forward mapping: the role name every LIVE app is expected to own.
        # IAM roles are account-scoped, so we union owned roles across the
        # managed clusters and diff once against all live apps.
        live_role_names = {
            workload_identity_role_name(app)
            for app in RegisteredApp.objects.filter(
                deleted_at__isnull=True,
            ).select_related("organization")
        }
        owned: set[str] = set()
        enumerated = False
        errored = False
        for cluster in managed_clusters:
            try:
                driver = driver_for_capability(cluster, "identity")
                owned.update(driver.list_owned_roles())
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
            for name in sorted(owned - live_role_names):
                orphans.append(OrphanResource(KIND_IAM_ROLE, name, "dangling"))
        else:
            # Couldn't fully enumerate (no supporting driver, or a list
            # error) — report incomplete rather than implying clean.
            incomplete.append(KIND_IAM_ROLE)

    return OrphanReport(
        orphans=orphans,
        scanned_kinds=scanned,
        incomplete_kinds=incomplete,
    )
