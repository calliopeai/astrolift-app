"""
Streaming audit-log export.

The :func:`stream_export` helper iterates an AuditEvent queryset and
writes a redacted CSV or NDJSON file to the platform's media root,
returning an :class:`ExportArtifact` carrying the on-disk path, byte
count, row count, and sha256 of the produced bytes. The caller is
responsible for persisting an :class:`AuditExport` row that references
the artifact and the single-use download token.

CSV columns are stable: callers can rely on the header row + ordering
for downstream tooling. NDJSON rows mirror the GraphQL
``AstroliftAuditEvent`` shape so a downstream pipeline can fan in
both export formats with one schema.

Sensitive values are scrubbed via :mod:`audit_redaction` before they
hit the file — exports never carry plaintext secrets even if the
underlying ``data`` JSONField does.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import os
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from astrolift_operations.audit_redaction import scrub_payload

EXPORT_SUBDIR = "audit_exports"

# CSV column order — stable contract for downstream tooling.
CSV_COLUMNS: tuple[str, ...] = (
    "occurred_at",
    "guid",
    "organization_id",
    "actor_kind",
    "actor_id",
    "actor_display",
    "action",
    "decision",
    "target_kind",
    "target_id",
    "target_slug",
    "request_id",
    "data",
)


@dataclasses.dataclass(slots=True)
class ExportArtifact:
    """Result of writing an audit export to disk.

    ``relative_path`` is rooted at ``MEDIA_ROOT`` (so the URL builder
    can prefix it with ``MEDIA_URL`` if it ever swaps to S3).
    ``sha256`` lets the recipient verify the download didn't get
    truncated or mangled in flight.
    """

    relative_path: str
    absolute_path: str
    byte_count: int
    row_count: int
    sha256: str
    format: str


def _ensure_export_dir() -> str:
    base = getattr(settings, "MEDIA_ROOT", None) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_audit_export_tmp"
    )
    target = os.path.join(base, EXPORT_SUBDIR)
    os.makedirs(target, exist_ok=True)
    return target


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        # Mirror the auditor-friendly Z-suffix shape used elsewhere in
        # the platform's exports.
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(value)


def _row_to_dict(row) -> dict[str, Any]:
    """Convert one ``AuditEvent`` ORM row into a JSON-serialisable
    dict with the data field redacted. Same shape used for both CSV
    and NDJSON output paths."""
    return {
        "guid": str(row.guid),
        "occurred_at": _iso(row.occurred_at),
        "organization_id": (str(row.organization_id) if row.organization_id else ""),
        "actor_kind": row.actor_kind or "",
        "actor_id": row.actor_id or "",
        "actor_display": row.actor_display or "",
        "action": row.action or "",
        "decision": row.decision or "",
        "target_kind": row.target_kind or "",
        "target_id": row.target_id or "",
        "target_slug": row.target_slug or "",
        "request_id": row.request_id or "",
        "data": scrub_payload(row.data or {}),
    }


def serialize_csv(rows: Iterable[Any]) -> tuple[bytes, int]:
    """Render ``rows`` as CSV bytes. Returns ``(bytes, row_count)``.

    ``data`` is serialized as a compact JSON string so the CSV stays
    flat — downstream tools that need structure can re-parse it.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    n = 0
    for row in rows:
        record = _row_to_dict(row)
        writer.writerow(
            [
                record["occurred_at"],
                record["guid"],
                record["organization_id"],
                record["actor_kind"],
                record["actor_id"],
                record["actor_display"],
                record["action"],
                record["decision"],
                record["target_kind"],
                record["target_id"],
                record["target_slug"],
                record["request_id"],
                json.dumps(record["data"], separators=(",", ":"), sort_keys=True),
            ]
        )
        n += 1
    return buffer.getvalue().encode("utf-8"), n


def serialize_ndjson(rows: Iterable[Any]) -> tuple[bytes, int]:
    """Render ``rows`` as newline-delimited JSON. Returns ``(bytes, row_count)``."""
    buffer = io.BytesIO()
    n = 0
    for row in rows:
        record = _row_to_dict(row)
        buffer.write(json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        buffer.write(b"\n")
        n += 1
    return buffer.getvalue(), n


def write_artifact(
    rows: Iterable[Any],
    *,
    format: str,
    guid: str,
) -> ExportArtifact:
    """Serialize ``rows`` in ``format`` and write to MEDIA_ROOT.

    Returns the :class:`ExportArtifact` describing the produced file.
    Caller persists the AuditExport row referencing this artifact.
    """
    fmt = format.lower()
    if fmt == "csv":
        payload, row_count = serialize_csv(rows)
        extension = "csv"
    elif fmt == "ndjson":
        payload, row_count = serialize_ndjson(rows)
        extension = "ndjson"
    else:
        raise ValueError(f"unsupported format: {format!r}")

    target_dir = _ensure_export_dir()
    filename = f"{guid}.{extension}"
    absolute_path = os.path.join(target_dir, filename)
    with open(absolute_path, "wb") as fh:
        fh.write(payload)

    return ExportArtifact(
        relative_path=os.path.join(EXPORT_SUBDIR, filename),
        absolute_path=absolute_path,
        byte_count=len(payload),
        row_count=row_count,
        sha256=hashlib.sha256(payload).hexdigest(),
        format=fmt,
    )


def mint_token() -> tuple[str, str]:
    """Generate a fresh (plaintext, hash) token pair. Plaintext goes
    into the URL the caller hands to the client; hash goes in the DB
    so a database dump can't be replayed against the download view."""
    plaintext = secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, digest


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
