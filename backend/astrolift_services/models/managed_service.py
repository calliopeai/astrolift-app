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
        MYSQL = "mysql"
        MSSQL = "mssql"
        REDIS = "redis"
        CACHE = "cache"
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
        CDN = "cdn"
        FAAS = "faas"
        API_GATEWAY = "api_gateway"
        EVENT_STREAM = "event_stream"
        FILESYSTEM = "filesystem"
        SMS = "sms"
        DATABASE_PROXY = "database_proxy"
        GRAPH_DB = "graph_db"
        WIDE_COLUMN = "wide_column"
        WAREHOUSE = "warehouse"
        EVENT_BUS = "event_bus"
        STREAM = "stream"
        WORKFLOW_ENGINE = "workflow_engine"
        ENCRYPTION_KEY = "encryption_key"
        PRIVATE_ENDPOINT = "private_endpoint"
        OBSERVABILITY = "observability"

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
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="managed_services",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="managed_services",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Owning project for a shared resource; null for app-private resources.",
    )
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="project_managed_services",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Provisioning target for a project-owned resource.",
    )
    environment_name = models.CharField(max_length=128, blank=True, default="")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    name = models.CharField(max_length=128, blank=True, default="")
    variant = models.CharField(max_length=64, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    status_error = models.TextField(blank=True, default="")
    connection_secret_ref = models.CharField(max_length=512, blank=True, default="")
    # Provider-side resource handle (e.g. "rds/<instance-id>",
    # "object_store/<bucket>") returned by the driver's provision() and
    # threaded back into deprovision() so teardown targets the exact
    # cloud resource. Empty until provisioning finalizes (#1002).
    backend_ref = models.CharField(max_length=512, blank=True, default="")
    # Per-service quick-action audit surface (#401). Captures the
    # most-recent operator-fired action against this service so the
    # Settings landing summary can render "tested 3m ago" / "revealed
    # 1h ago" without re-walking the platform audit log. The full audit
    # trail still lives in core.events; this column is the cached
    # latest-event for UI rendering only.
    last_action_at = models.DateTimeField(null=True, blank=True)
    last_action_kind = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        project__isnull=True,
                        tenant_cluster__isnull=True,
                        registered_app__isnull=False,
                        app_environment__isnull=False,
                    )
                    | models.Q(
                        project__isnull=False,
                        tenant_cluster__isnull=False,
                        registered_app__isnull=True,
                        app_environment__isnull=True,
                    )
                ),
                name="msvc_exactly_one_owner_scope",
            ),
            models.UniqueConstraint(
                fields=["registered_app", "kind", "name"],
                condition=models.Q(registered_app__isnull=False, deleted_at__isnull=True),
                name="msvc_unique_active_per_app_kind_name",
            ),
            models.UniqueConstraint(
                fields=["project", "kind", "name"],
                condition=models.Q(project__isnull=False, deleted_at__isnull=True),
                name="msvc_unique_active_per_project_kind_name",
            ),
        ]

    @property
    def owner_scope(self) -> str:
        return "project" if self.project_id else "app"

    @property
    def effective_cluster(self):
        if self.tenant_cluster_id:
            return self.tenant_cluster
        return self.app_environment.tenant_cluster if self.app_environment_id else None

    @property
    def effective_environment_name(self) -> str:
        if self.app_environment_id:
            return self.app_environment.name
        return self.environment_name or "default"


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


class ManagedServiceVolumeBinding(BaseCoreModel):
    """Durable, credential-free workload attachment for a filesystem."""

    class SourceKind(models.TextChoices):
        EXISTING_PVC = "existing_pvc"
        CSI = "csi"

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="volume_bindings",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=63)
    mount_path = models.CharField(max_length=512)
    sub_path = models.CharField(max_length=512, blank=True, default="")
    source_kind = models.CharField(max_length=32, choices=SourceKind.choices)
    protocol = models.CharField(max_length=32)
    claim_name = models.CharField(max_length=253, blank=True, default="")
    claim_namespace = models.CharField(max_length=253, blank=True, default="")
    csi_driver = models.CharField(max_length=253, blank=True, default="")
    volume_handle = models.CharField(max_length=1024, blank=True, default="")
    volume_attributes = models.JSONField(default=dict, blank=True)
    secret_refs = models.JSONField(default=dict, blank=True)
    mount_options = models.JSONField(default=list, blank=True)
    read_only = models.BooleanField(default=False)
    capacity = models.CharField(max_length=32, default="1Gi")
    access_modes = models.JSONField(default=list, blank=True)
    workload_names = models.JSONField(default=list, blank=True)
    container_names = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["managed_service", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="msvc_volume_name_unique_active",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source_kind="existing_pvc",
                        claim_name__gt="",
                        claim_namespace__gt="",
                        csi_driver="",
                        volume_handle="",
                    )
                    | models.Q(
                        source_kind="csi",
                        csi_driver__gt="",
                        volume_handle__gt="",
                        claim_name="",
                        claim_namespace="",
                    )
                ),
                name="msvc_volume_source_fields_valid",
            ),
        ]


class ManagedServiceAttachment(BaseCoreModel):
    """Attach one project-owned service to an app env or agent recipe."""

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="attachments",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="project_managed_service_attachments",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    agent_environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="project_managed_service_attachments",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(app_environment__isnull=False, agent_environment_spec__isnull=True)
                    | models.Q(app_environment__isnull=True, agent_environment_spec__isnull=False)
                ),
                name="msvc_attachment_exactly_one_consumer",
            ),
            models.UniqueConstraint(
                fields=["managed_service", "app_environment"],
                condition=models.Q(app_environment__isnull=False, deleted_at__isnull=True),
                name="msvc_attachment_unique_app_env",
            ),
            models.UniqueConstraint(
                fields=["managed_service", "agent_environment_spec"],
                condition=models.Q(agent_environment_spec__isnull=False, deleted_at__isnull=True),
                name="msvc_attachment_unique_agent_env",
            ),
        ]
