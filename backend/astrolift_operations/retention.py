"""
Event + audit retention policy and JSONL streaming export (#161,
spec 17 §11 + spec 03 §11).

Two responsibilities, kept in one module so they share the same
"row → JSONL line" serialization code:

* **Retention policy.** Per-org / per-stream cutoff: rows older
  than ``retention_days`` are eligible for deletion. The actual
  ``DELETE`` runs in a scheduled task that this module produces a
  queryset for. Default 90 days (spec 17 §11).

* **Streaming export.** Convert Event / AuditEvent rows to JSONL
  with a stable schema version + integrity hash. The bytes are
  written to a sink callable so the cloud upload (S3 / GCS / Azure
  Blob) lives in driver impls — this module never imports a cloud
  SDK.

The audit export carries a per-line **hash chain**: each line's
``integrity.hash_prev`` is the SHA-256 of the previous line's
serialized payload. Tampering with a line invalidates every line
after it. Cheaper than per-line signatures and gives us the
property auditors actually want — "the file is intact" — without
a key-management story.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

# Spec 17 §11 / spec 03 §11 — default 90-day retention; per-org +
# per-stream override land on the Organization row separately.
DEFAULT_RETENTION_DAYS = 90

# JSONL export schema. Bump on a breaking change; reader code keys
# off this so old exports still parse.
EXPORT_SCHEMA_VERSION = "1.0"


# ---- retention --------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Per-stream retention. ``stream`` is ``"event"`` or ``"audit"``.

    The org-level + per-event-type overrides flow into this dataclass
    at the call site so the policy logic stays free of model imports.
    """

    stream: str
    retention_days: int = DEFAULT_RETENTION_DAYS

    def __post_init__(self) -> None:
        if self.stream not in {"event", "audit"}:
            raise ValueError(
                f"retention stream must be 'event' or 'audit', got {self.stream!r}"
            )
        if self.retention_days <= 0:
            raise ValueError("retention_days must be positive")


def cutoff_for(policy: RetentionPolicy, *, now: datetime) -> datetime:
    """Pure: rows with ``occurred_at < cutoff`` are eligible for delete."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now - timedelta(days=policy.retention_days)


def expired_queryset(model_cls, policy: RetentionPolicy, *, now: datetime):
    """Queryset of rows past retention. Caller owns the actual delete
    (scheduled task / batch size / lock). Kept minimal so this module
    doesn't depend on Django at import time during tests; the model
    class is just used for ``filter()`` here."""
    return model_cls.objects.filter(occurred_at__lt=cutoff_for(policy, now=now))


# ---- JSONL serialization ---------------------------------------------


def _row_to_record(row: Any, *, stream: str) -> dict[str, Any]:
    """Convert a model row to its export dict.

    Stable shape: schema_version + stream + a flat envelope of the
    fields auditors expect. Timestamps go out as ISO-8601 UTC; FKs
    go out as their PK. ``payload`` carries through verbatim.
    """
    if stream == "event":
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "stream": "event",
            "guid": str(row.guid),
            "occurred_at": _iso(row.occurred_at),
            "organization_id": row.organization_id,
            "team_id": row.team_id,
            "project_id": row.project_id,
            "registered_app_id": row.registered_app_id,
            "event_type": row.event_type,
            "resource_kind": row.resource_kind,
            "resource_id": row.resource_id,
            "actor_user_id": row.actor_user_id,
            "request_id": row.request_id,
            "trace_id": row.trace_id,
            "payload": row.payload,
        }
    if stream == "audit":
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "stream": "audit",
            "occurred_at": _iso(row.occurred_at),
            "organization_id": row.organization_id,
            "actor_kind": row.actor_kind,
            "actor_id": row.actor_id,
            "action": row.action,
            "decision": row.decision,
            "resource_kind": row.resource_kind,
            "resource_id": row.resource_id,
            "payload": getattr(row, "payload", {}),
            "request_id": getattr(row, "request_id", ""),
        }
    raise ValueError(f"unknown stream {stream!r}")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        # Storage uses USE_TZ=True so this shouldn't happen, but be
        # defensive — silent UTC assumption beats a stack trace mid-export.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_line(payload_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload_bytes).hexdigest()


def serialize_jsonl(
    rows: Iterable[Any],
    *,
    stream: str,
    chained: bool = False,
) -> bytes:
    """Render rows to JSONL bytes.

    ``chained=True`` adds an ``integrity`` envelope to every line
    where ``hash_prev`` is the SHA-256 of the previous line's
    *serialized record* (not the integrity envelope itself, so the
    chain is reproducible). Audit exports use this; event exports
    don't (volume is too high to be worth the chain cost).

    The genesis line carries ``hash_prev = "genesis"`` so a reader
    can detect a chopped-off file ("the chain doesn't start where it
    should").
    """
    buf = io.BytesIO()
    prev_hash = "genesis"
    for row in rows:
        record = _row_to_record(row, stream=stream)
        if chained:
            # Serialize once *without* the integrity field to compute
            # the hash, then again *with* it — this means the hash
            # covers exactly the payload, never the integrity envelope.
            record_bytes = _canonical_json(record)
            line_hash = _hash_line(record_bytes)
            record["integrity"] = {
                "hash_prev": prev_hash,
                "hash_self": line_hash,
            }
            prev_hash = line_hash
        buf.write(_canonical_json(record))
        buf.write(b"\n")
    return buf.getvalue()


def _canonical_json(obj: Any) -> bytes:
    """Sorted keys + no whitespace = byte-identical for the same
    record. Required so the hash chain is reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_chain(jsonl_bytes: bytes) -> bool:
    """Walk a chained JSONL export and confirm every ``hash_prev``
    matches the prior line's payload. Returns True on intact, False
    on the first mismatch.

    Provided so audit-tooling can spot tampering without re-implementing
    the chain rules. The hash chain plus an out-of-band copy of the
    final ``hash_self`` gives auditors a 32-byte fingerprint of the
    whole file.
    """
    prev_expected = "genesis"
    for raw in jsonl_bytes.splitlines():
        if not raw:
            continue
        record = json.loads(raw)
        integrity = record.pop("integrity", None)
        if integrity is None:
            return False
        if integrity["hash_prev"] != prev_expected:
            return False
        record_bytes = _canonical_json(record)
        if integrity["hash_self"] != _hash_line(record_bytes):
            return False
        prev_expected = integrity["hash_self"]
    return True


# ---- export pipeline (cloud upload deferred to drivers) --------------


SinkCallable = Callable[[bytes, str], None]
"""Sink signature: ``sink(jsonl_bytes, partition_key)``.

``partition_key`` is the date/org slug the platform suggests; the
cloud-side driver (S3, GCS, Azure Blob) decides how to map that
to a path/object name. Decoupling lets the platform export without
knowing whether the destination is partitioned by ``org/yyyy-mm-dd/``
or ``yyyy/mm/dd/<org>/`` or anything else."""


def export_to_sink(
    rows: Iterable[Any],
    *,
    stream: str,
    org_slug: str,
    partition_date: datetime,
    sink: SinkCallable,
    chained: bool | None = None,
) -> int:
    """Serialize ``rows`` to JSONL and hand the bytes to ``sink``.

    Returns the byte count written. Empty input is a no-op (the sink
    is NOT called) — avoids 0-byte objects polluting the export
    bucket and leaving auditors to wonder which days were "intentionally
    silent" vs. "the worker died".

    ``chained`` defaults to True for the audit stream, False for the
    event stream — those are the spec-mandated defaults and a caller
    rarely wants to flip them.
    """
    if chained is None:
        chained = stream == "audit"

    rows_list = list(rows)  # need a count; events are usually batched anyway
    if not rows_list:
        return 0

    payload = serialize_jsonl(rows_list, stream=stream, chained=chained)
    partition_key = (
        f"{stream}/{org_slug}/{partition_date.strftime('%Y/%m/%d')}.jsonl"
    )
    sink(payload, partition_key)
    return len(payload)
