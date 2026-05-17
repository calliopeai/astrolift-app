"""
AuditExport — server-rendered, token-gated audit log download.

When compliance asks to ship the audit log to a third party, the
operator hits the Export button on ``/audit`` and the GraphQL
``exportAuditEvents`` mutation streams the matching rows into a file
under ``MEDIA_ROOT/audit_exports/<guid>.csv|ndjson``. Access to the
file is gated by the token stored on this row, so an URL handed off
to an auditor:

* expires at ``expires_at`` (Constance-tunable TTL).
* is single-purpose: only the matching format + row count download.
* is tenant-scoped: the view rejects mismatched ``organization_id``.

The export is never replicated to ``Event``/``AuditEvent`` storage —
it lives in its own row keyed off ``token``. Old expired exports are
swept by a future Temporal schedule; nothing else references this
table.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.fields import UUIDv7Field


class AuditExport(models.Model):
    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        NDJSON = "ndjson", "Newline-delimited JSON"

    guid = UUIDv7Field(unique=True, db_index=True)

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="audit_exports",
        on_delete=models.CASCADE,
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="audit_exports",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    format = models.CharField(max_length=16, choices=Format.choices)
    row_count = models.IntegerField(default=0)
    byte_count = models.BigIntegerField(default=0)

    # Sha256 of the on-disk artifact. Lets the recipient verify the
    # download wasn't tampered with in flight — surfaced in the UI as
    # an "integrity hash" line beside the link.
    sha256 = models.CharField(max_length=64, blank=True, default="")

    # The relative path under MEDIA_ROOT (e.g. ``audit_exports/<guid>.csv``).
    # Storing a relative path keeps moves between MEDIA backends safe.
    relative_path = models.CharField(max_length=512)

    # The opaque single-use token surfaced in the download URL. Hashed
    # at the DB level (sha256 hex) so a DB dump doesn't leak download
    # access. ``mint_token`` returns the plaintext exactly once.
    token_hash = models.CharField(max_length=64, db_index=True)

    # Filters that produced this export. Snapshotted for the audit
    # trail so a later question of "what was exported?" is answered
    # by the row itself — the input filters can drift.
    filters_snapshot = models.JSONField(default=dict, blank=True)

    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="audit_export_org_time_idx"),
        ]

    def __str__(self) -> str:
        return f"AuditExport {self.guid} ({self.format}, {self.row_count} rows)"
