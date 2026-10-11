"""Tenant-owned connection policy, reviewed requests and distinct human votes."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ModelConnectionPolicy(BaseCoreModel):
    class Mode(models.TextChoices):
        AUTO = "AUTO"
        REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
        DENY = "DENY"

    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="model_connection_policies"
    )
    model_deployment = models.ForeignKey(
        "astrolift_services.ManagedService",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="connection_policies",
    )
    mode = models.CharField(max_length=24, choices=Mode.choices)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    allow_self_approval = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(deleted_at__isnull=True, model_deployment__isnull=True),
                name="model_connect_policy_org_live",
            ),
            models.UniqueConstraint(
                fields=["organization", "model_deployment"],
                condition=models.Q(deleted_at__isnull=True, model_deployment__isnull=False),
                name="model_connect_policy_model_live",
            ),
            models.CheckConstraint(
                condition=models.Q(required_approvals__gte=1, required_approvals__lte=16),
                name="model_connect_policy_quorum",
            ),
            models.CheckConstraint(
                condition=models.Q(mode__in=["AUTO", "REQUIRE_APPROVAL", "DENY"]),
                name="model_connect_policy_mode",
            ),
        ]


class ModelConnectionRequest(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"
        STALE = "stale"

    organization = models.ForeignKey(
        "astrolift_identity.Organization", on_delete=models.PROTECT, related_name="model_connection_requests"
    )
    model_deployment = models.ForeignKey(
        "astrolift_services.ManagedService", on_delete=models.PROTECT, related_name="connection_requests"
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp", on_delete=models.PROTECT, related_name="model_connection_requests"
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        on_delete=models.PROTECT,
        related_name="model_connection_requests",
    )
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster", on_delete=models.PROTECT, related_name="model_connection_requests"
    )
    provider_plugin = models.ForeignKey(
        "astrolift_clusters.ProviderPlugin",
        on_delete=models.PROTECT,
        related_name="model_connection_requests",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="model_connection_requests"
    )
    alias = models.CharField(max_length=32)
    idempotency_key = models.UUIDField()
    reviewed_versions = models.JSONField(default=dict)
    policy_version = models.CharField(max_length=64)
    required_approvals = models.PositiveSmallIntegerField()
    allow_self_approval = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    subscription = models.ForeignKey(
        "astrolift_services.ManagedServiceAttachment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="connection_requests",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "requester", "idempotency_key"],
                name="model_connection_request_idempotency",
            ),
            models.CheckConstraint(
                condition=models.Q(required_approvals__gte=1, required_approvals__lte=16),
                name="model_connection_request_quorum",
            ),
        ]


class ModelConnectionApproval(BaseCoreModel):
    request = models.ForeignKey(
        ModelConnectionRequest, on_delete=models.PROTECT, related_name="approval_votes"
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="model_connection_votes"
    )

    api_token = models.ForeignKey(
        "astrolift_identity.ApiToken",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="model_connection_approval_votes",
    )
    session = models.ForeignKey(
        "astrolift_identity.AstroliftSession",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="model_connection_approval_votes",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "voter"], name="model_connection_approval_distinct_user"
            ),
        ]
