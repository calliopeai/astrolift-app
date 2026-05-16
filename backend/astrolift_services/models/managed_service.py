"""
ManagedService — a platform-provisioned dependency for an app.

The ``kind`` enum is portable across clouds (postgres, redis,
object_store, …); ``variant`` selects the plugin-specific shape
(rds, aurora_serverless_v2, cloudsql, …) and is matched against the
``ManagedServiceCatalogEntry`` rows registered by provider plugins.

The status field is descriptive only — provisioning is driven by
Temporal workflows that update this row.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ManagedService(BaseCoreModel):
    class Kind(models.TextChoices):
        POSTGRES = "postgres"
        REDIS = "redis"
        OBJECT_STORE = "object_store"
        QUEUE = "queue"
        TOPIC = "topic"
        KV_STORE = "kv_store"
        SEARCH = "search"
        MQ = "mq"
        NFS = "nfs"
        VECTOR_INDEX = "vector_index"
        TIME_SERIES = "time_series"
        DOCUMENT_DB = "document_db"
        EMAIL = "email"
        MODEL_ENDPOINT = "model_endpoint"

    class Status(models.TextChoices):
        PENDING = "pending"
        PROVISIONING = "provisioning"
        ACTIVE = "active"
        UPDATING = "updating"
        DEPROVISIONING = "deprovisioning"
        FAILED = "failed"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="managed_services",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="managed_services",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    name = models.CharField(max_length=128, blank=True, default="")
    variant = models.CharField(max_length=64, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    status_error = models.TextField(blank=True, default="")
    connection_secret_ref = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "kind", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="msvc_unique_active_per_app_kind_name",
            ),
        ]


class ManagedServiceBinding(BaseCoreModel):
    """Maps a managed service into the workload's environment as one
    or more env vars.
    """

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="bindings",
        on_delete=models.CASCADE,
    )
    env_key = models.CharField(max_length=128)
    env_value_ref = models.CharField(max_length=512)
    is_secret = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["managed_service", "env_key"],
                condition=models.Q(deleted_at__isnull=True),
                name="msvc_binding_env_unique_active",
            ),
        ]
