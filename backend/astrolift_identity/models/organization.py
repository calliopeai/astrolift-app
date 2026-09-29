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
    # Off by default, deliberately (#1594). Turning it on starts writing an
    # org's audit events to the install's blob store, which is a data-export
    # decision an operator has to make rather than inherit: the archive
    # leaves the platform's own storage and lands wherever the install
    # points AWS_STORAGE_BUCKET_NAME. It also does not change what is
    # retained -- the append-only trigger still refuses DELETE, so this
    # archives and never prunes.
    # Container image signing (#1605). Astrolift does not sign images; it
    # verifies signatures the customer's own build already produced, which
    # is the scope decided on the issue.
    #
    # Shape: {"enforcement": "disabled|optional|required",
    #         "allowed_signers": [{"kind": "oidc|kms", "identity": "..."}]}
    #
    # Empty means disabled, and disabled is the default. A cluster of
    # installs already carry `block_on_missing_signature: true` in their app
    # policy, which nothing has ever evaluated -- turning that into real
    # enforcement without an operator opting in would block every promote on
    # every one of them.
    #
    # Identities only. No keys, no certificates: an OIDC subject+issuer or a
    # KMS ARN is a pointer, and this is a plaintext column.
    image_signing_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Image signature verification. Empty or {"enforcement": "disabled"} '
            "verifies nothing. 'optional' warns on unsigned or unrecognised "
            "signers; 'required' blocks. Needs at least one allowed signer to "
            "be anything but disabled."
        ),
    )
    audit_export_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Write audit events past this org's retention window to the install's "
            "blob store as hash-chained JSONL. Does not delete anything: the "
            "append-only trigger still refuses DELETE (see #1594)."
        ),
    )
    preview_max_active_default = models.PositiveIntegerField(default=5)
    log_retention_days_default = models.PositiveIntegerField(default=30)
    # Observability streams. Metrics carry two horizons because raw
    # samples are expensive (default 90d) but rollups are cheap
    # (default 1y) — surfacing them as separate knobs lets billing
    # show 'raw' and 'rollup' lines distinctly.
    metrics_retention_days_default = models.PositiveIntegerField(default=90)
    metrics_rollup_retention_days_default = models.PositiveIntegerField(default=365)
    trace_retention_days_default = models.PositiveIntegerField(default=14)

    # Where a pipeline run's Job/Step rows live (#1531).
    #
    # SNAPSHOT copies the definition onto each PipelineRun, so a run keeps
    # the jobs it actually ran and a push landing mid-run cannot retarget a
    # fan-out already in flight. SHARED keeps one set of rows on the
    # Pipeline and rewrites them in place, which costs a fixed number of
    # rows however often the pipeline runs.
    #
    # This sits on the organization because a pipeline has no cluster to
    # carry it: a CI-only pipeline never deploys anywhere, and the ones
    # that do reach a cluster per deploy step rather than as a property of
    # the pipeline. The organization is the narrowest scope that is always
    # present.
    pipeline_definition_mode = models.CharField(
        max_length=16,
        choices=[("snapshot", "Snapshot per run"), ("shared", "Shared across runs")],
        default="snapshot",
    )

    # When True, users can edit their own profile fields (display
    # name, email, avatar, locale) on /settings/profile via
    # updateMyProfile. When False (default for SSO-only installs),
    # the IdP is the source of truth and the page renders read-only.
    # Even when True, fields populated by the IdP at last login stay
    # locked — local edits would just get overwritten on the next
    # sync, so we surface them as read-only with a tooltip.
    allow_user_profile_edit = models.BooleanField(default=True)

    # Flipped non-null by the first-run wizard once the operator
    # either completes or explicitly skips the guided setup. The FE
    # opens the wizard on first dashboard render when this is null
    # AND the user has zero team memberships; once set, the wizard
    # is opt-in only (re-launchable from the user menu). Set on the
    # *organization* rather than the user so an operator who joins
    # an already-bootstrapped org doesn't get prompted to redo it.
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    # Operator-defined tags stamped on every cloud resource this org
    # provisions, e.g. {"cost_center": "platform-rnd"} (#1505). They ride
    # ProvisionSpec.tags, which AWS namespaces under astrolift.io/extra/,
    # Azure passes as custom_tags and GCP merges into labels — the
    # platform envelope (org / app / env) is separate and always applied.
    #
    # Validated on the way in by core.resource_tags against the tightest
    # of the three clouds, so a tag either provisions everywhere or is
    # refused in front of the person who typed it.
    default_resource_tags = models.JSONField(default=dict, blank=True)

    # Data-residency pinning (#152, spec 12 §14). The deploy path reads this
    # into astrolift_clusters.residency.ResidencyPolicy and refuses any target
    # cluster whose region is outside the list. Empty is the unconfigured
    # state ("deploy anywhere"); once an admin sets it the list is an
    # allow-list and the check fails closed, including for a cluster whose
    # region column is blank.
    #
    # Regions, not cluster ids: a cluster can be replaced or re-provisioned
    # inside a region, and an allow-list of surrogate keys would silently stop
    # covering the replacement.
    residency_allowed_regions = models.JSONField(default=list, blank=True)

    # Per-kind managed-service isolation floor this org's compliance posture
    # requires, e.g. {"postgres": "dedicated"}. A floor, not a default: it
    # outranks the per-service request at provision time, so an app cannot
    # put a regulated kind back onto a shared backing instance. Empty means
    # no floor and the request or the variant default decides.
    #
    # Validated on the way in by the mutation. A mode this control plane
    # cannot parse would otherwise sit here until the next provision and
    # then fail it, away from whoever typed it.
    managed_service_isolation_policy = models.JSONField(default=dict, blank=True)

    # House theme for the operator UI (#135). The appearance axes — ground,
    # accent, density, corners — are per-person by default; this is the org's
    # answer for anyone who has not chosen, and `appearance_locked` makes it
    # the answer for everyone.
    #
    # Stored as one JSON blob rather than four columns because the axis set is
    # the frontend's to define (frontend/lib/appearance.ts) and will grow; a
    # column per axis would put a migration in the way of every addition.
    # Unknown or malformed keys are ignored by the client's `normalize`, so a
    # stale value degrades to the shipped default instead of breaking the UI.
    # Empty dict means "no house theme".
    appearance_default = models.JSONField(default=dict, blank=True)

    # When true the house theme wins outright and the personal picker renders
    # read-only. Deliberately separate from `appearance_default` being set:
    # an org can publish a default it wants people to be able to override.
    appearance_locked = models.BooleanField(default=False)

    # Settings a person lacks the permission to change (spec 44 §5.3): shown
    # read-only or hidden, for everyone who has not chosen for themselves
    # (#2154). A person's own UserPreferences.restricted_settings wins.
    restricted_settings_default = models.CharField(
        max_length=8,
        choices=[("show", "Show read-only"), ("hide", "Hide")],
        default="show",
    )

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
