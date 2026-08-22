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

    cert_expiry_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Watermark for the daily cert-expiry monitor (#155).  The "
            "sweep passes this to ``cert_expiry.evaluate`` as "
            "``last_check_at`` so each 30 / 14 / 7-day reminder fires "
            "exactly once instead of re-paging every tick; null makes "
            "the next sweep fire every threshold already crossed.  "
            "Distinct from cert_metadata_refreshed_at, which tracks how "
            "fresh the *driver snapshot* is, not what has been alerted "
            "on."
        ),
    )

    # ---- wildcard + SNI (#753) -------------------------------------

    is_wildcard = models.BooleanField(
        default=False,
        help_text=(
            "True when this domain covers ``*.hostname`` (wildcard TLS). "
            "Issued certificate must carry both the apex and the "
            "``*.<hostname>`` SAN. Wildcard issuance requires DNS-01 "
            "validation — HTTP-01 / DNS-TXT can't satisfy CA wildcard "
            "policy, so the ``addWildcardDomain`` mutation pins "
            "``validation_method`` to ``dns_01``."
        ),
    )
    sni_cert_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Provider-specific certificate identifier the renderer "
            "presents for SNI on this hostname (ACM ARN, GCP managed-"
            "cert resource name, Azure Key Vault cert URI, etc.). Empty "
            "when the platform manages cert selection automatically — "
            "the renderer falls back to its default cert-matching "
            "rules. Operators set this when they need to pin a "
            "specific cert across multi-domain SNI scenarios "
            "(e.g., an EV cert on the apex with a wildcard cert on "
            "subdomains)."
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


class DomainRedirectRule(BaseCoreModel):
    """One redirect rule bound to a ``CustomDomain`` (#742).

    Surfaces the HTTP→HTTPS / apex→www / alt-domain rules the FE renders
    under the Redirects sub-section of the Domains page (#685). The
    renderer reads the rule set for a domain when emitting the
    Ingress/route resources and wires it onto the cluster's ingress
    controller (nginx ``server-snippet``, traefik middleware, ALB
    redirect-action) via the per-cloud driver.

    Rules are ordered by ``priority`` (low → high; first match wins).
    ``setDomainRedirects`` replaces the full set atomically; the FE never
    edits individual rows.

    Soft-delete tracking lives on ``BaseCoreModel.deleted_at`` — the
    ``setDomainRedirects`` mutation soft-deletes the previous set before
    inserting the new one, preserving the audit trail.
    """

    class Kind(models.TextChoices):
        HTTP_TO_HTTPS = "http_to_https"
        APEX_TO_WWW = "apex_to_www"
        WWW_TO_APEX = "www_to_apex"
        ALIAS = "alias"
        CUSTOM = "custom"

    class HttpStatus(models.IntegerChoices):
        MOVED_PERMANENTLY = 301
        FOUND = 302
        TEMPORARY_REDIRECT = 307
        PERMANENT_REDIRECT = 308

    custom_domain = models.ForeignKey(
        CustomDomain,
        related_name="redirect_rules",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    source_pattern = models.CharField(max_length=512, blank=True, default="")
    destination_url = models.CharField(max_length=512, blank=True, default="")
    http_status = models.IntegerField(
        choices=HttpStatus.choices,
        default=HttpStatus.MOVED_PERMANENTLY,
    )
    preserve_query_string = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["priority"]
        indexes = [
            models.Index(
                fields=["custom_domain", "priority"],
                name="redirect_domain_priority_idx",
            ),
        ]


class DomainPathRoute(BaseCoreModel):
    """Path-prefix routing rule on a ``CustomDomain`` (#740).

    Backs the path-based routing sub-section of the Domains page (#686).
    The renderer expands the domain's active route set into multiple
    Ingress rules ordered by ``priority`` (low → high; longest-prefix /
    most-specific first wins) and the per-cloud ingress driver maps them
    onto the cluster's ingress controller (nginx ``location`` blocks, ALB
    listener rules, traefik router rules).

    ``setDomainPathRoutes`` replaces the full set atomically — the FE
    never edits individual rows.  Soft-delete via ``deleted_at``.
    """

    custom_domain = models.ForeignKey(
        CustomDomain,
        related_name="path_routes",
        on_delete=models.CASCADE,
    )
    path_prefix = models.CharField(max_length=512)
    target_workload_slug = models.CharField(max_length=128)
    target_port = models.PositiveIntegerField()
    strip_prefix = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["priority"]
        indexes = [
            models.Index(
                fields=["custom_domain", "priority"],
                name="path_route_domain_priority_idx",
            ),
        ]
