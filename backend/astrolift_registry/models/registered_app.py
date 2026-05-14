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

    class TriggerMode(models.TextChoices):
        AUTO_ON_PUSH = "auto_on_push"
        MANUAL = "manual"
        EXTERNAL_CI = "external_ci"
        CRON = "cron"

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
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="registered_apps",
        on_delete=models.CASCADE,
    )

    source_kind = models.CharField(max_length=32, choices=SourceKind.choices, default=SourceKind.GITHUB)
    source_repo = models.CharField(max_length=255, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    manifest_path = models.CharField(max_length=255, default="astrolift.toml")
    default_branch = models.CharField(max_length=128, default="main")

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

    # Latest preview screenshot URL for this app, written by the
    # platform's screenshotter service (spec 09 §4.21). Stays empty
    # until the service captures and uploads its first frame; the UI
    # falls back to a branded placeholder while empty.
    preview_screenshot_url = models.URLField(blank=True, default="", max_length=1024)

    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="registered_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

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
        ]

    _PROVISIONING_TRANSITIONS = {
        ProvisioningStatus.PENDING: {ProvisioningStatus.PROVISIONING, ProvisioningStatus.FAILED},
        ProvisioningStatus.PROVISIONING: {ProvisioningStatus.READY, ProvisioningStatus.FAILED},
        ProvisioningStatus.READY: {ProvisioningStatus.PROVISIONING, ProvisioningStatus.FAILED},
        ProvisioningStatus.FAILED: {ProvisioningStatus.PROVISIONING},
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
