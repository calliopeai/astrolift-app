"""
Organization — the top of the tenant hierarchy.

Every business entity in the control plane belongs to exactly one
Organization (transitively, via Team → Project → App). The org owns:

* the bound IdentityProvider (one per org);
* default cluster + managed-domain selections;
* SCIM enablement + audit retention defaults;
* preview / log retention defaults inherited by RegisteredApps.

See ``specs/04`` §3.2.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Organization(NamedBaseCoreModel):
    website = models.URLField(blank=True, default="")

    # Cluster + domain defaults: nullable FKs filled later (string FK
    # targets so the migration can land before P1.T3 brings those
    # models online).
    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="default_for_orgs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    default_managed_domain = models.ForeignKey(
        "astrolift_clusters.ManagedDomain",
        related_name="default_for_orgs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    identity_provider = models.ForeignKey(
        "astrolift_identity.IdentityProvider",
        related_name="default_for_orgs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # SCIM
    scim_enabled = models.BooleanField(default=False)
    scim_token_hash = models.CharField(max_length=128, blank=True, default="")

    # Retention / quota defaults inherited by leaf entities. Platform
    # admins may bump the platform-wide default via settings; the org
    # column is the per-org override (NULL = inherit). See spec 08 §12
    # for the observability stream defaults (logs/metrics/traces).
    audit_log_retention_days = models.PositiveIntegerField(default=365)
    preview_max_active_default = models.PositiveIntegerField(default=5)
    log_retention_days_default = models.PositiveIntegerField(default=30)
    # Observability streams. Metrics carry two horizons because raw
    # samples are expensive (default 90d) but rollups are cheap
    # (default 1y) — surfacing them as separate knobs lets billing
    # show 'raw' and 'rollup' lines distinctly.
    metrics_retention_days_default = models.PositiveIntegerField(default=90)
    metrics_rollup_retention_days_default = models.PositiveIntegerField(default=365)
    trace_retention_days_default = models.PositiveIntegerField(default=14)

    # When True, users can edit their own profile fields (display
    # name, email, avatar, locale) on /settings/profile via
    # updateMyProfile. When False (default for SSO-only installs),
    # the IdP is the source of truth and the page renders read-only.
    # Even when True, fields populated by the IdP at last login stay
    # locked — local edits would just get overwritten on the next
    # sync, so we surface them as read-only with a tooltip.
    allow_user_profile_edit = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="organization_slug_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["slug"], name="org_slug_idx"),
        ]
