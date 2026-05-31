"""Self-hosted runner model for Astrolift Pipelines (#81).

A Runner is a self-hosted agent process that executes pipeline jobs on
the operator's own infrastructure. Runners communicate with the Controller
via a poll-based protocol (see runner protocol #83).

Registration flow:
1. Operator runs `astro runner register` on the target host.
2. CLI calls POST /api/pipelines/v1/runners/register/ with a registration token.
3. Controller creates a Runner record, issues a scoped API key.
4. Runner polls for jobs, claims them, and reports completion.

A runner is distinct from a DispatcherInstance: runners run directly on
the target machine (no intermediary service), while Dispatcher Instances
are full service processes that can spawn multiple container jobs.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Runner(BaseCoreModel):
    class Status(models.TextChoices):
        OFFLINE = "offline", "Offline"
        IDLE = "idle", "Idle"
        ACTIVE = "active", "Active — running a job"
        SUSPENDED = "suspended", "Suspended"

    class OS(models.TextChoices):
        LINUX = "linux", "Linux"
        MACOS = "macos", "macOS"
        WINDOWS = "windows", "Windows"

    class Arch(models.TextChoices):
        AMD64 = "amd64", "x86-64 (amd64)"
        ARM64 = "arm64", "ARM64"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        related_name="runners",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)

    # Credentials
    registration_token_hash = models.CharField(max_length=64, blank=True, default="")
    api_key_hash = models.CharField(max_length=64, blank=True, default="")

    # Status
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.OFFLINE,
    )
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    # Hardware / platform
    os = models.CharField(max_length=16, choices=OS.choices, default=OS.LINUX)
    arch = models.CharField(max_length=16, choices=Arch.choices, default=Arch.AMD64)
    labels = models.JSONField(default=list)  # List of label strings for runs_on matching
    version_string = models.CharField(max_length=64, blank=True, default="")

    # Current job (null when idle)
    current_job_run = models.ForeignKey(
        "astrolift_pipelines.JobRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="claimed_by_runner",
    )

    class Meta:
        unique_together = [("organization", "slug")]
        indexes = [
            models.Index(fields=["organization", "status"]),
        ]

    def __str__(self) -> str:
        return f"Runner {self.slug} ({self.status})"

    def is_available(self) -> bool:
        """Return True if this runner can accept a new job."""
        return self.status == self.Status.IDLE and self.deleted_at is None
