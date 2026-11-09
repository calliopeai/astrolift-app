"""Organization-owned NVKE authority and durable, encrypted operation records."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel, NamedBaseCoreModel


def _immutable(row, *fields):
    if row.pk is None:
        return
    original = type(row).all_objects.filter(pk=row.pk).values(*fields).first()

    def comparable(value):
        return bytes(value) if isinstance(value, (bytes, memoryview, bytearray)) else value

    if original and any(
        comparable(getattr(row, field)) != comparable(value) for field, value in original.items()
    ):
        raise ValueError("NVKE resource ownership and request identity cannot change")


class NvkeSite(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="nvke_sites"
    )
    organization_namespace = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True)
    upstream_revision = models.CharField(max_length=40, blank=True, default="")

    def save(self, *args, **kwargs):
        _immutable(self, "organization_id", "organization_namespace")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="nvke_site_org_slug_live",
            ),
        ]


class NvkeConnection(BaseCoreModel):
    class Surface(models.TextChoices):
        CONTROLLER = "controller"
        TENANT = "tenant"
        OPERATOR = "operator"
        DATASTORE = "datastore"

    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="connections")
    surface = models.CharField(max_length=16, choices=Surface.choices)
    endpoint = models.URLField(max_length=2048)
    is_enabled = models.BooleanField(default=True)
    allow_http = models.BooleanField(default=False)
    timeout_seconds = models.PositiveIntegerField(default=30)
    credential_backend_kind = models.CharField(max_length=32, blank=True, default="")
    credential_ciphertext = models.BinaryField(default=bytes)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_code = models.CharField(max_length=64, blank=True, default="")

    def save(self, *args, **kwargs):
        _immutable(self, "site_id", "surface")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "surface"],
                condition=models.Q(deleted_at__isnull=True),
                name="nvke_connection_surface_live",
            ),
            models.CheckConstraint(
                condition=models.Q(surface__in=["controller", "tenant", "operator", "datastore"]),
                name="nvke_connection_known_surface",
            ),
            models.CheckConstraint(
                condition=models.Q(timeout_seconds__gte=1, timeout_seconds__lte=300),
                name="nvke_connection_bounded_timeout",
            ),
        ]


class NvkeMetricsAuthority(BaseCoreModel):
    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="metrics_authorities")
    endpoint = models.URLField(max_length=2048)
    allow_http = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    timeout_seconds = models.PositiveIntegerField(default=30)
    credential_backend_kind = models.CharField(max_length=32, blank=True, default="")
    credential_ciphertext = models.BinaryField(default=bytes)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_code = models.CharField(max_length=64, blank=True, default="")

    def save(self, *args, **kwargs):
        _immutable(self, "site_id")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=models.Q(deleted_at__isnull=True), name="nvke_metrics_site_live"
            ),
            models.CheckConstraint(
                condition=models.Q(timeout_seconds__gte=1, timeout_seconds__lte=300),
                name="nvke_metrics_timeout",
            ),
        ]


class NvkeTemporalAuthority(BaseCoreModel):
    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="temporal_authorities")
    address = models.CharField(max_length=512)
    namespace = models.CharField(max_length=255)
    task_queue = models.CharField(max_length=255)
    server_name = models.CharField(max_length=253, blank=True, default="")
    use_tls = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True)
    timeout_seconds = models.PositiveIntegerField(default=30)
    credential_backend_kind = models.CharField(max_length=32, blank=True, default="")
    credential_ciphertext = models.BinaryField(default=bytes)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_code = models.CharField(max_length=64, blank=True, default="")

    def save(self, *args, **kwargs):
        _immutable(self, "site_id")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=models.Q(deleted_at__isnull=True), name="nvke_temporal_site_live"
            ),
            models.CheckConstraint(
                condition=models.Q(timeout_seconds__gte=1, timeout_seconds__lte=300),
                name="nvke_temporal_timeout",
            ),
        ]


class NvkeOperatorAuthority(BaseCoreModel):
    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="operator_authorities")
    endpoint = models.URLField(max_length=2048)
    instance_id = models.UUIDField()
    bundle_manifest_sha256 = models.CharField(max_length=64)
    is_enabled = models.BooleanField(default=True)
    timeout_seconds = models.PositiveIntegerField(default=30)
    credential_backend_kind = models.CharField(max_length=32, blank=True, default="")
    credential_ciphertext = models.BinaryField(default=bytes)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_code = models.CharField(max_length=64, blank=True, default="")

    def save(self, *args, **kwargs):
        _immutable(self, "site_id")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=models.Q(deleted_at__isnull=True), name="nvke_operator_site_live"
            ),
            models.CheckConstraint(
                condition=models.Q(timeout_seconds__gte=1, timeout_seconds__lte=300),
                name="nvke_operator_timeout",
            ),
        ]


class NvkeWorkloadAccessBinding(BaseCoreModel):
    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="workload_access_bindings")
    controller_connection = models.ForeignKey(NvkeConnection, on_delete=models.PROTECT, related_name="+")
    source_operation = models.ForeignKey("NvkeOperation", on_delete=models.PROTECT, related_name="+")
    cluster_id = models.UUIDField()
    name_digest = models.CharField(max_length=64)
    upstream_revision = models.CharField(max_length=40)

    def save(self, *args, **kwargs):
        _immutable(
            self,
            "site_id",
            "controller_connection_id",
            "source_operation_id",
            "cluster_id",
            "name_digest",
            "upstream_revision",
        )
        if not NvkeConnection.all_objects.filter(
            pk=self.controller_connection_id, site_id=self.site_id, surface="controller"
        ).exists():
            raise ValueError("NVKE access binding must use its site's controller")
        if not NvkeOperation.all_objects.filter(
            pk=self.source_operation_id, site_id=self.site_id, operation_key="getCluster"
        ).exists():
            raise ValueError("NVKE access binding must use its site's cluster read")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "controller_connection", "cluster_id", "name_digest", "upstream_revision"],
                condition=models.Q(deleted_at__isnull=True),
                name="nvke_access_binding_live",
            ),
        ]


class NvkeOperation(BaseCoreModel):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        RECONCILING = "reconciling"
        AWAITING_READY = "awaiting_ready"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"
        UNCERTAIN = "uncertain"

    site = models.ForeignKey(NvkeSite, on_delete=models.PROTECT, related_name="operations")
    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="nvke_operations"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    request_id = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    operation_key = models.CharField(max_length=128)
    expected_site_version = models.PositiveIntegerField()
    authority = models.JSONField(default=dict)
    input_backend_kind = models.CharField(max_length=32)
    input_ciphertext = models.BinaryField()
    result_backend_kind = models.CharField(max_length=32, blank=True, default="")
    result_ciphertext = models.BinaryField(default=bytes)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    generation = models.PositiveIntegerField(default=0)
    workflow_run_id = models.CharField(max_length=128, blank=True, default="")
    stage = models.CharField(max_length=96, blank=True, default="")
    remote_reference = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=256, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField()

    @property
    def workflow_id(self):
        return f"NvkeOperationWorkflow-{self.guid}-{self.generation}"

    @property
    def terminal(self):
        return self.status in {self.Status.SUCCEEDED, self.Status.FAILED, self.Status.CANCELLED}

    def save(self, *args, **kwargs):
        _immutable(
            self,
            "site_id",
            "organization_id",
            "actor_id",
            "request_id",
            "request_digest",
            "operation_key",
            "expected_site_version",
            "authority",
            "input_backend_kind",
            "input_ciphertext",
        )
        owner = NvkeSite.all_objects.filter(pk=self.site_id).values_list("organization_id", flat=True).first()
        if owner != self.organization_id:
            raise ValueError("NVKE operation must belong to its site's organization")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "actor", "request_id"], name="nvke_operation_actor_request_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "queued",
                        "running",
                        "reconciling",
                        "awaiting_ready",
                        "succeeded",
                        "failed",
                        "cancelled",
                        "uncertain",
                    ]
                ),
                name="nvke_operation_known_status",
            ),
        ]
        indexes = [models.Index(fields=["site", "status", "created_at"], name="nvke_operation_site_state")]


class NvkeInstallPlan(BaseCoreModel):
    original_operation = models.ForeignKey(
        NvkeOperation, on_delete=models.PROTECT, related_name="install_plans"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    source_binding = models.JSONField(default=dict)
    request_digest = models.CharField(max_length=64)
    input_backend_kind = models.CharField(max_length=32)
    input_ciphertext = models.BinaryField()
    summary = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, default="preparing", choices=[("preparing", "Preparing"), ("prepared", "Prepared")]
    )

    def save(self, *args, **kwargs):
        _immutable(
            self,
            "guid",
            "original_operation_id",
            "actor_id",
            "source_binding",
            "request_digest",
            "input_backend_kind",
            "input_ciphertext",
        )
        if self.pk and type(self).all_objects.filter(pk=self.pk, status="prepared").exists():
            _immutable(self, "summary", "status")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["preparing", "prepared"]),
                name="nvke_install_plan_known_status",
            )
        ]
