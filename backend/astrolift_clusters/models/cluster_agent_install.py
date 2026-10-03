"""A reviewed installation attempt owns one credential and immutable Secret."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ClusterAgentInstall(BaseCoreModel):
    class Status(models.TextChoices):
        QUEUED = "queued"
        INSTALLING = "installing"
        AWAITING_HEARTBEAT = "awaiting_heartbeat"
        SUCCEEDED = "succeeded"
        REFUSED = "refused"
        UNCERTAIN = "uncertain"

    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster", on_delete=models.PROTECT, related_name="agent_installations"
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    request_id = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    expected_version = models.PositiveIntegerField()
    source_digest = models.CharField(max_length=64)
    credential_ceiling = models.JSONField(default=dict)
    credential_hash = models.CharField(max_length=64)
    credential_backend_kind = models.CharField(max_length=64)
    credential_ciphertext = models.BinaryField()
    interval_seconds = models.PositiveIntegerField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    generation = models.PositiveIntegerField(default=0)
    workflow_run_id = models.CharField(max_length=128, blank=True, default="")
    secret_uid = models.CharField(max_length=128, blank=True, default="")
    secret_resource_version = models.CharField(max_length=128, blank=True, default="")
    manifest_identities = models.JSONField(default=dict)
    previous_deployment_name = models.CharField(max_length=128, blank=True, default="")
    previous_deployment_uid = models.CharField(max_length=128, blank=True, default="")
    previous_deployment_retired = models.BooleanField(default=False)
    activation_source_digest = models.CharField(max_length=64, blank=True, default="")
    deployment_confirmed = models.BooleanField(default=False)
    heartbeat_confirmed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    @property
    def secret_name(self):
        return f"astrolift-agent-{self.guid}"

    @property
    def deployment_name(self):
        return f"astrolift-agent-{self.guid}"

    @property
    def workflow_id(self):
        return f"InstallClusterAgentWorkflow-{self.guid}-{self.generation}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"], name="agent_install_actor_request_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant_cluster"],
                condition=models.Q(
                    deleted_at__isnull=True,
                    status__in=["queued", "installing", "awaiting_heartbeat", "uncertain"],
                ),
                name="agent_install_one_live_attempt",
            ),
        ]
