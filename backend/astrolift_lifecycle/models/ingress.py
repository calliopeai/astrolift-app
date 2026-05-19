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
        VALIDATING = "validating"
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

    # ---- handshake surface (#397) ---------------------------------

    txt_challenge_token = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Random token the operator pastes into a TXT record at "
            "``_astrolift-challenge.<hostname>`` so DNS-TXT validation "
            "can confirm they control the hostname."
        ),
    )
    expected_cname_target = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Cluster ingress hostname the operator's CNAME should "
            "point at. Captured at addAppDomain time so the operator "
            "sees a stable target even if the cluster's resolver "
            "shape changes later."
        ),
    )
    required_dns_records = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of ``{kind, name, value, ttl, propagated, "
            "last_checked_at, message}`` rows the operator must add "
            "to their authoritative DNS for the hostname to validate. "
            "When the parent zone is platform-managed, the platform "
            "creates these via ``DnsDriver.ensure_record`` and the "
            "rows reflect the platform's own create state."
        ),
    )
    is_platform_managed_zone = models.BooleanField(
        default=False,
        help_text=(
            "True when the hostname's parent zone matches a row in "
            "``ManagedDomain`` — the platform owns the zone and "
            "creates the records automatically. False when the "
            "operator's authoritative DNS is somewhere the platform "
            "can't write to and they have to add records themselves."
        ),
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_validation_error = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text=(
            "Operator-facing reason the most recent validation pass "
            "failed (e.g., 'TXT record not found at _astrolift-"
            "challenge.example.com — still propagating?'). Cleared on "
            "successful validation."
        ),
    )

    # ---- cert lifecycle (#397 PR 5 + self-serve unhappy path) -----

    class CertificateState(models.TextChoices):
        NOT_REQUESTED = "not_requested"
        ISSUING = "issuing"
        ACTIVE = "active"
        FAILED = "failed"
        BYO = "byo"

    certificate_state = models.CharField(
        max_length=16,
        choices=CertificateState.choices,
        default=CertificateState.NOT_REQUESTED,
        help_text=(
            "not_requested: validation hasn't completed yet. "
            "issuing: ensure_certificate fired; provider still "
            "provisioning. "
            "active: cert is usable by the renderer. "
            "failed: cert issuance failed — operator action needed "
            "(BYO cert, switch validation method, etc.). "
            "byo: operator uploaded a cert via "
            "``uploadCustomDomainCertificate``, bypassing auto-issuance."
        ),
    )
    last_certificate_error = models.CharField(
        max_length=512,
        blank=True,
        default="",
    )
    byo_certificate_pem = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Operator-uploaded certificate chain (PEM) for the BYO "
            "path — used when the platform can't auto-issue (AWS "
            "with externally-managed DNS, LE rate-limited zones, "
            "custom CA). Write-only via GraphQL; never echoed back."
        ),
    )
    byo_certificate_uploaded_at = models.DateTimeField(null=True, blank=True)

    # ---- cert observability metadata (#731) -----------------------
    # Cached snapshot of the TLS driver's ``CertificateInfo`` so the
    # AppDomain GraphQL type can render 'expires in N days' without a
    # per-request round-trip to the cloud's cert API.  Refreshed at
    # most once per hour by the resolver's lazy refresher (#731) and
    # by the cert-renewal workflow when it rotates the cert.

    cert_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Cached ``not_after`` from the TLS driver's "
            "``list_certificates`` call.  Null when no certificate has "
            "been issued yet (state ``not_requested`` / ``issuing``) or "
            "the driver returned no row.  The FE renders 'expires in N "
            "days' off this column and dots the chip warning when less "
            "than 14 days."
        ),
    )
    cert_issuer_serial = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Stable identifier for the current cert across renewals — "
            "the ACM ARN, the Let's Encrypt serial, etc.  Lets the "
            "renewal workflow observe a renewal landing (the serial "
            "changes) versus the same cert living on past its "
            "originally-issued date (the serial holds steady)."
        ),
    )
    cert_observability_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "Driver-reported renewal status the FE colours the chip "
            "off ('auto' / 'manual' / 'failed' / 'unknown').  Empty "
            "string when never populated; the FE renders the chip "
            "neutral in that case."
        ),
    )
    cert_metadata_refreshed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last time the resolver's lazy refresher (or the renewal "
            "workflow) updated cert_expires_at / cert_issuer_serial / "
            "cert_observability_status.  Reads older than 1h trigger a "
            "background refresh; null forces a refresh on the next "
            "read."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hostname"],
                condition=models.Q(deleted_at__isnull=True),
                name="custom_domain_hostname_unique_active",
            ),
        ]
