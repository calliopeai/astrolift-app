"""
AppSecretMetadata — sidecar metadata for app-level secret literals (#677, #678).

App secrets live in the staged manifest text buffer (``RegisteredApp.manifest_raw_staged``),
not in a per-secret database row. The manifest is the system of record for the
key/value pairs; this table adds operator-facing metadata that the manifest can't
carry (expiry windows, where the value was set from).

Shape: one row per ``(registered_app, environment_name, key)`` triple. Rows are
allocated lazily on first metadata write — most secrets never need a row because
their UI rendering is fine without an explicit expiry / source override.

* ``expires_at`` is set by the operator (or by an integration that knows the
  upstream credential lifetime). When it is within 14 days the FE renders a
  "rotate now" warning; when null no expiry chip renders at all.

* ``source`` is the provenance of the most-recent write. Default ``web`` for
  rows created from the GraphQL set/rotate mutations; ``cli`` / ``env_paste``
  / ``bundle`` / ``managed_service`` are reserved for future writers and can
  be supplied explicitly via ``setAppSecretMetadata``.

* ``set_at`` records when the metadata row was last updated, distinct from the
  underlying secret's audit-log set timestamp — the FE renders "Set via CLI
  on May 12" by joining these.

Soft-deleted via ``BaseCoreModel``; we don't hard-delete metadata even when the
underlying key disappears from the manifest because re-adding the same key later
should not silently reuse stale metadata.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AppSecretMetadata(BaseCoreModel):
    class Source(models.TextChoices):
        WEB = "web"
        CLI = "cli"
        ENV_PASTE = "env_paste"
        BUNDLE = "bundle"
        MANAGED_SERVICE = "managed_service"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="secret_metadata",
        on_delete=models.CASCADE,
    )
    environment_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Environment scope for the metadata row. Empty string means "
            "'applies to every environment that surfaces this key' — used "
            "when the operator sets metadata before any per-env override "
            "has been authored."
        ),
    )
    key = models.CharField(max_length=255)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Operator-declared rotation deadline. Null means 'no declared "
            "expiry' — most app-level literals are evergreen. When this "
            "field is within 14 days the FE renders a 'rotate now' badge."
        ),
    )
    source = models.CharField(
        max_length=24,
        choices=Source.choices,
        default=Source.WEB,
        help_text=(
            "Where the most-recent write originated. 'web' is the default "
            "for rows created via the GraphQL set/rotate mutation; CLI / "
            ".env paste / bundle / managed-service writers populate the "
            "other values when they call setAppSecretMetadata explicitly."
        ),
    )
    set_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last time the metadata row was authored / refreshed. Distinct "
            "from the underlying secret's audit-log set timestamp — this "
            "tracks metadata edits, not value writes."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "environment_name", "key"],
                condition=models.Q(deleted_at__isnull=True),
                name="app_secret_metadata_unique_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["registered_app", "key"],
                name="app_secret_meta_app_key_idx",
            ),
        ]
