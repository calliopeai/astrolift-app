import uuid

from django.db import models

from core.models.base import BaseCoreModel


class ManagedBoxRuntime(BaseCoreModel):
    class Phase(models.TextChoices):
        PENDING = "pending"
        CERTIFIED = "certified"
        REVOKED = "revoked"

    phase = models.CharField(max_length=16, choices=Phase.choices, default=Phase.CERTIFIED)
    box = models.OneToOneField(
        "astrolift_agents.AgentBox", on_delete=models.RESTRICT, related_name="managed_runtime"
    )
    cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.RESTRICT)
    owner_epoch = models.UUIDField(default=uuid.uuid4, editable=False)
    api_url = models.URLField(max_length=512)
    workspace_path = models.CharField(max_length=1024)
    namespace = models.CharField(max_length=63)
    job_name = models.CharField(max_length=253)
    job_uid = models.CharField(max_length=128, blank=True, default="")
    claim_name = models.CharField(max_length=253)
    claim_uid = models.CharField(max_length=128, blank=True, default="")
    pod_name = models.CharField(max_length=253, blank=True, default="")
    pod_uid = models.CharField(max_length=128, blank=True, default="")
    container_name = models.CharField(max_length=63)
    image = models.CharField(max_length=512)
    observed_image_id = models.CharField(max_length=512, blank=True, default="")
    runtime_instance_id = models.CharField(max_length=32, blank=True, default="")
    token_hash = models.CharField(max_length=64, blank=True, default="")
    token_expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner_epoch"], name="managed_box_owner_epoch_unique")]
