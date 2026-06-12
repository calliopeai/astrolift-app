"""
RegisteredApp — the deployable thing.

Represents a Git repository registered for deployment, plus the
parsed manifest, registry coordinates, and tenant defaults. The
provisioning state machine lives on the model itself; mutating the
state field directly is forbidden — call ``transition_to``.

See ``specs/04`` §6.1.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import NamedBaseCoreModel


class RegisteredApp(NamedBaseCoreModel):
    class SourceKind(models.TextChoices):
        GITHUB = "github"
        GITLAB = "gitlab"
        BITBUCKET = "bitbucket"
        GITEA = "gitea"
        GIT_URL = "git_url"
        # Apps promoted from a Calliope App Builder DevEnvironment
        # (#767, #768). No source repo; the canonical artifact is the
        # file tree captured on the DevEnvironment at promote time and
        # baked into a container image by the platform.
        DIRECT_UPLOAD = "direct_upload"

    class TriggerMode(models.TextChoices):
        AUTO_ON_PUSH = "auto_on_push"
        MANUAL = "manual"
        EXTERNAL_CI = "external_ci"
        CRON = "cron"

    class BuildMode(models.TextChoices):
        # CI builds and pushes the image; the platform only deploys the
        # already-published tag. The historical behaviour and the
        # default for every app registered before this field existed.
        CI_PUSHED = "ci_pushed"
        # The platform fetches the source tree and invokes a BuildDriver
        # to build + push the image itself (no CI integration required).
        PLATFORM_BUILD = "platform_build"
        # The image tag is supplied directly and no build step runs at
        # all — the operator points the app at a pre-built image.
        NONE = "none"

    class ProvisioningStatus(models.TextChoices):
        PENDING = "pending"
        PROVISIONING = "provisioning"
        READY = "ready"
        FAILED = "failed"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="registered_apps",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="registered_apps",
        on_delete=models.CASCADE,
    )
    # Nullable so the operator can unassign an app from its project
    # (and so the nav-tree code can render "Unassigned" buckets at the
    # team / org levels — #391). When the project itself is hard-deleted
    # we clear the FK rather than cascading the destruction; soft-deletes
    # leave the FK in place because the project row stays in the table.
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="registered_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    source_kind = models.CharField(max_length=32, choices=SourceKind.choices, default=SourceKind.GITHUB)
    source_repo = models.CharField(max_length=255, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    manifest_path = models.CharField(max_length=255, default="astrolift.toml")
    default_branch = models.CharField(max_length=128, default="main")

    # How the deployable image is produced for this app. ``ci_pushed``
    # (default) keeps the historical contract where CI publishes the
    # image and the platform only rolls it out. ``platform_build`` hands
    # the source tree to a BuildDriver; ``none`` skips building entirely
    # and deploys a tag supplied directly. ``dockerfile_path`` /
    # ``build_context`` / ``build_args`` are consumed only under
    # ``platform_build`` (they're ignored for the other two modes but
    # captured at registration so the contract is stable when the
    # platform-build workflow lands).
    build_mode = models.CharField(
        max_length=32,
        choices=BuildMode.choices,
        default=BuildMode.CI_PUSHED,
    )
    dockerfile_path = models.CharField(max_length=512, default="Dockerfile", blank=True)
    build_context = models.CharField(max_length=512, default=".", blank=True)
    build_args = models.JSONField(default=dict, blank=True)

    manifest_raw = models.TextField(blank=True, default="")
    manifest_raw_staged = models.TextField(blank=True, default="")
    manifest_normalized = models.JSONField(default=dict, blank=True)
    manifest_hash = models.CharField(max_length=128, blank=True, default="")
    last_synced_hash = models.CharField(max_length=128, blank=True, default="")

    registry_repo_uri = models.CharField(max_length=512, blank=True, default="")
    registry_pull_secret_ref = models.CharField(max_length=512, blank=True, default="")
    push_role_ref = models.CharField(max_length=512, blank=True, default="")

    k8s_namespace = models.CharField(max_length=128, blank=True, default="")
    subdomain = models.CharField(max_length=128, blank=True, default="")
    is_active = models.BooleanField(default=True)
    provisioning_status = models.CharField(
        max_length=32,
        choices=ProvisioningStatus.choices,
        default=ProvisioningStatus.PENDING,
    )
    provisioning_error = models.TextField(blank=True, default="")
    deploy_token_hash = models.CharField(max_length=128, blank=True, default="")
    deploy_token_last_4 = models.CharField(max_length=4, blank=True, default="")
    log_retention_days = models.PositiveIntegerField(default=30)
    preview_max_active = models.PositiveIntegerField(default=5)
    preview_enabled = models.BooleanField(default=True)
    trigger_mode = models.CharField(
        max_length=32,
        choices=TriggerMode.choices,
        default=TriggerMode.AUTO_ON_PUSH,
    )
    # Five-field cron expression (minute hour day month weekday). Only
    # meaningful when ``trigger_mode == CRON``; empty otherwise. The
    # scheduled-deploy workflow that consumes this is tracked
    # separately — for now this is captured at registration time so
    # the contract is stable when that workflow lands.
    cron_expression = models.CharField(max_length=120, blank=True, default="")
    # When ``trigger_mode == 'cron'``, this flag pauses scheduled
    # dispatch without flipping the mode (which would also clear the
    # cron expression). The dispatcher tick skips paused apps; manual
    # deploys are unaffected.
    cron_paused = models.BooleanField(default=False)
    deploy_branch = models.CharField(max_length=128, default="main")

    # App-global webhook-deploy pause (#399). Independent of the
    # per-env ``deploys_paused`` (#378) and the per-env ``ingress_paused``
    # axes: this flag short-circuits CI-fired deploys across every
    # environment of the app. The gate fires inside ``start_deployment``
    # and the CI REST endpoint when ``trigger_kind`` is webhook-shaped
    # (push / ci / scheduled). Operator-fired ``manual`` deploys bypass
    # the pause — explicit on-call escape valve so a wedged CI can be
    # stopped without locking the operator out of fixing the app.
    # ``paused_at`` / ``paused_by`` are stamped on the off→on
    # transition so the Settings UI can render "Paused by <user>,
    # <relative time> — reason: <reason>" without joining the audit log.
    # Archive state (#743). When archived, all workload replicas are scaled
    # to zero and webhook/cron deploys are suppressed. ``archived_by`` is
    # the user who triggered the archive (audit trail; not a permission gate).
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="archived_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    webhook_deploys_paused = models.BooleanField(default=False)
    webhook_deploys_paused_at = models.DateTimeField(null=True, blank=True)
    webhook_deploys_paused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="webhook_deploys_paused_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    webhook_deploys_pause_reason = models.CharField(max_length=512, blank=True, default="")

    # Approval policy (#291). Applies in addition to (and OR'd with)
    # ``AppEnvironment.required_approvals`` — whichever path requires
    # more approvers wins. Empty approver sets with
    # ``requires_approval=True`` mean any user holding the
    # ``app.approve_deploy`` permission on the app's org can approve.
    requires_approval = models.BooleanField(default=False)
    approver_team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="approver_for_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    approver_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="approver_for_apps",
        blank=True,
    )
    minimum_approvals = models.PositiveIntegerField(default=1)

    # Secret-change approval policy (#488). Parallel to the deployment
    # approval gear above. When ``requires_secret_approval=True``, the
    # secret-write mutations (``setAppSecret`` / ``deleteAppSecret`` /
    # ``attachSecretBundle`` / ``detachSecretBundle``) stop applying
    # directly and instead create a ``SecretChangeProposal`` that needs
    # ``secret_minimum_approvals`` approvers before the underlying op
    # fires.  Empty ``secret_approver_users`` with the flag on means
    # "any user holding the secret-approve permission on the app's
    # org can approve" — the permission gate covers that case.
    requires_secret_approval = models.BooleanField(default=False)
    secret_approver_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="secret_approver_for_apps",
        blank=True,
    )
    secret_minimum_approvals = models.PositiveIntegerField(default=1)

    # Latest preview screenshot URL for this app, written by the
    # platform's screenshotter service (spec 09 §4.21). Stays empty
    # until the service captures and uploads its first frame; the UI
    # falls back to a branded placeholder while empty.
    preview_screenshot_url = models.URLField(blank=True, default="", max_length=1024)

    # Timestamp of the last operator-initiated "Resync from source"
    # (#386). Distinct from ``updated_at`` which moves on every save;
    # this only advances when the manifest_sync service runs against
    # the repo. Surfaces on the Settings page so operators can see
    # "Last resynced 5 minutes ago" without grepping audit logs.
    last_resync_at = models.DateTimeField(null=True, blank=True)

    # Source-host webhook state (#385). Populated by
    # ``installAstroliftSourceWebhook`` so the Settings page can show
    # whether the push-event hook is wired up. ``source_webhook_id``
    # is the host-side identifier (GitHub returns a numeric id; we
    # store as string so GitLab / Bitbucket slot in alongside).
    # ``source_webhook_installed_at`` advances on each successful
    # install / refresh; the FE renders it as a relative-time chip.
    source_webhook_id = models.CharField(max_length=128, blank=True, default="")
    source_webhook_installed_at = models.DateTimeField(null=True, blank=True)

    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="registered_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Supply-chain / scanner policy consulted at promote time by the
    # PromoteDeploymentWorkflow (#313). Persisted as a sparse JSON blob
    # so we can add keys without per-toggle migrations; the
    # ``security_policy_resolved`` property fills in the platform
    # defaults for any unset key so callers never need to know which
    # keys are populated. Empty dict means "use defaults everywhere".
    security_policy = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="registered_app_slug_unique_active_per_org",
            ),
            models.UniqueConstraint(
                fields=["source_repo", "manifest_path"],
                condition=models.Q(
                    deleted_at__isnull=True,
                )
                & ~models.Q(source_repo=""),
                name="registered_app_repo_manifest_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="app_org_active_idx"),
            models.Index(fields=["project"], name="app_project_idx"),
            # Apps-list filters (#481). The list query pages by
            # ``(-created_at, -guid)`` over a row set already narrowed
            # by team / project / provisioning_status, so these are the
            # supporting indexes for the filter axes the FE exposes.
            # All three include the soft-delete predicate columns
            # (``deleted_at`` for the team/project pair) so the partial
            # filter inside ``_apply_apps_list_filters`` stays index-only.
            models.Index(fields=["team", "deleted_at"], name="app_team_deleted_idx"),
            models.Index(fields=["project", "deleted_at"], name="app_project_deleted_idx"),
            models.Index(fields=["provisioning_status"], name="app_provisioning_status_idx"),
        ]

    _PROVISIONING_TRANSITIONS = {
        ProvisioningStatus.PENDING: {ProvisioningStatus.PROVISIONING, ProvisioningStatus.FAILED},
        ProvisioningStatus.PROVISIONING: {ProvisioningStatus.READY, ProvisioningStatus.FAILED},
        ProvisioningStatus.READY: {ProvisioningStatus.PROVISIONING, ProvisioningStatus.FAILED},
        ProvisioningStatus.FAILED: {ProvisioningStatus.PROVISIONING},
    }

    @property
    def security_policy_resolved(self) -> dict:
        """Return the supply-chain policy with platform defaults filled in.

        Defaults are deliberately strict: critical CVEs and missing
        cosign signatures block by default. ``block_on_high_cve_threshold``
        is nullable — None means "no count-based block on high-severity
        CVEs". A non-null integer N means "block when the high-CVE count
        on the candidate image is >= N".

        Callers (notably PromoteDeploymentWorkflow's supply-chain gate)
        should always read through this property rather than the raw
        JSON field so that a partial policy persists the operator's
        explicit choices without losing the platform defaults for the
        unspecified knobs.
        """
        policy = self.security_policy or {}
        return {
            "block_on_critical_cves": policy.get("block_on_critical_cves", True),
            "block_on_missing_signature": policy.get("block_on_missing_signature", True),
            "block_on_high_cve_threshold": policy.get("block_on_high_cve_threshold"),
        }

    def transition_provisioning(self, new_status: RegisteredApp.ProvisioningStatus) -> None:
        current = RegisteredApp.ProvisioningStatus(self.provisioning_status)
        allowed = self._PROVISIONING_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"RegisteredApp({self.pk}) cannot transition {current.value} → {new_status.value}"
            )
        self.provisioning_status = new_status.value
        if new_status is RegisteredApp.ProvisioningStatus.READY:
            self.provisioning_error = ""
        self.save(update_fields=["provisioning_status", "provisioning_error", "updated_at", "version"])
