"""
Workload — a runtime kind under a RegisteredApp.

A registered app has one or more workloads (deployment, statefulset,
job, cronjob). The workload row is the *static* description (replicas,
resource asks, HPA targets); the runtime state lives on Deployment.

``is_public`` selects which workload(s) get an ingress.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Workload(NamedBaseCoreModel):
    class Kind(models.TextChoices):
        DEPLOYMENT = "deployment"
        STATEFULSET = "statefulset"
        JOB = "job"
        CRONJOB = "cronjob"
        # One-shot ``batch/v1 Job`` (runs once, no schedule). Distinct
        # from the legacy ``JOB`` choice — ``task`` is the kind the
        # manifest parser / renderer support end-to-end.
        TASK = "task"
        # Long-running AI agent (#795). Renders to the same K8s shape as
        # ``DEPLOYMENT`` (Deployment + Service + HPA) but carries agent
        # dispatch tuning and an ``astrolift.dev/workload-kind: agent``
        # pod annotation; per-dispatch results land on AgentRun (#804).
        AGENT = "agent"
        # Temporal workflow worker (#796). Renders to the same K8s shape
        # as ``DEPLOYMENT`` (Deployment + Service + HPA) but carries an
        # ``astrolift.dev/workload-kind: workflow`` pod annotation and
        # injected ``ASTROLIFT_WORKFLOW_TYPE`` / ``ASTROLIFT_TASK_QUEUE`` /
        # ``TEMPORAL_NAMESPACE`` env so the worker registers against the
        # platform Temporal cluster. Distinct from ``WorkflowDefinition``
        # (the BUILD-concern state-machine model) — this is the worker
        # process deployment. The DB migration for all new Kind values
        # (TASK/AGENT/WORKFLOW/FUNCTION) lands together in #805.
        WORKFLOW = "workflow"
        FUNCTION = "function"

    class ConcurrencyPolicy(models.TextChoices):
        # Mirrors Kubernetes ``CronJob.spec.concurrencyPolicy``:
        #   forbid  → ``Forbid``  (skip the next firing while one runs)
        #   queue   → ``Allow``   (let overlapping runs stack)
        #   replace → ``Replace`` (kill the in-flight run, start fresh)
        # Only meaningful when ``kind == CRONJOB``; deployment / job /
        # statefulset rows ignore the field. Default ``forbid`` matches
        # the platform's manifest renderer pre-#427 behaviour so the
        # migration is a no-op for every existing cronjob.
        FORBID = "forbid"
        QUEUE = "queue"
        REPLACE = "replace"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="workloads",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.DEPLOYMENT)
    is_public = models.BooleanField(default=False)
    schedule = models.CharField(max_length=64, blank=True, default="")
    concurrency_policy = models.CharField(
        max_length=16,
        choices=ConcurrencyPolicy.choices,
        default=ConcurrencyPolicy.FORBID,
    )
    notify_on_failure = models.BooleanField(default=True)

    replicas = models.PositiveIntegerField(default=1)
    # Replica count snapshotted when the app is archived (#743) so
    # restoreApp can bring workloads back to their pre-archive state.
    # Null means this workload was never archived.
    pre_archive_replicas = models.PositiveIntegerField(null=True, blank=True)
    cpu_request = models.CharField(max_length=32, blank=True, default="")
    cpu_limit = models.CharField(max_length=32, blank=True, default="")
    memory_request = models.CharField(max_length=64, blank=True, default="")
    memory_limit = models.CharField(max_length=64, blank=True, default="")

    hpa_min_replicas = models.PositiveIntegerField(null=True, blank=True)
    hpa_max_replicas = models.PositiveIntegerField(null=True, blank=True)
    hpa_target_cpu_pct = models.PositiveIntegerField(default=80)

    storage_class = models.CharField(max_length=64, blank=True, default="")
    storage_size = models.CharField(max_length=32, blank=True, default="")

    # Volume declarations from the manifest (#739). Stored as the raw
    # list of volume-config dicts so the workload-detail page can render
    # volume cards without re-parsing the TOML. Each dict mirrors the
    # TOML ``[[workloads.<name>.volumes]]`` shape.
    volumes = models.JSONField(default=list, blank=True)

    # Agent dispatch tuning (#795). Only meaningful when
    # ``kind == AGENT``; other kinds carry the defaults and ignore them.
    # ``max_retries`` / ``tool_timeout_seconds`` are injected into the
    # agent pod as ``ASTROLIFT_MAX_RETRIES`` / ``ASTROLIFT_TOOL_TIMEOUT``
    # env vars by the renderer; ``result_ttl_hours`` governs how long an
    # AgentRun result is retained (#804). Defaults mirror the manifest
    # spec, so a deployment/task/cronjob row keeps them at the no-op
    # values and the migration is a no-op for existing rows.
    max_retries = models.PositiveIntegerField(default=5)
    tool_timeout_seconds = models.PositiveIntegerField(default=300)
    result_ttl_hours = models.PositiveIntegerField(default=72)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="workload_slug_unique_active_per_app",
            ),
        ]
        indexes = [
            models.Index(fields=["registered_app", "kind"], name="workload_app_kind_idx"),
        ]
