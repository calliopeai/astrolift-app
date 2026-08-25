"""Archive audit events past retention to the install's blob store (#1594).

`retention.py` has carried a JSONL exporter with a per-line SHA-256 hash
chain, and `AuditExport` has carried a row to record one, since #161. Nothing
called either. Meanwhile `prune_audit_log` runs daily, is described as
"Delete audit log past retention", and cannot delete: migration 0003 binds a
`BEFORE UPDATE OR DELETE` trigger to `astrolift_operations_auditevent` per
spec/04 §1 principle 7, so Postgres refuses.

**This module archives. It does not delete, and nothing here weakens the
append-only guarantee.**

That is deliberate rather than incomplete. #1594 records the contradiction
between "retained for N days" in the UI and append-only at the storage layer,
and resolving it is a compliance-posture decision:

* retention wins -- give the sweep a privileged path and restate the
  guarantee as "append-only except scheduled retention"
* append-only wins -- drop the schedule and the retention setting, and
  change the UI copy
* export then delete -- archive off-platform first, then delete through a
  privileged path

Only the third needs an archive, but the archive is useful under the first
too, and harmless under the second. So it is buildable ahead of the decision
in a way the delete half is not: adding a privileged DELETE path would settle
the question by making it, which is not this module's call.

**Off by default.** `Organization.audit_export_enabled` gates it per org.
An archive leaves the platform's storage for wherever the install points its
blob store, so an operator has to opt in rather than discover it happened.

**Where it lands.** Through `core.blob_store_resolution.install_s3_driver`,
the same resolver artifacts and agent snapshots use. That honours
`AWS_S3_ENDPOINT_URL`, so MinIO, SeaweedFS, Ceph RGW and GCS's S3-compatible
API all work; Azure Blob does not, and #1610 tracks genuine multicloud
resolution.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AuditArchiveError(RuntimeError):
    """The archive could not be written.

    Distinct from a missing blob store, which is not an error: an org with
    export disabled, or an install with no bucket, is a supported state and
    archiving is skipped rather than failed.
    """


def archive_expired_audit_events(org, *, now, retention_days: int) -> dict[str, Any]:
    """Write ``org``'s expired audit events to the blob store as JSONL.

    Returns a summary dict. Never deletes. Returns ``{"skipped": ...}``
    rather than raising when the org has not opted in or the install has no
    blob store, because both are ordinary states rather than failures.

    The export is **hash-chained**: `serialize_jsonl(chained=True)` gives
    each line an `integrity.hash_prev` of the previous line's payload, so
    tampering with one line invalidates every line after it. That property is
    the reason an audit archive is worth more than a database backup, and it
    is why the chain is not optional here even though `export_to_sink` would
    let a caller turn it off.
    """
    from astrolift_operations.models import AuditEvent
    from astrolift_operations.retention import (
        RetentionPolicy,
        cutoff_for,
        expired_queryset,
        export_to_sink,
    )

    if not getattr(org, "audit_export_enabled", False):
        return {"skipped": "export not enabled for this organization", "bytes": 0, "rows": 0}

    from core.blob_store_resolution import install_s3_driver

    driver = install_s3_driver(purpose="audit archive")
    if driver is None:
        # Not an error: an install with no bucket simply has nowhere to put
        # this. Loud enough to find, because an operator who switched the
        # flag on expects something to happen.
        logger.warning(
            "audit archive: %s has audit_export_enabled but the install has no blob store; "
            "set AWS_STORAGE_BUCKET_NAME",
            getattr(org, "slug", org),
        )
        return {"skipped": "no blob store configured", "bytes": 0, "rows": 0}

    policy = RetentionPolicy(stream="audit", retention_days=max(1, int(retention_days)))
    cutoff = cutoff_for(policy, now=now)
    rows = list(
        expired_queryset(AuditEvent, policy, now=now)
        .filter(organization_id=org.pk)
        .order_by("occurred_at", "pk")
    )
    if not rows:
        return {"skipped": "nothing past retention", "bytes": 0, "rows": 0}

    written: dict[str, Any] = {}

    def _sink(payload: bytes, partition_key: str) -> None:
        written["key"] = partition_key
        driver.upload(partition_key, payload, content_type="application/x-ndjson")

    try:
        byte_count = export_to_sink(
            rows,
            stream="audit",
            org_slug=str(getattr(org, "slug", "") or org.pk),
            partition_date=now,
            sink=_sink,
            chained=True,
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type below
        raise AuditArchiveError(
            f"audit archive failed for organization {getattr(org, 'slug', org)!r}: {exc}",
        ) from exc

    _record_archive(org, key=written.get("key", ""), rows=rows, byte_count=byte_count, cutoff=cutoff)

    logger.info(
        "audit archive: wrote %d event(s) (%d bytes) for %s to %s",
        len(rows),
        byte_count,
        getattr(org, "slug", org),
        written.get("key", ""),
    )
    return {
        "rows": len(rows),
        "bytes": byte_count,
        "key": written.get("key", ""),
        # The exact rows that are now safely off-platform. The pruner deletes
        # these and only these -- never a re-query -- so an event written
        # between the archive and the delete cannot be removed unarchived.
        "archived_pks": [r.pk for r in rows],
    }


def prune_archived_audit_events(pks: list[int]) -> int:
    """Delete audit events that have already been archived (#1594).

    The delete half of export-then-delete. Refuses to run on anything it was
    not handed: ``pks`` comes from :func:`archive_expired_audit_events`'s
    return value, which is the set of rows actually written to the blob
    store and recorded in ``AuditArchive``. Re-querying for expired rows here
    instead would delete anything that aged past the cutoff between the two
    steps, without a copy.

    The gate is opened and closed explicitly, in a ``finally``, rather than
    left to ``SET LOCAL``'s transaction scope. That scope is the *outermost*
    transaction, not the nearest ``atomic()`` block, so under any outer
    transaction -- a nested ``atomic()``, ``ATOMIC_REQUESTS``, or a test's
    wrapper -- a gate left to unwind on its own stays open for everything
    that follows on that connection. A test caught exactly that here.
    Closing it by hand makes the window this function's own, whatever it is
    nested inside.

    UPDATE stays refused throughout. The trigger only honours the gate for
    DELETE, because retention is about how long a record is kept and never
    about editing one.
    """
    if not pks:
        return 0

    from django.db import connection, transaction

    from astrolift_operations.models import AuditEvent

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL astrolift.retention_sweep = 'on'")
        try:
            deleted, _ = AuditEvent.objects.filter(pk__in=pks).delete()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL astrolift.retention_sweep = 'off'")

    logger.info("audit retention: deleted %d archived event(s)", deleted)
    return int(deleted)


def _record_archive(org, *, key: str, rows: list, byte_count: int, cutoff) -> None:
    """Record the archive in ``AuditArchive``.

    Not ``AuditExport``: that model is a token-gated, expiring, user-requested
    download and #1594's claim that it "already exists for exactly this" does
    not survive reading it. A retention archive is machine-written, expires
    never, and must outlive every token.

    Best-effort. An archive that was written but not recorded is recoverable
    -- the object is in the bucket -- whereas failing the activity here would
    re-export the same window tomorrow and leave a duplicate behind.
    """
    import hashlib

    from astrolift_operations.models import AuditArchive
    from astrolift_operations.retention import serialize_jsonl

    try:
        digest = hashlib.sha256(serialize_jsonl(rows, stream="audit", chained=True)).hexdigest()
        AuditArchive.objects.create(
            organization=org,
            object_key=key,
            row_count=len(rows),
            byte_count=byte_count,
            sha256=digest,
            covered_through=cutoff,
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit archive: wrote %s but could not record the AuditArchive row", key)
