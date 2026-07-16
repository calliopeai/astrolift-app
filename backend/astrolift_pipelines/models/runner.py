"""
Runner — a self-hosted runner agent registered with the platform (#81).

Runners are operator-managed machines (Mac mini, Linux VM, bare metal,
cloud VM) that poll for pipeline jobs matching their labels. No K8s is
required on the runner host; the control plane dispatches jobs to
runners that advertise the right OS/arch/label set.

Registration flow:
1. Operator creates a Runner via UI or mutation → receives a one-time
   registration token (shown once).
2. Operator installs the runner agent and runs ``astro runner register``.
3. Agent authenticates with the token → token consumed, API key issued.
4. Agent begins polling (see ``/api/pipelines/v1/runners/claim/``).
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Runner(NamedBaseCoreModel):
    """A self-hosted runner agent registered to an organization."""

    class Status(models.TextChoices):
        OFFLINE = "offline", "Offline"
        IDLE = "idle", "Idle"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    class Os(models.TextChoices):
        LINUX = "linux", "Linux"
        MACOS = "macos", "macOS"
        WINDOWS = "windows", "Windows"

    class Arch(models.TextChoices):
        AMD64 = "amd64", "amd64 (x86_64)"
        ARM64 = "arm64", "arm64 (Apple Silicon / Graviton)"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="runners",
        on_delete=models.CASCADE,
    )

    # Authentication — registration token is hashed (SHA-256) and
    # consumed on first successful agent auth; api_key_hash is issued
    # after successful registration and carries the long-lived credential.
    # Both fields are null when not yet set or after consumption.
    registration_token_hash = models.CharField(  # noqa: DJ001 — null marks "consumed", distinct from ""
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
    )
    api_key_hash = models.CharField(  # noqa: DJ001 — null marks "not yet issued", distinct from ""
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OFFLINE,
        db_index=True,
    )
    os = models.CharField(max_length=16, choices=Os.choices)
    arch = models.CharField(max_length=8, choices=Arch.choices)

    # Arbitrary operator-defined labels used for ``runs_on`` job matching.
    # E.g. ["macos", "arm64", "xlarge", "self-hosted"]
    labels = models.JSONField(default=list, blank=True)

    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    # Set while the runner is executing a job; null when idle / offline.
    current_job_run = models.ForeignKey(
        "astrolift_pipelines.JobRun",
        related_name="executing_runners",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Agent version string reported at registration and heartbeat.
    version_string = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            # Slug is unique per org among non-deleted rows.
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="runner_slug_unique_per_org_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization_id})"
