"""
Cron-deploy activity — one tick of the cron-dispatch loop (#296).

Each invocation:
  1. Loads every active cron-triggered ``RegisteredApp`` row.
  2. Filters via :mod:`astrolift_workflows.cron_deploy` policy
     (skip paused, skip soft-deleted, match the expression to the
     current minute).
  3. For every match, creates a ``Deployment`` in the same shape
     the GraphQL ``startDeployment`` mutation would and enqueues a
     ``DeployAppWorkflow`` against the app's primary environment.
  4. Emits a ``deployment.cron.fired`` event so the deployment
     history page can show ``source='cron'`` as the trigger reason.

Idempotency: the single-flight workflow id
``DeployAppWorkflow-<app-guid>-<env-guid>`` means double-firing the
same tick (e.g. on Temporal restart) is a no-op — Temporal rejects
the duplicate start, and the dispatcher swallows it.
"""

from __future__ import annotations

import dataclasses
import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.cron_deploy")


@dataclasses.dataclass(frozen=True, slots=True)
class CronDispatchSummary:
    """Per-tick summary so the workflow / tests can assert what fired."""

    candidates_count: int
    fired_count: int
    fired_app_slugs: tuple[str, ...]


@activity.defn(name="astrolift.cron.dispatch_tick")
async def dispatch_cron_deploys() -> CronDispatchSummary:
    """One tick of the cron dispatcher. Lazily imports Django models
    so the workflow sandbox stays clean."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_dispatch_cron_deploys_sync, thread_sensitive=False)()


def _dispatch_cron_deploys_sync() -> CronDispatchSummary:
    from django.utils import timezone

    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_operations.models import WorkflowRun
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows import client as wf_client
    from astrolift_workflows.cron_deploy import (
        CronDispatchCandidate,
        select_matches,
    )
    from astrolift_workflows.inputs import Actor, DeployAppInput

    now = timezone.now()

    apps = (
        RegisteredApp.objects.filter(
            trigger_mode=RegisteredApp.TriggerMode.CRON.value,
            deleted_at__isnull=True,
            is_active=True,
        )
        .exclude(cron_expression="")
        .only(
            "id",
            "guid",
            "slug",
            "cron_expression",
            "cron_paused",
            "is_active",
            "organization_id",
        )
    )

    # Resolve the "primary" environment for each app — for the v1 of
    # cron dispatch we pick the alphabetically-first non-paused env
    # (typically 'prod' or 'production'); operators with multiple envs
    # land a future ticket to pin a preferred env per cron app.
    env_by_app: dict[int, AppEnvironment] = {}
    if apps:
        envs = AppEnvironment.objects.filter(
            registered_app_id__in=[a.id for a in apps],
            deleted_at__isnull=True,
            deploys_paused=False,
        ).order_by("name")
        for env in envs:
            env_by_app.setdefault(env.registered_app_id, env)

    candidates: list[CronDispatchCandidate] = []
    app_by_id: dict[int, RegisteredApp] = {a.id: a for a in apps}
    for a in apps:
        env = env_by_app.get(a.id)
        candidates.append(
            CronDispatchCandidate(
                app_id=a.id,
                app_slug=a.slug,
                app_guid=str(a.guid),
                cron_expression=a.cron_expression or "",
                cron_paused=bool(a.cron_paused),
                primary_environment_name=env.name if env is not None else None,
            )
        )

    matches = select_matches(candidates=candidates, now=now)

    fired: list[str] = []
    for match in matches:
        app = app_by_id[match.app_id]
        env = env_by_app[match.app_id]
        # Re-derive image_tag from the most recent successful deploy
        # in this env; absent that, fall back to 'latest' which the
        # deploy workflow's pre_flight activity will validate against
        # the registry. The point is: cron deploys re-roll the latest
        # known good image rather than minting a new tag here.
        last_success = (
            Deployment.objects.filter(
                registered_app=app,
                app_environment=env,
                status=Deployment.Status.RUNNING.value,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        image_tag = last_success.image_tag if last_success else "latest"
        image_digest = last_success.image_digest if last_success else ""

        deployment = Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            trigger_kind=Deployment.TriggerKind.SCHEDULED.value,
            status=Deployment.Status.PENDING.value,
            image_tag=image_tag,
            image_digest=image_digest,
            approvals_required=0,
            approvals_received=0,
            ci_actor_kind="cron",
        )

        wf_id = f"DeployAppWorkflow-{app.guid}-{env.guid}"
        actor = Actor(kind="system", display="cron-dispatch")
        try:
            handle = wf_client.start_workflow(
                "DeployAppWorkflow",
                args=[
                    DeployAppInput(
                        registered_app_id=app.pk,
                        app_environment_id=env.pk,
                        image_tags={"app": image_tag},
                        trigger_kind=Deployment.TriggerKind.SCHEDULED.value,
                        actor=actor,
                    )
                ],
                workflow_id=wf_id,
            )
        except Exception:
            # A duplicate-id rejection from Temporal lands here — the
            # other tick already enqueued this deploy. Mark the row as
            # superseded so it doesn't dangle in PENDING.
            log.warning("cron-dispatch start_workflow failed", exc_info=True)
            deployment.delete()  # safe: it was just created; nothing references it yet
            continue

        if handle.enqueued:
            run = WorkflowRun.objects.create(
                workflow_kind="DeployAppWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id or "",
                status=WorkflowRun.Status.RUNNING,
                started_at=now,
                organization_id=app.organization_id,
                registered_app_id=app.pk,
                app_environment_id=env.pk,
                trigger_actor_user_id=None,
                trigger_actor_token_kind="cron",
                trigger_actor_token_id=None,
            )
            deployment.workflow_run = run
            deployment.save(update_fields=["workflow_run", "updated_at", "version"])

        # Publish a deploy-event so the deployment-history page can
        # surface the cron trigger reason. Wrapped so a broker error
        # never breaks dispatch.
        try:
            from core.pubsub import publish_sync

            publish_sync(
                f"deployment.cron.fired.{app.organization_id}",
                {
                    "deployment_id": str(deployment.guid),
                    "registered_app_slug": app.slug,
                    "environment_name": env.name,
                    "source": "cron",
                    "cron_expression": app.cron_expression or "",
                    "occurred_at": now.isoformat(),
                },
            )
        except Exception:
            log.warning("deployment.cron.fired publish failed", exc_info=True)

        fired.append(app.slug)

    return CronDispatchSummary(
        candidates_count=len(candidates),
        fired_count=len(fired),
        fired_app_slugs=tuple(fired),
    )
