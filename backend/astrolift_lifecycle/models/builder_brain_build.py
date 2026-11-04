"""An immutable source build, consumed by a separate, fresh hosting request.

A brain intent hosts an app on an existing brain (#2456). A project intent
hosts an ordinary app in its exact project without one (#2471).
"""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class BuilderBrainBuild(BaseCoreModel):
    class HostingMode(models.TextChoices):
        BRAIN = "brain", "Brain"
        PROJECT = "project", "Project"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    project = models.ForeignKey("astrolift_identity.Project", on_delete=models.PROTECT)
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        on_delete=models.PROTECT,
        related_name="source_builds",
        null=True,
        blank=True,
    )
    # The DB default keeps a writer from before #2471 creating brain intents.
    hosting_mode = models.CharField(
        max_length=16, choices=HostingMode.choices, default=HostingMode.BRAIN, db_default=HostingMode.BRAIN
    )
    dev_environment = models.ForeignKey("astrolift_lifecycle.DevEnvironment", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    credential = models.ForeignKey("astrolift_identity.ApiToken", on_delete=models.PROTECT)
    credential_binding = models.CharField(max_length=64)
    accepted_scopes = models.JSONField(default=list)
    idempotency_key = models.CharField(max_length=128)
    request_sha256 = models.CharField(max_length=64)
    archive_key = models.CharField(max_length=255)
    archive_sha256 = models.CharField(max_length=64)
    archive_bytes = models.PositiveIntegerField()
    expanded_bytes = models.PositiveIntegerField()
    # Server-owned admission facts, including exact source owner, target UID,
    # generated recipe and initial TTL request. No bearer/archive credentials.
    admission = models.JSONField(default=dict)
    status = models.CharField(max_length=32, default="queued")
    failure_code = models.CharField(max_length=64, blank=True, default="")
    image_digest = models.CharField(max_length=80, blank=True, default="")
    image_ref = models.CharField(max_length=512, blank=True, default="")
    # Exact original native object UIDs; cleanup never adopts a replacement.
    cleanup_receipt = models.JSONField(default=dict)
    build_deadline = models.DateTimeField()
    build_submission = models.CharField(max_length=16, default="pending")
    build_first_submission_at = models.DateTimeField(null=True, blank=True)
    build_run_id = models.CharField(max_length=64, blank=True, default="")
    build_engine_status = models.CharField(max_length=32, blank=True, default="")
    deployment_submission = models.CharField(max_length=16, default="not_requested")
    deployment_first_submission_at = models.DateTimeField(null=True, blank=True)
    dispatch_backend_kind = models.CharField(max_length=32, blank=True, default="")
    dispatch_ciphertext = models.BinaryField(default=bytes, blank=True)
    dispatch_sha256 = models.CharField(max_length=64, blank=True, default="")
    deployment_workflow_id = models.CharField(max_length=256, blank=True, default="")
    deployment_run_id = models.CharField(max_length=64, blank=True, default="")
    deployment_engine_status = models.CharField(max_length=32, blank=True, default="")
    hosted_app = models.OneToOneField(
        "astrolift_registry.RegisteredApp",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="builder_brain_source",
    )
    deployment = models.OneToOneField(
        "astrolift_lifecycle.Deployment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="builder_brain_source",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "idempotency_key"], name="builder_brain_intent_unique"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(hosting_mode="brain", brain__isnull=False)
                    | models.Q(hosting_mode="project", brain__isnull=True)
                ),
                name="builder_source_hosting_mode_brain",
            ),
        ]
