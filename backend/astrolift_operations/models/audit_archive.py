"""
AuditArchive — a record that an org's expired audit events were written
to the install's blob store (#1594).

Deliberately **not** ``AuditExport``. That model is a token-gated,
expiring, user-requested download: it carries ``token_hash``,
``expires_at`` and ``consumed_at``, lands under
``MEDIA_ROOT/audit_exports/``, and is swept once it expires. #1594's body
says ``AuditExport`` "already exists for exactly this", which reading the
model shows is not so -- a retention archive is machine-written, does not
expire, is nobody's download, and must outlive every token.

What this row is for: an auditor is handed the object key and the
``sha256``. Because ``retention.serialize_jsonl(chained=True)`` gives every
line an ``integrity.hash_prev`` of the previous line's payload, that single
digest is a fingerprint of the whole archive -- tampering with any line
invalidates every line after it. An out-of-band copy of the digest is then
enough to prove the file is intact, which is the property that makes an
archive worth more than a database backup.

Nothing here deletes. The append-only trigger on
``astrolift_operations_auditevent`` still refuses DELETE; #1594 carries
that decision.
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field


class AuditArchive(models.Model):
    guid = UUIDv7Field(unique=True, db_index=True)

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="audit_archives",
        on_delete=models.CASCADE,
    )

    #: Object key in the install's blob store, as handed to the sink.
    object_key = models.CharField(max_length=1024)

    row_count = models.IntegerField(default=0)
    byte_count = models.BigIntegerField(default=0)

    #: SHA-256 of the whole JSONL payload. With the per-line chain this is a
    #: 32-byte fingerprint of the archive; keep a copy out of band.
    sha256 = models.CharField(max_length=64, blank=True, default="")

    #: The retention cutoff this archive covered: every event in the file
    #: occurred before it. Recorded so a later run can tell what has already
    #: been archived without reading the bucket.
    covered_through = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"AuditArchive({self.organization_id}, {self.row_count} rows, {self.object_key})"
