"""Retained exact app/environment/workload ownership for a reviewed domain route."""

import json

from django.core.exceptions import ValidationError
from django.db import models

from core.models.base import BaseCoreModel


class ManagedDomainAppBinding(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    managed_domain = models.ForeignKey("astrolift_clusters.ManagedDomain", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    custom_domain = models.ForeignKey(
        "astrolift_lifecycle.CustomDomain",
        null=True,
        on_delete=models.PROTECT,
        related_name="managed_route_operations",
    )
    app_environment = models.ForeignKey("astrolift_lifecycle.AppEnvironment", on_delete=models.PROTECT)
    workload = models.ForeignKey("astrolift_registry.Workload", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    deployment = models.ForeignKey("astrolift_lifecycle.Deployment", on_delete=models.PROTECT)
    faas_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        null=True,
        on_delete=models.PROTECT,
        related_name="domain_faas_bindings",
    )
    api_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        null=True,
        on_delete=models.PROTECT,
        related_name="domain_api_bindings",
    )
    request_id = models.UUIDField(editable=False)
    hostname = models.CharField(max_length=253, editable=False)
    review_sha256 = models.CharField(max_length=64, editable=False)
    target_snapshot = models.JSONField(editable=False)
    authority_reference = models.JSONField(editable=False)
    native_receipts = models.JSONField(default=dict, editable=False)

    class Purpose(models.TextChoices):
        PREPARE = "prepare"
        CONNECT = "connect"

    purpose = models.CharField(max_length=16, choices=Purpose.choices)

    class Status(models.TextChoices):
        ACCEPTED = "accepted"
        CONFIGURING = "configuring"
        READY = "ready"
        UNKNOWN = "unknown"
        REFUSED = "refused"
        PENDING = "pending"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACCEPTED)
    reason = models.CharField(max_length=64, default="REVIEWED_ROUTE_ACCEPTED")
    workflow_id = models.CharField(max_length=128, blank=True, default="")
    observed_at = models.DateTimeField(null=True)

    ORIGINAL_FIELDS = (
        "guid",
        "organization_id",
        "managed_domain_id",
        "registered_app_id",
        "provider_plugin_id",
        "custom_domain_id",
        "app_environment_id",
        "workload_id",
        "tenant_cluster_id",
        "deployment_id",
        "faas_service_id",
        "api_service_id",
        "request_id",
        "hostname",
        "review_sha256",
        "target_snapshot",
        "authority_reference",
        "purpose",
        "workflow_id",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id"], name="domain_route_original_request_unique"
            ),
            models.UniqueConstraint(
                fields=["custom_domain"],
                condition=models.Q(purpose="connect"),
                name="domain_route_original_host_unique",
            ),
            models.UniqueConstraint(
                fields=["managed_domain"],
                condition=models.Q(purpose="prepare"),
                name="domain_route_original_preparation_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(purpose="prepare", custom_domain__isnull=True)
                    | models.Q(purpose="connect", custom_domain__isnull=False)
                ),
                name="domain_route_exact_purpose_host",
            ),
        ]

    def save(self, *args, **kwargs):
        for field in ("target_snapshot", "authority_reference", "native_receipts"):
            value = getattr(self, field)
            if not isinstance(value, dict) or len(json.dumps(value, sort_keys=True)) > 16384:
                raise ValidationError("Domain route metadata is invalid or exceeds its bound.")
        if self.pk is not None:
            original = type(self).all_objects.filter(pk=self.pk).values(*self.ORIGINAL_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self.ORIGINAL_FIELDS
            ):
                raise ValidationError("Original domain route ownership cannot be replaced.")
        super().save(*args, **kwargs)
