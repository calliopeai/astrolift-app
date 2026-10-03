"""An original reviewed collector request, without credential or log payloads."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ClusterLogCollectorOperation(BaseCoreModel):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PREPARING = "preparing"
        INSTALLING = "installing"
        READINESS_PENDING = "readiness_pending"
        READER_GRANT_PENDING = "reader_grant_pending"
        INGESTION_PENDING = "ingestion_pending"
        PROBE_DELETION_PENDING = "probe_deletion_pending"
        POST_LOSS_READ_PENDING = "post_loss_read_pending"
        UNCERTAIN = "uncertain"
        REFUSED = "refused"
        ACTIVATED = "activated"

    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster", on_delete=models.PROTECT, related_name="log_collector_operations"
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    request_id = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    expected_version = models.PositiveIntegerField()
    expected_source = models.CharField(max_length=64)
    source_digest = models.CharField(max_length=64)
    binding_digest = models.CharField(max_length=64)
    credential_ceiling = models.JSONField(default=dict)
    retention_days = models.PositiveIntegerField()
    probe_image = models.CharField(max_length=256)
    policy_digest = models.CharField(max_length=64)
    deadline = models.DateTimeField()
    query_since = models.DateTimeField()
    query_until = models.DateTimeField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    generation = models.PositiveIntegerField(default=0)
    workflow_run_id = models.CharField(max_length=128, blank=True, default="")
    checkpoints = models.JSONField(default=dict)
    stage = models.CharField(max_length=96, blank=True, default="")
    coverage = models.CharField(max_length=32, default="unknown")
    cleanup_pending = models.BooleanField(default=False)
    event_hash = models.CharField(max_length=64, blank=True, default="")
    post_loss_verified_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_cluster_version = models.PositiveIntegerField(null=True, blank=True)
    activation_source_digest = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    @property
    def workflow_id(self):
        return f"InstallClusterLogCollectorWorkflow-{self.guid}-{self.generation}"

    @property
    def terminal(self):
        return self.status in {self.Status.REFUSED, self.Status.ACTIVATED}

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"], name="collector_actor_request_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant_cluster"],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(status__in=["refused", "activated"]),
                name="collector_one_live_operation",
            ),
        ]
