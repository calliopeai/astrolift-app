import uuid

from django.db import models

from core.models.base import BaseCoreModel


class ManagedBoxRuntime(BaseCoreModel):
    box = models.OneToOneField(
        "astrolift_agents.AgentBox", on_delete=models.RESTRICT, related_name="managed_runtime"
    )
    cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.RESTRICT)
    owner_epoch = models.UUIDField(default=uuid.uuid4, editable=False)
    api_url = models.URLField(max_length=512)
    workspace_path = models.CharField(max_length=1024)
    namespace = models.CharField(max_length=63)
    job_name = models.CharField(max_length=253)
    job_uid = models.CharField(max_length=128)
    claim_name = models.CharField(max_length=253)
    claim_uid = models.CharField(max_length=128)
    pod_name = models.CharField(max_length=253)
    pod_uid = models.CharField(max_length=128)
    container_name = models.CharField(max_length=63)
    image = models.CharField(max_length=512)
    observed_image_id = models.CharField(max_length=512)
    runtime_instance_id = models.CharField(max_length=32, blank=True, default="")
    token_hash = models.CharField(max_length=64)
    token_expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner_epoch"], name="managed_box_owner_epoch_unique")]
