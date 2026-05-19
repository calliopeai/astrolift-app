"""
Deployment — one rollout attempt against one (app, env).

Status transitions are typed; direct field set is forbidden — the
state machine on ``transition_to`` is the only way to advance. The
allowed graph mirrors specs/04 §6.5:

    pending_approval → pending → deploying → running
                                    ↓           ↓
                                 failed   redeploying → running
                                                          ↓
                                                       failed
    running → rolled_back
    running → superseded   (newer deploy reaches running)

The full set of transitions is encoded in ``_TRANSITIONS`` so a future
DB-level trigger can be generated from the same source.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models.base import BaseCoreModel


class Deployment(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval"
        PENDING = "pending"
        DEPLOYING = "deploying"
        RUNNING = "running"
        REDEPLOYING = "redeploying"
        FAILED = "failed"
        SUPERSEDED = "superseded"
        ROLLED_BACK = "rolled_back"

    class TriggerKind(models.TextChoices):
        PUSH = "push"
        MANUAL = "manual"
        CI = "ci"
        SCHEDULED = "scheduled"
        ROLLBACK = "rollback"
        PROMOTION = "promotion"

    class Strategy(models.TextChoices):
        """Rollout strategy captured at deploy-creation time (#736).

        ``unknown`` is the back-fill default for rows that pre-date the
        column.  The Temporal workflow's rollout-policy decision sets
        the canonical value when the deploy is created.
        """

        ROLLING = "rolling"
        BLUE_GREEN = "blue_green"
        CANARY = "canary"
        RECREATE = "recreate"
        UNKNOWN = "unknown"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="deployments",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="deployments",
        on_delete=models.CASCADE,
    )
    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="deployments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    triggered_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="triggered_deployments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    triggered_by_token_kind = models.CharField(max_length=32, blank=True, default="")
    triggered_by_token_id = models.BigIntegerField(null=True, blank=True)
    trigger_kind = models.CharField(max_length=32, choices=TriggerKind.choices)

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_APPROVAL,
    )
    workflow_run = models.ForeignKey(
        "astrolift_operations.WorkflowRun",
        related_name="deployments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    image_tag = models.CharField(max_length=128, blank=True, default="")
    image_digest = models.CharField(max_length=256, blank=True, default="")
    config_snapshot = models.JSONField(default=dict, blank=True)
    cluster_revision = models.CharField(max_length=128, blank=True, default="")
    # Rollout strategy captured at deploy-creation time (#736).  The
    # workflow's rollout-policy decision writes this once; never
    # mutated after.  Back-fill rows default to ``unknown`` so the FE
    # can render a neutral pill instead of erroring on a missing field.
    strategy = models.CharField(
        max_length=32,
        choices=Strategy.choices,
        default=Strategy.UNKNOWN,
        blank=True,
    )

    # CI / VCS provenance — captured at deployment creation, never
    # updated thereafter (treat as append-only). Per spec 14 §18.
    # ``ci_actor_kind`` is a free-form label (human / bot / deploy-token /
    # github-action) so we don't need a migration for every new actor
    # variant.
    ci_actor_kind = models.CharField(max_length=64, blank=True, default="")
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    branch = models.CharField(max_length=255, blank=True, default="")
    ci_run_url = models.URLField(blank=True, default="")
    ci_provider = models.CharField(max_length=64, blank=True, default="")
    # Commit-message provenance (#419) — surfaced on the approval card
    # so approvers see *what* they're greenlighting without leaving the
    # page. Optional at the GraphQL boundary; webhook + CLI deploys
    # populate them, manual UI deploys don't.
    commit_message = models.TextField(blank=True, default="")
    commit_author = models.CharField(max_length=255, blank=True, default="")
    # GitHub provenance (#722) — populated by the push-webhook ingestion
    # when the deploy was triggered from a PR. Deployments triggered
    # manually from the UI or via a tagged release have pr_number=0
    # (rendered as "—" on the FE per #651). Avatar URL is stored at
    # deploy-creation time because the GitHub commits API rate-limits
    # otherwise and we don't want every list-deployments call to fan
    # out to GitHub.
    pr_number = models.PositiveIntegerField(default=0)
    commit_author_avatar_url = models.URLField(blank=True, default="")
    # Rejection / abort context (#419) — non-empty when an operator
    # rejected or aborted the deploy. Persisted on the row (rather than
    # only on the audit log) so the approvals UI can render it without
    # a join even after audit-log retention prunes the entry.
    aborted_reason = models.TextField(blank=True, default="")
    promoted_from = models.ForeignKey(
        "self",
        related_name="promotions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    approvals_required = models.PositiveIntegerField(default=0)
    approvals_received = models.PositiveIntegerField(default=0)

    # Emailed-approval magic link (#125, spec 06 §4.6). Plaintext is
    # surfaced exactly once in the deployment-creation event payload;
    # only the SHA-256 hash + expiry persist here. The public
    # approve_deployment_by_token / reject_deployment_by_token
    # mutations match presented tokens against this hash — the token
    # IS the auth proof, so the resolver runs unauthenticated.
    approval_token_hash = models.CharField(max_length=128, blank=True, default="")
    approval_token_expires_at = models.DateTimeField(null=True, blank=True)
    approval_token_used_at = models.DateTimeField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["registered_app", "app_environment", "-created_at"],
                name="deploy_app_env_created_idx",
            ),
            models.Index(fields=["status"], name="deploy_status_idx"),
        ]

    _TRANSITIONS = {
        Status.PENDING_APPROVAL: {Status.PENDING, Status.FAILED},
        Status.PENDING: {Status.DEPLOYING, Status.FAILED},
        Status.DEPLOYING: {Status.RUNNING, Status.FAILED},
        Status.RUNNING: {Status.REDEPLOYING, Status.ROLLED_BACK, Status.SUPERSEDED, Status.FAILED},
        Status.REDEPLOYING: {Status.RUNNING, Status.FAILED},
        Status.FAILED: set(),
        Status.SUPERSEDED: set(),
        Status.ROLLED_BACK: set(),
    }

    def transition_to(self, new_status: Deployment.Status) -> None:
        current = Deployment.Status(self.status)
        allowed = self._TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(f"Deployment({self.pk}) cannot transition {current.value} → {new_status.value}")
        now = timezone.now()
        self.status = new_status.value
        if new_status is Deployment.Status.DEPLOYING and not self.started_at:
            self.started_at = now
        elif new_status is Deployment.Status.RUNNING:
            self.succeeded_at = self.succeeded_at or now
            self.ended_at = now
        elif new_status is Deployment.Status.FAILED:
            self.failed_at = now
            self.ended_at = now
        elif new_status in {
            Deployment.Status.SUPERSEDED,
            Deployment.Status.ROLLED_BACK,
        }:
            self.ended_at = self.ended_at or now

        if self.started_at and self.ended_at:
            self.duration_seconds = int((self.ended_at - self.started_at) / timedelta(seconds=1))

        self.save(
            update_fields=[
                "status",
                "started_at",
                "succeeded_at",
                "failed_at",
                "ended_at",
                "duration_seconds",
                "updated_at",
                "version",
            ]
        )

        # Publish the lifecycle event to any active subscribers.
        # In-process for now; multi-worker deployments swap the
        # broker for Redis without touching this call site.
        # Wrapped so a broker error never breaks a transition.
        try:
            from core.pubsub import publish_sync

            event = {
                "deployment_id": str(self.guid),
                "registered_app_slug": (self.registered_app.slug if self.registered_app_id else ""),
                "environment_name": (self.app_environment.name if self.app_environment_id else ""),
                "status": self.status,
                "occurred_at": now.isoformat(),
            }
            org_id = self.registered_app.organization_id if self.registered_app_id else None
            if org_id is not None:
                publish_sync(f"deployment.lifecycle.{org_id}", event)
            if self.registered_app_id:
                publish_sync(
                    f"deployment.lifecycle.app.{self.registered_app.guid}",
                    event,
                )
        except Exception:
            import logging

            logging.getLogger(__name__).warning("deployment.lifecycle publish failed", exc_info=True)
