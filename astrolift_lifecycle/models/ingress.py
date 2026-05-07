"""
Ingress + custom-domain models.

* IngressRule: per-app subdomain routing (one-to-one with the app
  by default; nullable workload for the multi-public case).
* ProjectIngress: project-level multi-app routing on a shared host.
* CustomDomain: customer-owned hostnames with TLS validation tracking.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class IngressRule(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="ingress_rules",
        on_delete=models.CASCADE,
    )
    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="ingress_rules",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    managed_domain = models.ForeignKey(
        "astrolift_clusters.ManagedDomain",
        related_name="ingress_rules",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    subdomain = models.CharField(max_length=128)
    hostname = models.CharField(max_length=255, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subdomain", "managed_domain"],
                condition=models.Q(deleted_at__isnull=True),
                name="ingress_subdomain_unique_active_per_domain",
            ),
        ]


class ProjectIngress(BaseCoreModel):
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="ingresses",
        on_delete=models.CASCADE,
    )
    hostname = models.CharField(max_length=255, db_index=True)
    rules = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)


class CustomDomain(BaseCoreModel):
    class ValidationStatus(models.TextChoices):
        PENDING = "pending"
        VALIDATED = "validated"
        FAILED = "failed"

    class ValidationMethod(models.TextChoices):
        DNS_TXT = "dns_txt"
        HTTP_01 = "http_01"
        DNS_01 = "dns_01"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="custom_domains",
        on_delete=models.CASCADE,
    )
    hostname = models.CharField(max_length=255)
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    validation_method = models.CharField(
        max_length=16,
        choices=ValidationMethod.choices,
        default=ValidationMethod.DNS_TXT,
    )
    validation_value = models.CharField(max_length=255, blank=True, default="")
    certificate_id = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hostname"],
                condition=models.Q(deleted_at__isnull=True),
                name="custom_domain_hostname_unique_active",
            ),
        ]
