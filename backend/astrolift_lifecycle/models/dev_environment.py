"""
DevEnvironment — an ephemeral deploy environment for the Calliope App
Builder (#767, #768).

Files are pushed directly (no Git repo). The environment runs on a real
tenant cluster; a Temporal workflow provisions the K8s namespace,
artifact-backed Deployment, Service, and Ingress. Promote creates a
``RegisteredApp`` (with ``source_kind=direct_upload``) from the uploaded
files so the app can then go through the standard onboarding pipeline.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models.base import BaseCoreModel

# 24h default TTL. The builder UI keeps a session alive for as long as
# the user is iterating; on cancel/close the platform's preview-GC sweep
# tears down anything past ``ttl_until``.
DEV_ENV_TTL_HOURS = 24


def _default_dev_ttl_until():
    """Callable default for the ``ttl_until`` column.

    Defined at module scope (not as a field lambda) so the migration
    can serialize the default reference without dragging the whole
    model module into the migration file.
    """
    return timezone.now() + timedelta(hours=DEV_ENV_TTL_HOURS)


class DevEnvironment(BaseCoreModel):
    """Per-user ephemeral builder workspace bound to a tenant cluster."""

    class Status(models.TextChoices):
        CREATING = "creating"
        RUNNING = "running"
        SYNCING = "syncing"
        PROMOTING = "promoting"
        FAILED = "failed"
        TORN_DOWN = "torn_down"

    class Runtime(models.TextChoices):
        PYTHON = "python"
        NODE = "node"
        RUBY = "ruby"
        GO = "go"
        STATIC = "static"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="dev_environments",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="dev_environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="dev_environments",
        on_delete=models.CASCADE,
    )
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="dev_environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Project spec — denormalized on the row so the activity can build
    # manifests off a single Django fetch without joining a separate
    # spec table. Edits go through the sync endpoint; previous values
    # are not retained (this is throwaway state for iterative builder
    # sessions, not a deploy history).
    runtime = models.CharField(
        max_length=16,
        choices=Runtime.choices,
        default=Runtime.PYTHON,
    )
    runtime_version = models.CharField(max_length=32, blank=True, default="")
    start_command = models.CharField(max_length=512, blank=True, default="")
    port = models.PositiveIntegerField(default=8080)
    env_vars = models.JSONField(default=dict, blank=True)
    resource_profile = models.CharField(max_length=32, default="small")

    # File tree: ``{relative_path: content_string}``, or
    # ``{relative_path: {"content": <base64>, "encoding": "base64"}}`` for a
    # binary file (#1858). Capped by the operator's builder capacity in decoded
    # content at the API boundary.
    files = models.JSONField(default=dict, blank=True)

    # The one declared data file (#1858), e.g. an app's ``data.sqlite``. It is
    # included in the private artifact and seeded only into an empty data
    # volume. ``data_file_path`` is relative to that volume; empty means no
    # data file. Queries that do not render manifests defer ``data_file``.
    data_file_path = models.CharField(max_length=255, blank=True, default="")
    data_file = models.BinaryField(null=True, blank=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CREATING,
    )
    preview_url = models.URLField(blank=True, default="")
    namespace = models.CharField(max_length=128, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # Set non-null when the dev environment was promoted to a registered
    # production app. The dev env's status flips to PROMOTING while the
    # OnboardAppWorkflow runs against the new RegisteredApp.
    promoted_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_dev_environment",
    )

    ttl_until = models.DateTimeField(default=_default_dev_ttl_until)
    torn_down_at = models.DateTimeField(null=True, blank=True)

    workflow_run = models.ForeignKey(
        "astrolift_operations.WorkflowRun",
        related_name="dev_environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
