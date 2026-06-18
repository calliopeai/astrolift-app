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


@dataclasses.dataclass(frozen=True, slots=True)
class AgentCronDispatchSummary:
    """Per-tick summary for the agent-cron tick (spec 33, PR-4).

    The Task-dispatch counterpart to :class:`CronDispatchSummary`.
    ``fired_workload_slugs`` are the agent Workloads we dispatched a
    Task for this tick; ``fired_task_guids`` are the AgentTask rows
    created (one per fired workload) so tests can assert the dispatch
    path ran, not an app deploy."""

    candidates_count: int
    fired_count: int
    fired_workload_slugs: tuple[str, ...]
    fired_task_guids: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ScaleTickSummary:
    """Per-tick summary for the scheduled-scaling tick (spec 33, PR-5).

    ``scaled_workload_slugs`` are the Service agents whose Deployment we
    patched this tick; ``scaled_to`` is the parallel replica count we
    patched each to (so tests can assert which workload went to X vs 0).
    Candidates already at target (idempotent skip) and those whose cron
    didn't match are counted in ``candidates_count`` but not ``scaled``.
    """

    candidates_count: int
    scaled_count: int
    scaled_workload_slugs: tuple[str, ...]
    scaled_to: tuple[int, ...]


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
            # App-global webhook-deploy pause (#399) blocks scheduled
            # dispatch — ``scheduled`` is a webhook-shaped trigger
            # kind. Filtering at the query is simpler than letting
            # the ``select_matches`` policy decide; this layer already
            # excludes soft-deleted + inactive rows.
            webhook_deploys_paused=False,
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
                        deployment_id=deployment.pk,
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


# ---------------------------------------------------------------------------
# Agent-cron tick (spec 33, PR-4)
# ---------------------------------------------------------------------------
#
# The Task-dispatch sibling of the app-deploy tick above. It selects agent
# ``Workload`` rows whose run-spec says "schedule" and dispatches an AgentTask
# at each cron match — by going THROUGH the same PR-1 dispatch path the
# ``runAstroliftAgent`` mutation uses (create AgentTask in QUEUED with
# ``agent_definition`` set, then start ``DispatchAgentTaskWorkflow``), NOT by
# reinventing dispatch and NOT by minting an app Deployment.
#
# The two selectors are mutually exclusive by construction:
#   * the deploy tick iterates ``RegisteredApp`` rows on ``trigger_mode='cron'``
#     (an app-level field) and fires ``DeployAppWorkflow``;
#   * this tick iterates ``Workload`` rows on
#     ``kind='agent' AND run_family='task' AND run_mode='schedule'``
#     (workload-level run-spec fields) and fires ``DispatchAgentTaskWorkflow``.
# An app has no ``run_mode``; a Workload has no ``trigger_mode``. A
# ``run_family='service'`` agent is excluded here (it deploys through the app
# Deployment path instead), so a schedule agent is never deployed-as-app and an
# app is never dispatched-as-task.


@activity.defn(name="astrolift.agent.cron_dispatch_tick")
async def dispatch_agent_crons() -> AgentCronDispatchSummary:
    """One tick of the agent-cron dispatcher. Lazily imports Django
    models so the workflow sandbox stays clean."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_dispatch_agent_crons_sync, thread_sensitive=False)()


def _dispatch_agent_crons_sync() -> AgentCronDispatchSummary:
    from django.db import transaction
    from django.utils import timezone

    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows import client as wf_client
    from astrolift_workflows.cron_deploy import (
        AgentCronDispatchCandidate,
        select_agent_matches,
    )
    from astrolift_workflows.inputs import Actor, DispatchAgentTaskInput

    now = timezone.now()

    # Select agent Workloads in Schedule mode. ``run_family='task'`` is the
    # explicit guard so a Service-family agent (which deploys through the app
    # Deployment path) can never be dispatched as a Task here, even if its
    # run_mode were left at 'schedule'. Soft-deleted workloads and apps are
    # excluded so a torn-down agent stops dispatching.
    workloads = (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT.value,
            run_family=Workload.RunFamily.TASK.value,
            run_mode=Workload.RunMode.SCHEDULE.value,
            deleted_at__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        .exclude(run_cron_expression="")
        .select_related("registered_app")
        .only(
            "id",
            "guid",
            "slug",
            "run_cron_expression",
            "run_paused",
            "registered_app__organization_id",
        )
    )

    candidates: list[AgentCronDispatchCandidate] = []
    workload_by_id: dict[int, Workload] = {w.id: w for w in workloads}
    for w in workloads:
        candidates.append(
            AgentCronDispatchCandidate(
                workload_id=w.id,
                workload_slug=w.slug,
                workload_guid=str(w.guid),
                organization_id=w.registered_app.organization_id,
                run_cron_expression=w.run_cron_expression or "",
                run_paused=bool(w.run_paused),
            )
        )

    matches = select_agent_matches(candidates=candidates, now=now)

    fired_slugs: list[str] = []
    fired_task_guids: list[str] = []
    for match in matches:
        workload = workload_by_id[match.workload_id]
        app = workload.registered_app

        # Create the AgentTask exactly the way ``run_astrolift_agent`` does:
        # ``agent_definition`` set (the K8s Job spawner requires it to render
        # the pod image), DRAFT then advanced to QUEUED via the sanctioned
        # ``transition_to`` so ``queued_at`` is stamped. The scheduled tick
        # supplies no environment spec (cron dispatch is unattended), so VNC
        # is off and the task launches from the workload's own image/runtime.
        try:
            with transaction.atomic():
                task = AgentTask.objects.create(
                    organization_id=app.organization_id,
                    agent_definition=workload,
                    status=AgentTask.Status.DRAFT,
                    timeout_seconds=int(workload.tool_timeout_seconds or 300),
                )
                task.transition_to(AgentTask.Status.QUEUED)
        except Exception:
            log.warning(
                "agent-cron create-task failed for workload %s",
                workload.slug,
                exc_info=True,
            )
            continue

        # Enqueue the durable dispatch through the PR-1 path. Workflow id is
        # keyed to the task guid so a duplicate fire of THIS task joins the
        # in-flight run; a fresh tick mints a new task (new guid) so back-to-
        # back cron matches each get their own dispatch. When Temporal is
        # disabled (dev/CI) this is a logged no-op and the task stays QUEUED.
        actor = Actor(kind="system", display="agent-cron")
        try:
            wf_client.start_workflow(
                "DispatchAgentTaskWorkflow",
                args=[DispatchAgentTaskInput(agent_task_id=task.pk, actor=actor)],
                workflow_id=f"DispatchAgentTaskWorkflow-{task.guid}",
            )
        except Exception:
            # A start failure leaves the task QUEUED; a later tick won't
            # re-dispatch THIS task (it mints a new one), so fail the orphan
            # so it doesn't dangle as a phantom queued run.
            log.warning(
                "agent-cron start_workflow failed for task %s",
                task.guid,
                exc_info=True,
            )
            task.failure = {"message": "agent-cron dispatch start failed"}
            task.save(update_fields=["failure", "updated_at", "version"])
            try:
                task.transition_to(AgentTask.Status.FAILED)
            except ValueError:
                pass
            continue

        # Publish an agent-dispatch event so the agent executions surface can
        # show the cron trigger reason. Wrapped so a broker error never breaks
        # dispatch (mirrors the deploy tick's publish guard).
        try:
            from core.pubsub import publish_sync

            publish_sync(
                f"agent.cron.dispatched.{app.organization_id}",
                {
                    "task_guid": str(task.guid),
                    "workload_slug": workload.slug,
                    "source": "cron",
                    "run_cron_expression": workload.run_cron_expression or "",
                    "occurred_at": now.isoformat(),
                },
            )
        except Exception:
            log.warning("agent.cron.dispatched publish failed", exc_info=True)

        fired_slugs.append(workload.slug)
        fired_task_guids.append(str(task.guid))

    return AgentCronDispatchSummary(
        candidates_count=len(candidates),
        fired_count=len(fired_slugs),
        fired_workload_slugs=tuple(fired_slugs),
        fired_task_guids=tuple(fired_task_guids),
    )


# ---------------------------------------------------------------------------
# Scheduled-scaling tick (spec 33, PR-5)
# ---------------------------------------------------------------------------
#
# The replica-patch sibling of the two ticks above. It selects
# ``run_family='service'`` agent ``Workload`` rows carrying a scale-up
# and/or scale-down cron and, at each cron match, patches the Service's
# Deployment replicas via the existing ``scale_workload`` service
# (bounds-checked) — to ``scheduled_scale_to`` (X) on the up-cron, to 0
# on the down-cron. NO dispatch, NO app Deployment row: a Service agent's
# pods already exist (it deployed through the app-deploy path); this only
# changes how many of them run.
#
# Mutually exclusive from the dispatch + deploy ticks by the same
# ``run_family`` axis the agent-cron selector uses: this tick reads ONLY
# ``run_family='service'`` agents, so a Task-family agent (acceptance (4))
# and a plain app are both invisible to it.
#
# ``scale_workload`` enforces the env max bound (raising VALIDATION when
# out of range) but does NOT no-op at target — so the selector clamps the
# up-target to the env ceiling and skips the patch when already at target
# (idempotency, acceptance (3)). A successful scale also writes the new
# count back onto ``Workload.replicas`` (the field the manifest renderer
# reads) so the next tick's idempotency check is accurate and a later
# redeploy doesn't silently revert the scheduled scale.


@activity.defn(name="astrolift.agent.scale_tick")
async def dispatch_scale_ticks() -> ScaleTickSummary:
    """One tick of the scheduled-scaling loop. Lazily imports Django
    models so the workflow sandbox stays clean."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_dispatch_scale_ticks_sync, thread_sensitive=False)()


def _dispatch_scale_ticks_sync() -> ScaleTickSummary:
    from django.db import models as dj_models
    from django.utils import timezone

    from astrolift_lifecycle.services.k8s_ops import (
        K8sOpError,
        _primary_environment_for_workload,
        resolve_replica_bounds,
        scale_workload,
    )
    from astrolift_registry.models import Workload
    from astrolift_workflows.cron_deploy import (
        ScaleTickCandidate,
        select_scale_matches,
    )

    now = timezone.now()

    # Select Service-family agent Workloads that carry at least one scale
    # cron. ``run_family='service'`` is the explicit guard so a Task-family
    # agent is never scaled here (acceptance (4)); ``run_mode`` is
    # irrelevant for a Service (it's always-on, not dispatched). Soft-deleted
    # workloads and apps are excluded so a torn-down agent stops scaling.
    workloads = (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT.value,
            run_family=Workload.RunFamily.SERVICE.value,
            deleted_at__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        # At least one scale cron set (mirrors the deploy tick's
        # ``.exclude(cron_expression="")`` but for the OR of the pair).
        .filter(dj_models.Q(scale_up_cron__gt="") | dj_models.Q(scale_down_cron__gt=""))
        .select_related("registered_app")
        .only(
            "id",
            "guid",
            "slug",
            "replicas",
            "scheduled_scale_to",
            "scale_up_cron",
            "scale_down_cron",
            "registered_app__organization_id",
        )
    )

    candidates: list[ScaleTickCandidate] = []
    workload_by_id: dict[int, Workload] = {w.id: w for w in workloads}
    for w in workloads:
        # Resolve the env ceiling the same way ``scale_workload`` will, so
        # the selector clamps to the identical bound (and the idempotency
        # comparison is against the clamped target). No active env → the
        # platform default ceiling, and the scale_workload call below will
        # surface PRECONDITION if the env/cluster truly can't be resolved.
        env = _primary_environment_for_workload(w)
        _lower, upper = resolve_replica_bounds(env)
        candidates.append(
            ScaleTickCandidate(
                workload_id=w.id,
                workload_slug=w.slug,
                workload_guid=str(w.guid),
                organization_id=w.registered_app.organization_id,
                scheduled_scale_to=w.scheduled_scale_to,
                scale_up_cron=w.scale_up_cron or "",
                scale_down_cron=w.scale_down_cron or "",
                current_replicas=int(w.replicas),
                max_replicas=int(upper),
            )
        )

    actions = select_scale_matches(candidates=candidates, now=now)

    scaled_slugs: list[str] = []
    scaled_to: list[int] = []
    for action in actions:
        workload = workload_by_id[action.workload_id]
        app = workload.registered_app
        try:
            scale_workload(workload, action.target_replicas)
        except K8sOpError:
            # Cluster not resolvable / Deployment not found / driver error.
            # Log and move on; a later tick retries (the cron still matches
            # the next minute for ``*`` schedules, and the idempotency skip
            # means a recovered cluster converges).
            log.warning(
                "scale-tick scale_workload failed for workload %s (target=%d)",
                workload.slug,
                action.target_replicas,
                exc_info=True,
            )
            continue

        # Persist the new desired count on the workload so the next tick's
        # idempotency check is accurate and a subsequent manifest redeploy
        # renders the scheduled count rather than reverting it.
        workload.replicas = action.target_replicas
        workload.save(update_fields=["replicas", "updated_at", "version"])

        # Publish a scale event so the agent surface can show the scheduled
        # scale reason. Wrapped so a broker error never breaks the tick
        # (mirrors the deploy + dispatch ticks' publish guard).
        try:
            from core.pubsub import publish_sync

            publish_sync(
                f"agent.scale.scheduled.{app.organization_id}",
                {
                    "workload_slug": workload.slug,
                    "workload_guid": str(workload.guid),
                    "direction": action.direction,
                    "target_replicas": action.target_replicas,
                    "source": "scale_cron",
                    "occurred_at": now.isoformat(),
                },
            )
        except Exception:
            log.warning("agent.scale.scheduled publish failed", exc_info=True)

        scaled_slugs.append(workload.slug)
        scaled_to.append(action.target_replicas)

    return ScaleTickSummary(
        candidates_count=len(candidates),
        scaled_count=len(scaled_slugs),
        scaled_workload_slugs=tuple(scaled_slugs),
        scaled_to=tuple(scaled_to),
    )
