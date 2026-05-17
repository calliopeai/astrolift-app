"""
AppLogExport — server-rendered, token-gated app-log download.

Mirrors :class:`AuditExport` (#433): the ``exportAstroliftAppLogs``
mutation streams the requested log slice into a file under
``MEDIA_ROOT/app_log_exports/<guid>.<ext>``, and access is gated by a
single-use token persisted on this row. The URL handed to a vendor or
auditor:

* expires at ``expires_at`` (Constance-tunable TTL).
* is single-format + single-app + single-env scoped — a leaked URL
  can't be replayed against a different slice.
* records ``consumed_at`` on the first download so the audit trail
  shows when the bytes left the platform.

The export is never replicated to ``AuditEvent`` storage — the
``exportAstroliftAppLogs`` mutation already emits an audit event via
the ``@mutation_audit`` decorator. Old expired rows are swept by a
future Temporal schedule; nothing else references this table.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.fields import UUIDv7Field


class AppLogExport(models.Model):
    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        NDJSON = "ndjson", "Newline-delimited JSON"
        TXT = "txt", "Raw text"

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    guid = UUIDv7Field(unique=True, db_index=True)

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="app_log_exports",
        on_delete=models.CASCADE,
    )

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="log_exports",
        on_delete=models.CASCADE,
    )

    # Environment is optional — the operator may want a multi-env
    # bundle. When set, the export was scoped to that env; when null,
    # all envs were considered.
    environment_name = models.CharField(max_length=128, blank=True, default="")

    # Pod / workload / container scoping. All optional; an empty
    # string means "no scoping on this axis".
    pod_name = models.CharField(max_length=255, blank=True, default="")
    workload_name = models.CharField(max_length=255, blank=True, default="")
    container = models.CharField(max_length=255, blank=True, default="")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="app_log_exports",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    format = models.CharField(max_length=16, choices=Format.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.READY,
    )

    row_count = models.IntegerField(default=0)
    byte_count = models.BigIntegerField(default=0)

    # Sha256 of the on-disk artifact. Lets the recipient verify the
    # download wasn't tampered with in flight — surfaced in the UI as
    # an "integrity hash" line beside the link.
    sha256 = models.CharField(max_length=64, blank=True, default="")

    # The relative path under MEDIA_ROOT (e.g. ``app_log_exports/<guid>.ndjson``).
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

    # Captured failure detail when status == FAILED. Empty on success.
    error_message = models.CharField(max_length=512, blank=True, default="")

    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-created_at"],
                name="app_log_export_org_time_idx",
            ),
            models.Index(
                fields=["registered_app", "-created_at"],
                name="app_log_export_app_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"AppLogExport {self.guid} ({self.format}, {self.row_count} lines)"
