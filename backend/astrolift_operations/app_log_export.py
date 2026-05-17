"""
Streaming app-log export (#483).

Mirrors :mod:`audit_export` (#433) but writes container log lines
rather than ``AuditEvent`` rows. The :func:`stream_export` helper
fetches lines through the cluster ``ClusterDriver`` (same path the
``onAppLog`` subscription uses), applies in-memory filters
(level / regex), serializes to CSV / NDJSON / TXT, and writes the
file to ``MEDIA_ROOT/app_log_exports/<guid>.<ext>``.

The caller (the mutation resolver) is responsible for persisting an
:class:`AppLogExport` row that references the artifact + single-use
download token. The download view in :mod:`astrolift_operations.views`
gates the file by token and stamps ``consumed_at`` on first read.

Filters:

* ``level`` — case-insensitive substring match against the line
  message (``"ERROR"`` matches ``"error"`` and ``"ERROR"``). Most
  app log shapes interleave the level at the start of the message,
  which is what operators grep for.
* ``regex`` — Python regex over the message. Invalid regex is
  rejected at the mutation boundary, never reaches this module.

Sensitive values are NOT scrubbed here — container log output is
operator-controlled and the platform can't reliably know what's
sensitive in arbitrary app output. The download URL is single-use +
TTL-gated so a leaked URL has a bounded blast radius.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import os
import re
import secrets
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

EXPORT_SUBDIR = "app_log_exports"

# CSV column order — stable contract for downstream tooling.
CSV_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "pod_name",
    "container",
    "stream",
    "message",
)

VALID_FORMATS: frozenset[str] = frozenset({"csv", "ndjson", "txt"})


@dataclasses.dataclass(slots=True)
class ExportArtifact:
    """Result of writing an app-log export to disk.

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
    truncated: bool
    """True when the line cap was hit before the upstream stream ended.
    Caller surfaces this in the UI so the operator can narrow filters
    and retry instead of assuming the export captured everything."""


def _ensure_export_dir() -> str:
    base = getattr(settings, "MEDIA_ROOT", None) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_app_log_export_tmp"
    )
    target = os.path.join(base, EXPORT_SUBDIR)
    os.makedirs(target, exist_ok=True)
    return target


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        # Z-suffix shape matches the audit export so downstream
        # tooling can fan in both pipelines with one parser.
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(value) if value is not None else ""


def _line_to_dict(line) -> dict[str, Any]:
    """Normalise one ``PodLogLine`` (or duck-typed test fake) into a
    JSON-serialisable dict. Same shape used for both CSV and NDJSON
    output paths so a vendor handed both formats sees identical
    fields.
    """
    return {
        "timestamp": _iso(getattr(line, "timestamp", None)),
        "pod_name": str(getattr(line, "pod_name", "") or ""),
        "container": str(getattr(line, "container", "") or ""),
        "stream": str(getattr(line, "stream", "") or ""),
        "message": str(getattr(line, "message", "") or ""),
    }


def apply_filters(
    lines: Iterable[Any],
    *,
    level: str | None,
    regex: str | None,
    max_lines: int,
) -> Iterator[Any]:
    """Yield ``lines`` filtered by ``level`` / ``regex`` and capped
    at ``max_lines``. The cap is the upper-bound on lines RETURNED,
    not lines READ — once the consumer pulls ``max_lines`` items
    the iterator stops.

    ``level`` is a case-insensitive substring match against the
    message. ``regex`` is a Python regex; an invalid pattern raises
    :class:`re.error` and the caller turns that into a VALIDATION
    failure on the mutation.
    """
    level_needle = (level or "").strip().upper() or None
    pattern = re.compile(regex) if (regex or "").strip() else None

    yielded = 0
    for line in lines:
        if yielded >= max_lines:
            return
        message = str(getattr(line, "message", "") or "")
        if level_needle is not None and level_needle not in message.upper():
            continue
        if pattern is not None and pattern.search(message) is None:
            continue
        yield line
        yielded += 1


def serialize_csv(lines: Iterable[Any]) -> tuple[bytes, int]:
    """Render ``lines`` as CSV bytes. Returns ``(bytes, row_count)``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    n = 0
    for line in lines:
        record = _line_to_dict(line)
        writer.writerow(
            [
                record["timestamp"],
                record["pod_name"],
                record["container"],
                record["stream"],
                record["message"],
            ]
        )
        n += 1
    return buffer.getvalue().encode("utf-8"), n


def serialize_ndjson(lines: Iterable[Any]) -> tuple[bytes, int]:
    """Render ``lines`` as newline-delimited JSON. Returns ``(bytes, row_count)``."""
    buffer = io.BytesIO()
    n = 0
    for line in lines:
        record = _line_to_dict(line)
        buffer.write(json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        buffer.write(b"\n")
        n += 1
    return buffer.getvalue(), n


def serialize_txt(lines: Iterable[Any]) -> tuple[bytes, int]:
    """Render ``lines`` as raw text — one line per record, prefixed
    by an ISO-8601 timestamp + the pod/container in a familiar
    ``kubectl logs --timestamps`` shape. ``row_count`` is the line
    count for the cap check.
    """
    buffer = io.StringIO()
    n = 0
    for line in lines:
        record = _line_to_dict(line)
        prefix_parts: list[str] = []
        if record["timestamp"]:
            prefix_parts.append(record["timestamp"])
        if record["pod_name"]:
            container = record["container"]
            tag = f"[{record['pod_name']}/{container}]" if container else f"[{record['pod_name']}]"
            prefix_parts.append(tag)
        prefix = " ".join(prefix_parts)
        if prefix:
            buffer.write(prefix + " " + record["message"])
        else:
            buffer.write(record["message"])
        buffer.write("\n")
        n += 1
    return buffer.getvalue().encode("utf-8"), n


def _serializer_for(format: str):
    fmt = format.lower()
    if fmt == "csv":
        return serialize_csv, "csv"
    if fmt == "ndjson":
        return serialize_ndjson, "ndjson"
    if fmt == "txt":
        return serialize_txt, "log"
    raise ValueError(f"unsupported format: {format!r}")


def write_artifact(
    lines: Iterable[Any],
    *,
    format: str,
    guid: str,
    max_lines: int,
    level: str | None = None,
    regex: str | None = None,
) -> ExportArtifact:
    """Filter + serialize ``lines`` in ``format`` and write to
    ``MEDIA_ROOT/app_log_exports/<guid>.<ext>``.

    ``max_lines`` is enforced in-loop; the artifact's ``truncated``
    flag is set when at least one more filter-passing line was
    available after the cap was hit (i.e. the export honestly
    couldn't fit everything the caller asked for).

    Filtering happens here rather than via :func:`apply_filters` so
    we can detect truncation without losing items to a generator's
    look-ahead.
    """
    serializer, extension = _serializer_for(format)

    level_needle = (level or "").strip().upper() or None
    pattern = re.compile(regex) if (regex or "").strip() else None

    def _passes(line: Any) -> bool:
        message = str(getattr(line, "message", "") or "")
        if level_needle is not None and level_needle not in message.upper():
            return False
        if pattern is not None and pattern.search(message) is None:
            return False
        return True

    kept: list[Any] = []
    truncated = False
    for line in lines:
        if not _passes(line):
            continue
        if len(kept) < max_lines:
            kept.append(line)
            continue
        # We're past the cap and the source still has a filter-passing
        # line — flag truncation and stop reading. Anything beyond
        # this is wasted work for an export the caller already knows
        # they'll need to retry with a narrower filter.
        truncated = True
        break

    payload, row_count = serializer(kept)

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
        format=format.lower(),
        truncated=truncated,
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
