"""
Volume snapshot + backup policy (#106, spec 22 §5-6).

Pure-Python policy. The snapshot workflows (CSI snapshot,
scheduled policy, logical backup, Velero/Kasten cluster backup)
all consult this module for retention rules + per-kind backup
strategy selection.

Pairs with #20 lifecycle (managed-service snapshot/restore) and
#22 emit-all (Velero-style cluster restore).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import StrEnum


class SnapshotKind(StrEnum):
    """Three snapshot strategies; backup workflow picks based on
    what's being snapshotted."""

    CSI_VOLUME = "csi_volume"
    """k8s VolumeSnapshot CR — CSI-level. Cluster-bound; cannot
    cross-cluster restore in v1."""

    LOGICAL_BACKUP = "logical_backup"
    """Application-level dump (pg_basebackup, BGSAVE, etc.).
    Stored as object-store blob; portable across clusters."""

    VELERO_BUNDLE = "velero_bundle"
    """Velero / Kasten K10 — volume snapshot + k8s resources
    bundled. Used for cluster-level disaster recovery."""


# Logical-backup tooling per kind (spec 22 §6). Locked: each
# entry maps the abstract managed-service kind to its
# canonical dump command. Driver implementations execute these.
LOGICAL_BACKUP_TOOLING: dict[str, str] = {
    "postgres": "pg_basebackup",  # also pg_dump for one-shot
    "mysql": "mysqldump",
    "redis": "BGSAVE",
    "mq": "kafka-backup",  # vendor-specific; placeholder
    "object_store": "cross_bucket_replication",
    "document_db": "mongodump",
}


# ---- snapshot retention --------------------------------------------


_CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    """Per-volume scheduled snapshot rule."""

    schedule: str
    """Cron expression (5-field: minute hour dom mon dow)."""

    retention: int
    """Number of snapshots to keep before pruning oldest."""

    copy_to_object_store: bool = False
    """When True, after each CSI snapshot the workflow exports a
    logical/blob copy to the per-org object store. Required for
    cross-cluster recovery (CSI snapshots are cluster-bound)."""

    def __post_init__(self) -> None:
        if self.retention <= 0:
            raise ValueError("retention must be positive")
        if not _CRON_RE.match(self.schedule):
            raise ValueError(f"schedule {self.schedule!r} not a 5-field cron expression")


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """Minimum projection of a Snapshot row."""

    snapshot_id: str
    created_at_unix: int
    kind: SnapshotKind


def snapshots_to_prune(
    *,
    snapshots: Sequence[SnapshotRecord],
    retention: int,
) -> tuple[SnapshotRecord, ...]:
    """Return snapshots beyond the retention window, oldest first.

    Mirrors #20 ``snapshots_to_prune`` for managed-service
    snapshots — same shape, applied to volume snapshots here.
    """
    if retention <= 0:
        raise ValueError("retention must be positive")
    if not snapshots:
        return ()
    sorted_snaps = sorted(
        snapshots,
        key=lambda s: s.created_at_unix,
        reverse=True,
    )
    if len(sorted_snaps) <= retention:
        return ()
    return tuple(sorted_snaps[retention:])


# ---- restore validation --------------------------------------------


class RestoreError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreRequest:
    snapshot_id: str
    snapshot_cluster_id: int
    target_cluster_id: int
    snapshot_kind: SnapshotKind


def assert_restore_valid(req: RestoreRequest) -> None:
    """v1 invariant: CSI volume snapshots are cluster-bound; you
    can't restore one to a different cluster. Logical backups +
    Velero bundles can cross clusters.

    Workflow checks this before allocating restore resources to
    avoid wasted setup."""
    if req.snapshot_kind == SnapshotKind.CSI_VOLUME:
        if req.snapshot_cluster_id != req.target_cluster_id:
            raise RestoreError(
                f"CSI volume snapshot {req.snapshot_id!r} cannot "
                f"restore across clusters "
                f"({req.snapshot_cluster_id} -> {req.target_cluster_id}); "
                "use logical_backup or velero_bundle for "
                "cross-cluster restore"
            )


# ---- per-kind backup strategy --------------------------------------


def backup_tool_for(kind: str) -> str:
    """The logical backup tool for a managed service kind. Empty
    string when the platform doesn't ship a logical backup for
    this kind (caller falls back to CSI snapshot only)."""
    return LOGICAL_BACKUP_TOOLING.get(kind, "")


def supports_logical_backup(*, kind: str) -> bool:
    """True iff the platform has a logical-backup tool for this
    kind."""
    return bool(backup_tool_for(kind))
