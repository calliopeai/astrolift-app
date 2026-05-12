"""Tests for volume snapshot + backup policy (#106, spec 22 §5-6)."""

from __future__ import annotations

import pytest

from astrolift_drivers.volume_snapshot import (
    LOGICAL_BACKUP_TOOLING,
    RestoreError,
    RestoreRequest,
    SnapshotKind,
    SnapshotPolicy,
    SnapshotRecord,
    assert_restore_valid,
    backup_tool_for,
    snapshots_to_prune,
    supports_logical_backup,
)

# ---- snapshot policy guards ----------------------------------------


def test_policy_rejects_zero_retention():
    with pytest.raises(ValueError, match="retention"):
        SnapshotPolicy(schedule="0 3 * * *", retention=0)


def test_policy_rejects_invalid_cron():
    with pytest.raises(ValueError, match="schedule"):
        SnapshotPolicy(schedule="not a cron", retention=7)


def test_valid_policy():
    p = SnapshotPolicy(
        schedule="0 3 * * *",
        retention=7,
        copy_to_object_store=True,
    )
    assert p.schedule == "0 3 * * *"
    assert p.retention == 7
    assert p.copy_to_object_store is True


# ---- pruning -------------------------------------------------------


def _snap(snap_id: str, ts: int, kind: SnapshotKind = SnapshotKind.CSI_VOLUME) -> SnapshotRecord:
    return SnapshotRecord(snapshot_id=snap_id, created_at_unix=ts, kind=kind)


def test_no_pruning_when_under_retention():
    snaps = [_snap(f"s{i}", i) for i in range(3)]
    assert snapshots_to_prune(snapshots=snaps, retention=7) == ()


def test_prune_oldest_beyond_retention():
    snaps = [_snap("s1", 100), _snap("s2", 200), _snap("s3", 300), _snap("s4", 400), _snap("s5", 500)]
    out = snapshots_to_prune(snapshots=snaps, retention=3)
    pruned_ids = [s.snapshot_id for s in out]
    # Newest 3 kept (s5/s4/s3); s1/s2 pruned, oldest first
    assert pruned_ids == ["s2", "s1"]


def test_prune_empty_when_no_snapshots():
    assert snapshots_to_prune(snapshots=[], retention=7) == ()


def test_prune_invalid_retention_rejected():
    with pytest.raises(ValueError):
        snapshots_to_prune(snapshots=[_snap("s1", 1)], retention=0)


# ---- restore validation --------------------------------------------


def test_csi_snapshot_cant_cross_cluster():
    """v1 invariant: CSI volume snapshots are cluster-bound."""
    req = RestoreRequest(
        snapshot_id="snap-1",
        snapshot_cluster_id=1,
        target_cluster_id=2,
        snapshot_kind=SnapshotKind.CSI_VOLUME,
    )
    with pytest.raises(RestoreError, match="cannot restore across clusters"):
        assert_restore_valid(req)


def test_csi_snapshot_same_cluster_ok():
    req = RestoreRequest(
        snapshot_id="snap-1",
        snapshot_cluster_id=1,
        target_cluster_id=1,
        snapshot_kind=SnapshotKind.CSI_VOLUME,
    )
    assert_restore_valid(req)  # no raise


def test_logical_backup_can_cross_cluster():
    """Logical backups are object-store blobs; portable."""
    req = RestoreRequest(
        snapshot_id="dump-1",
        snapshot_cluster_id=1,
        target_cluster_id=2,
        snapshot_kind=SnapshotKind.LOGICAL_BACKUP,
    )
    assert_restore_valid(req)  # no raise


def test_velero_bundle_can_cross_cluster():
    """Velero bundles are designed for cluster-level DR."""
    req = RestoreRequest(
        snapshot_id="velero-1",
        snapshot_cluster_id=1,
        target_cluster_id=99,
        snapshot_kind=SnapshotKind.VELERO_BUNDLE,
    )
    assert_restore_valid(req)


# ---- per-kind backup tooling ---------------------------------------


def test_logical_backup_tooling_locked():
    """Lock-tested vocabulary so adding a kind requires a
    deliberate code-review entry."""
    assert LOGICAL_BACKUP_TOOLING["postgres"] == "pg_basebackup"
    assert LOGICAL_BACKUP_TOOLING["mysql"] == "mysqldump"
    assert LOGICAL_BACKUP_TOOLING["redis"] == "BGSAVE"
    assert LOGICAL_BACKUP_TOOLING["object_store"] == "cross_bucket_replication"


def test_supports_logical_backup_for_known_kinds():
    for kind in ("postgres", "mysql", "redis", "object_store", "document_db", "mq"):
        assert supports_logical_backup(kind=kind) is True


def test_supports_logical_backup_false_for_unknown():
    """Kinds without a logical-backup story (kv_store, search,
    vector_index, time_series, nfs, cdn, email, sms) get CSI
    snapshot only."""
    for kind in ("kv_store", "search", "vector_index", "time_series", "nfs", "cdn", "email", "sms"):
        assert supports_logical_backup(kind=kind) is False


def test_backup_tool_for_unknown_returns_empty():
    assert backup_tool_for("unknown-kind") == ""
