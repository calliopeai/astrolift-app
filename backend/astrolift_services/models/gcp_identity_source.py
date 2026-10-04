"""Retained original registered GKE incarnation and one-shot app GSA evidence."""

from django.db import models

from core.models.base import BaseCoreModel


class _RetainedSource(BaseCoreModel):
    class Meta:
        abstract = True

    immutable_fields = ()

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self)._unscoped.filter(pk=self.pk).values().first()
            if previous and any(previous[name] != getattr(self, name) for name in self.immutable_fields):
                raise ValueError("GCP_ORIGINAL_SOURCE_IMMUTABLE")
            if previous and previous.get("unique_id") is not None and previous["unique_id"] != self.unique_id:
                raise ValueError("GCP_ORIGINAL_ACCOUNT_UID_IMMUTABLE")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("GCP_ORIGINAL_SOURCE_RETIREMENT_REQUIRES_REVIEW")

    def soft_delete(self, **kwargs):
        raise ValueError("GCP_ORIGINAL_SOURCE_RETIREMENT_REQUIRES_REVIEW")

    def restore(self):
        raise ValueError("GCP_ORIGINAL_SOURCE_RESTORE_REQUIRES_REVIEW")


class GCPClusterIdentitySource(_RetainedSource):
    immutable_fields = (
        "guid",
        "tenant_cluster_id",
        "provider_plugin_id",
        "organization_id",
        "original_snapshot",
        "original_sha256",
        "admitted_operation_id",
    )
    tenant_cluster = models.OneToOneField("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    organization = models.ForeignKey(
        "astrolift_identity.Organization", null=True, blank=True, on_delete=models.PROTECT
    )
    original_snapshot = models.JSONField()
    original_sha256 = models.CharField(max_length=64)
    admitted_operation_id = models.UUIDField()


class GCPAppIdentitySource(_RetainedSource):
    immutable_fields = (
        "guid",
        "organization_id",
        "registered_app_id",
        "tenant_cluster_id",
        "provider_plugin_id",
        "cluster_source_id",
        "original_snapshot",
        "original_sha256",
        "operation_id",
        "reservation_nonce",
        "workflow_id",
        "execution_id",
        "authority_reference",
        "authority_reference_sha256",
        "accepted_union_sha256",
        "accepted_native_sha256",
        "create_request_sha256",
        "account_id",
    )

    class State(models.TextChoices):
        UNSENT = "UNSENT"
        SENT = "SENT"
        UNKNOWN = "UNKNOWN"
        EVIDENCE = "EVIDENCE"
        OBSERVED = "OBSERVED"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    cluster_source = models.ForeignKey(GCPClusterIdentitySource, on_delete=models.PROTECT)
    original_snapshot = models.JSONField()
    original_sha256 = models.CharField(max_length=64)
    operation_id = models.UUIDField()
    reservation_nonce = models.UUIDField()
    workflow_id = models.CharField(max_length=200)
    execution_id = models.CharField(max_length=200)
    authority_reference = models.JSONField()
    authority_reference_sha256 = models.CharField(max_length=64)
    accepted_union_sha256 = models.CharField(max_length=64)
    accepted_native_sha256 = models.CharField(max_length=64)
    create_request_sha256 = models.CharField(max_length=64)
    account_id = models.CharField(max_length=30)
    unique_id = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001, NULL means no native UID evidence
    state = models.CharField(max_length=16, choices=State.choices)
    evidence_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster"], name="gcp_original_app_cluster_retained"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=["UNSENT", "SENT", "UNKNOWN"], unique_id__isnull=True)
                    | models.Q(state__in=["EVIDENCE", "OBSERVED"], unique_id__isnull=False)
                ),
                name="gcp_original_source_evidence_state",
            ),
        ]
