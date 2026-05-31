"""
Brief — a pre-assembled, content-hashed, object-storage-keyed package
that an agent fetches at boot time.

A Brief bundles resolved config (env vars, manifest overrides), secret
references (values are resolved by the Dispatch Service at spawn time),
skill references, and context metadata into one immutable unit.  Once
assembled the Brief is never mutated — any change requires a new Brief.

See issue #41 (Agent Dispatch Layer — Brief model).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Brief(BaseCoreModel):
    class Status(models.TextChoices):
        ASSEMBLING = "assembling"
        READY = "ready"
        REVOKED = "revoked"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="briefs",
        on_delete=models.CASCADE,
    )
    # Optional link to the app whose manifest snapshot was included.
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="briefs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # SHA-256 of the canonical JSON payload, hex-encoded.  Content-
    # addressed: two Briefs with identical payloads share a hash and
    # the assembly service can skip re-upload for an identical Brief.
    content_hash = models.CharField(max_length=64, unique=True)
    # Object-storage path (e.g. ``briefs/<org-guid>/<hash>.json``).
    storage_key = models.CharField(max_length=1024, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ASSEMBLING,
    )
    # Resolved config captured at assembly time — env var literals,
    # manifest overrides, feature flags.
    manifest_snapshot = models.JSONField(default=dict, blank=True)
    # Secret references (not values).  Each entry identifies a secret
    # that the Dispatch Service must resolve before spawning the agent.
    secrets_refs = models.JSONField(default=list, blank=True)
    # Context metadata: org, team, project slugs, task ID, actor info.
    context = models.JSONField(default=dict, blank=True)
    # How long (seconds) a READY Brief remains valid before the agent
    # bootstrap layer treats it as expired.  0 means no expiry.
    ttl_seconds = models.PositiveIntegerField(default=3600)
    assembled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "status"], name="brief_org_status_idx"),
            models.Index(fields=["content_hash"], name="brief_content_hash_idx"),
        ]

    def __str__(self) -> str:
        return f"Brief {self.content_hash[:12]} ({self.status})"
