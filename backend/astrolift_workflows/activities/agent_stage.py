"""
Temporal activities for agentic workflow stage execution.

Activities:
  execute_agent_stage(params) -> dict
    Creates an AgentTask, assembles a Brief from config_repo if set,
    dispatches via K8sJobSpawner, polls until terminal, returns result.

  cancel_agent_stage(task_guid) -> dict
    Cancels an in-flight agent task and reports whether the hard stop settled.

Pattern
-------
Django ORM + the dispatch system are sync; every Django- or spawner-touching
operation runs inside a ``_sync`` helper invoked through ``sync_to_async`` so
the Temporal activity coroutine never blocks the event loop and never imports
Django at workflow-sandbox import time (mirrors ``pipeline_job_spawn``).

``execute_agent_stage`` is a single long-running activity that owns the whole
spawn -> poll -> terminal lifecycle of one stage. It heartbeats on every poll
so Temporal can detect a wedged worker; the polling cadence is
``_POLL_INTERVAL_SECONDS``. This differs from the pipeline pattern (which
decomposes spawn/poll into separate short activities driven by the workflow)
because an agent stage is one indivisible dispatch — there is no per-job DAG to
fan out at the workflow layer.

Dispatch backend
----------------
Agent jobs run on the org's first ``managed`` :class:`TenantCluster` in a
dedicated per-org namespace (``astrolift-agents-<org-slug>``), spawned through
the canonical :func:`astrolift_dispatch.spawners.registry.get_spawner`
``"k8s_job"`` backend. The spawner renders the Job from the task's
``agent_definition`` Workload image and injects the Brief identity env vars.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from _sdk.k8s_naming import AGENT_NAMESPACE_PREFIX, agent_namespace
from django.utils import timezone
from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.agent_stage")

# Agent stage jobs run in a dedicated per-org namespace, separate from the
# tenant app namespaces and the pipeline namespaces. Derived through the shared
# helper: organization slugs allow 200 characters and a namespace allows 63, so
# interpolating the slug produced an invalid namespace for a valid slug (#1379).
_AGENT_NS_PREFIX = f"{AGENT_NAMESPACE_PREFIX}-"

# Seconds between status polls / heartbeats while a task is non-terminal.
_POLL_INTERVAL_SECONDS = 10

# AgentTask statuses that mean "no longer running" — the poll loop exits.
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "timed_out",
        "cancelled",
    }
)


# ---------------------------------------------------------------------------
# Sync helpers (Django- / dispatch-touching, called via sync_to_async)
# ---------------------------------------------------------------------------


def _agent_namespace(org_slug: str) -> str:
    return agent_namespace(org_slug)


def _skill_org_or_global_q(organization: Any):
    """Q filter matching skills owned by ``organization`` OR global (null org)."""
    from django.db.models import Q

    return Q(organization=organization) | Q(organization__isnull=True)


def _resolve_managed_cluster(organization: Any) -> Any:
    """Return the org's ``managed`` TenantCluster for agent dispatch, or raise.

    Delegates to :func:`astrolift_agents.services.agent_cluster.resolve_agent_cluster`
    so the spawn path, the secret-value mutations, and the secret-status
    query all resolve the *same* cluster (and therefore the same secret
    store) for a given org — the write/read symmetry #1173 depends on.
    """
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster

    return resolve_agent_cluster(organization)


def _create_agent_task_sync(params: dict[str, Any]) -> int:
    """Create the AgentTask (DRAFT), assemble + link a Brief, advance to QUEUED.

    Returns the AgentTask pk for the spawn/poll helpers.

    Steps (issue brief, in order):
      1. Resolve Organization by slug.
      2. Resolve the optional Skill and AgentEnvironmentSpec by slug.
      3. Create the AgentTask in DRAFT.
      4. If the env spec sets ``config_repo`` -> assemble a Brief and link it.
      5. If a Skill is set -> its ``content`` is the system prompt, folded
         into the Brief manifest so the dispatch handshake serves it.
      6. Advance the task DRAFT -> QUEUED.
    """
    from astrolift_agents.models import (
        AgentEnvironmentSpec,
        AgentTask,
        Skill,
    )
    from astrolift_agents.services.brief_assembler import assemble_agent_brief
    from astrolift_identity.models import Organization

    org_slug = params["org_slug"]
    skill_slug = params.get("skill_slug") or ""
    env_spec_slug = params.get("environment_spec_slug") or ""
    prompt = params.get("prompt") or ""
    context = dict(params.get("context") or {})
    output_key = params["output_key"]
    timeout_seconds = int(params.get("timeout_seconds") or 3600)

    organization = Organization.objects.filter(slug=org_slug, deleted_at__isnull=True).first()
    if organization is None:
        raise RuntimeError(f"organization {org_slug!r} not found")

    # Skill lookup: an org-scoped skill wins over a global one of the same
    # slug; both are matched in a single query and disambiguated below.
    skill: Skill | None = None
    if skill_slug:
        candidates = list(
            Skill.objects.filter(
                slug=skill_slug,
                deleted_at__isnull=True,
                is_active=True,
            ).filter(_skill_org_or_global_q(organization))
        )
        # Prefer the org-owned skill; fall back to the global (null-org) one.
        skill = next(
            (s for s in candidates if s.organization_id == organization.id),
            None,
        ) or next((s for s in candidates if s.organization_id is None), None)
        if skill is None:
            raise RuntimeError(f"skill {skill_slug!r} not found for org {org_slug!r}")

    env_spec: AgentEnvironmentSpec | None = None
    if env_spec_slug:
        env_spec = AgentEnvironmentSpec.objects.filter(
            organization=organization,
            slug=env_spec_slug,
            deleted_at__isnull=True,
        ).first()
        if env_spec is None:
            raise RuntimeError(f"environment spec {env_spec_slug!r} not found for org {org_slug!r}")

    task = AgentTask.objects.create(
        organization=organization,
        environment_spec=env_spec,
        status=AgentTask.Status.DRAFT,
        timeout_seconds=timeout_seconds,
        # Freeze VNC eligibility from the spec so the task stays
        # self-describing if the spec is later edited or deleted.
        vnc_enabled=bool(env_spec and env_spec.vnc_enabled),
    )

    # Context folded into the Brief / dispatch handshake. ``output_key`` is
    # carried so a downstream stage can locate this stage's result.
    task_context: dict[str, Any] = {
        "task_guid": str(task.guid),
        "org_slug": org_slug,
        "output_key": output_key,
        **context,
    }
    if prompt:
        task_context["prompt"] = prompt

    system_prompt = skill.content if skill is not None else prompt

    if env_spec is not None and env_spec.config_repo:
        brief = assemble_agent_brief(
            organization=organization,
            config_repo=env_spec.config_repo,
            config_branch=env_spec.config_branch or "main",
            manifest_path=env_spec.config_manifest_path or "",
            context=task_context,
            ttl_seconds=timeout_seconds,
        )
        # A skill's content (or the raw prompt) is the system prompt the agent
        # boots with. ``assemble_agent_brief`` may dedup-return a shared READY
        # Brief; only overlay the system prompt when this stage actually
        # supplies one so we never blank out an existing manifest value.
        if system_prompt:
            manifest = dict(brief.manifest_snapshot or {})
            manifest["system_prompt"] = system_prompt
            if skill is not None:
                manifest["skill_slug"] = skill.slug
            brief.manifest_snapshot = manifest
            brief.save(update_fields=["manifest_snapshot", "updated_at", "version"])
        task.brief = brief
        task.save(update_fields=["brief", "updated_at", "version"])

    task.transition_to(AgentTask.Status.QUEUED)
    log.info(
        "execute_agent_stage: created+queued AgentTask %s (org=%s skill=%s env_spec=%s brief=%s)",
        task.guid,
        org_slug,
        skill_slug or "-",
        env_spec_slug or "-",
        task.brief_id or "-",
    )
    return task.pk


def _spawn_agent_task_sync(task_pk: int) -> dict[str, Any]:
    """Advance QUEUED -> PROVISIONING and spawn the job via the dispatch system.

    Returns ``{"external_id": str, "ok": bool, "error": str}`` from the
    spawner's ``SpawnResult``. On a spawn error the task is moved to FAILED so
    the poll loop sees a terminal state immediately.
    """
    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner

    task = AgentTask.objects.select_related("organization", "agent_definition", "brief").get(pk=task_pk)

    organization = task.organization
    namespace = _agent_namespace(organization.slug)
    try:
        cluster = _resolve_managed_cluster(organization)
    except Exception as exc:  # noqa: BLE001 — a queued task must settle visibly
        return _settle_spawn_failure(task.pk, f"cluster resolution failed: {exc}")

    try:
        task.transition_to(AgentTask.Status.PROVISIONING)
    except ValueError:
        # Cancellation can win before the activity reaches the spawn call.
        # Do not turn that expected race into a failed Temporal activity.
        task.refresh_from_db()
        return {
            "external_id": task.external_id,
            "ok": False,
            "error": f"task became {task.status} before its job was created",
        }

    spawner = get_spawner("k8s_job", cluster=cluster, namespace=namespace)
    try:
        result = spawner.spawn(task)
    except Exception as exc:  # noqa: BLE001 — renderer/driver bugs must not strand PROVISIONING
        return _settle_spawn_failure(task.pk, f"spawn raised {type(exc).__name__}: {exc}")

    # Cancellation can race the external create: the operator may cancel after
    # we enter ``spawn()`` but before the returned Job id is persisted.  In that
    # window the cancel path cannot see an external id to delete.  Re-read the
    # row after the external call and tear down the just-created Job ourselves
    # instead of resurrecting the cancelled task with a live orphan pod.
    task.refresh_from_db()
    if task.status in _TERMINAL_STATUSES:
        if result.ok and result.external_id:
            try:
                spawner.stop(result.external_id)
            except Exception:  # noqa: BLE001 — task remains terminal; surface via logs
                log.exception(
                    "execute_agent_stage: cleanup failed for job %s created after task %s became %s",
                    result.external_id,
                    task.guid,
                    task.status,
                )
        return {
            "external_id": result.external_id,
            "ok": False,
            "error": f"task became {task.status} while its job was being created",
        }

    if not result.ok:
        log.warning(
            "execute_agent_stage: spawn failed for task %s: %s",
            task.guid,
            result.error,
        )
        return _settle_spawn_failure(
            task.pk,
            f"spawn failed: {result.error}",
            external_id=result.external_id,
            response_error=result.error,
        )

    task.external_id = result.external_id
    task.pod_name = result.external_id
    task.namespace = namespace
    task.save(update_fields=["external_id", "pod_name", "namespace", "updated_at", "version"])
    log.info(
        "execute_agent_stage: spawned job %s for task %s in namespace %s",
        result.external_id,
        task.guid,
        namespace,
    )
    return {"external_id": result.external_id, "ok": True, "error": ""}


def _settle_spawn_failure(
    task_pk: int,
    message: str,
    *,
    external_id: str = "",
    response_error: str = "",
) -> dict[str, Any]:
    """Atomically make a pre-spawn failure terminal unless cancellation won."""
    from django.db import transaction

    from astrolift_agents.models import AgentTask

    with transaction.atomic():
        task = AgentTask.objects.select_for_update().get(pk=task_pk)
        if task.status not in _TERMINAL_STATUSES:
            task.failure = {"message": message}
            task.save(update_fields=["failure", "updated_at", "version"])
            task.transition_to(AgentTask.Status.FAILED)
        return {
            "external_id": external_id or task.external_id,
            "ok": False,
            "error": response_error or message,
        }


def _poll_agent_task_sync(task_pk: int) -> dict[str, Any]:
    """Reconcile the spawned container's status onto the AgentTask, once.

    Reads the live container status from the dispatch backend and walks the
    AgentTask forward (PROVISIONING -> RUNNING -> terminal) accordingly. The
    Dispatch Service may also be advancing the task via its callback API, so
    every transition is guarded — a state already advanced by a race is fine.

    Returns ``{"status": <agent task status>, "terminal": bool}``.
    """
    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner

    task = AgentTask.objects.select_related("organization").get(pk=task_pk)

    # Already terminal (e.g. the dispatch callback finished it). Remove only
    # the plaintext-bearing per-task Secret; keep the completed Job/pod long
    # enough for the operator log surface to inspect it.
    if task.status in _TERMINAL_STATUSES:
        _cleanup_terminal_task_secret(task)
        return {"status": task.status, "terminal": True}

    # The Job also carries activeDeadlineSeconds, but enforce the same limit
    # control-plane-side so the AgentTask reaches TIMED_OUT promptly and the
    # per-task Secret is removed with the Job. Use provisioning_at because the
    # deadline begins when dispatch creates the Job, including pod scheduling.
    started = task.provisioning_at or task.started_at or task.queued_at
    timeout_seconds = max(1, int(task.timeout_seconds or 300))
    if started is not None and (timezone.now() - started).total_seconds() >= timeout_seconds:
        if task.external_id:
            try:
                cluster = _resolve_managed_cluster(task.organization)
                namespace = task.namespace or _agent_namespace(task.organization.slug)
                get_spawner("k8s_job", cluster=cluster, namespace=namespace).stop(task.external_id)
            except Exception:  # noqa: BLE001 — K8s deadline remains the backstop
                log.warning("agent timeout: container stop failed for task %s", task.guid, exc_info=True)
        task.failure = {"message": f"agent exceeded its {timeout_seconds}s timeout"}
        task.save(update_fields=["failure", "updated_at", "version"])
        _advance_to_terminal(task, AgentTask.Status.TIMED_OUT)
        return {"status": task.status, "terminal": True}

    if not task.external_id:
        # Spawn may still be in flight. The deadline check above guarantees a
        # lost worker / stuck provisioning row cannot spin forever.
        return {"status": task.status, "terminal": False}

    cluster = _resolve_managed_cluster(task.organization)
    namespace = task.namespace or _agent_namespace(task.organization.slug)
    spawner = get_spawner("k8s_job", cluster=cluster, namespace=namespace)

    status = spawner.status(task.external_id)

    if status.running and task.status == AgentTask.Status.PROVISIONING:
        try:
            task.transition_to(AgentTask.Status.RUNNING)
        except ValueError:
            pass  # raced past PROVISIONING via the dispatch callback — fine

    if status.succeeded:
        if task.result is None:
            task.result = {"exit_code": status.exit_code or 0}
            task.save(update_fields=["result", "updated_at", "version"])
        _advance_to_terminal(task, AgentTask.Status.COMPLETED)
        _cleanup_terminal_task_secret(task, spawner=spawner)
        return {"status": task.status, "terminal": True}

    if status.failed:
        if task.failure is None:
            task.failure = {
                "message": status.error_message or "container failed",
                "exit_code": status.exit_code,
            }
            task.save(update_fields=["failure", "updated_at", "version"])
        _advance_to_terminal(task, AgentTask.Status.FAILED)
        _cleanup_terminal_task_secret(task, spawner=spawner)
        return {"status": task.status, "terminal": True}

    # A pod wedged in a fatal image/config waiting state never produces a Job
    # Complete/Failed condition — its container never starts — so the Job-
    # condition check above reports running=True indefinitely. Without this
    # gate a missing/renamed image (ImagePullBackOff), a malformed ref
    # (InvalidImageName), or a missing secret/configmap
    # (CreateContainerConfigError) would leave the task RUNNING forever
    # instead of failing. Only the reasons that never self-heal are fatal;
    # transient startup states (ContainerCreating, a first-attempt
    # ErrImagePull) fall through and get another poll.
    fatal = _fatal_pod_wait_reason(cluster, namespace, str(task.guid))
    if fatal:
        if task.failure is None:
            task.failure = {"message": f"container never started: {fatal}"}
            task.save(update_fields=["failure", "updated_at", "version"])
        # These startup states cannot recover. Delete the live Job rather than
        # merely marking the row failed and leaving a pod until its deadline.
        try:
            spawner.stop(task.external_id)
        except Exception:  # noqa: BLE001 — activeDeadlineSeconds remains the backstop
            log.warning("agent fatal-start cleanup failed for task %s", task.guid, exc_info=True)
        _advance_to_terminal(task, AgentTask.Status.FAILED)
        return {"status": task.status, "terminal": True}

    # Still running / not yet terminal.
    return {"status": task.status, "terminal": False}


def _cleanup_terminal_task_secret(task: Any, *, spawner: Any | None = None) -> None:
    """Best-effort removal of the temporary K8s Secret after task settlement."""
    if not getattr(task, "external_id", ""):
        return
    try:
        if spawner is None:
            from astrolift_dispatch.spawners.registry import get_spawner

            cluster = _resolve_managed_cluster(task.organization)
            namespace = task.namespace or _agent_namespace(task.organization.slug)
            spawner = get_spawner("k8s_job", cluster=cluster, namespace=namespace)
        cleanup = getattr(spawner, "cleanup_task_secret", None)
        if callable(cleanup):
            cleanup(task.external_id)
    except Exception:  # noqa: BLE001 — terminal truth must survive cleanup trouble
        log.warning("agent terminal secret cleanup failed for task %s", task.guid, exc_info=True)


# Pod container-waiting reasons that never self-heal: the container will not
# start no matter how long we wait, so the task should fail rather than hang.
# Deliberately excludes ErrImagePull (a first-attempt pull that k8s retries —
# it becomes ImagePullBackOff once genuinely stuck) and CrashLoopBackOff (the
# container DID start and its exit is captured through the Job's Failed
# condition / exit code path).
_FATAL_POD_WAIT_REASONS = frozenset({"ImagePullBackOff", "InvalidImageName", "CreateContainerConfigError"})


def _fatal_pod_wait_reason(cluster: Any, namespace: str, task_guid: str) -> str:
    """Return the task pod's fatal container-waiting reason, or ``""``.

    Discovers the pod by its ``astrolift.dev/task-id`` label (the same lookup
    the log + VNC paths use) and returns its rolled-up status when that status
    is an unrecoverable image/config-pull state (see
    :data:`_FATAL_POD_WAIT_REASONS`). Best-effort: any lookup failure returns
    ``""`` so a transient probe error never fails a healthy task.
    """
    from core.cluster_observability import ClusterObservabilityError, list_app_pods

    try:
        pods = list_app_pods(
            cluster=cluster,
            namespace=namespace,
            app_slug=task_guid,
            task_id=task_guid,
        )
    except ClusterObservabilityError:
        return ""
    except Exception:  # noqa: BLE001 — a probe hiccup must not fail the task
        log.exception("agent poll: pod health probe failed for %s", task_guid)
        return ""
    for pod in pods:
        status = (getattr(pod, "status", "") or "").strip()
        if status in _FATAL_POD_WAIT_REASONS:
            return status
    return ""


def _advance_to_terminal(task: Any, terminal_status: str) -> None:
    """Walk a task to a terminal status, stepping through RUNNING if needed.

    The state graph only allows COMPLETED/FAILED from RUNNING (and FAILED also
    from PROVISIONING). A container can finish so fast we observe success while
    the task is still PROVISIONING, so step through RUNNING first. Every hop is
    guarded against a concurrent advance by the dispatch callback API.
    """
    from astrolift_agents.models import AgentTask

    if task.status in _TERMINAL_STATUSES:
        return
    if task.status == AgentTask.Status.PROVISIONING:
        # COMPLETED is the only terminal state that requires RUNNING first.
        if terminal_status == AgentTask.Status.COMPLETED:
            try:
                task.transition_to(AgentTask.Status.RUNNING)
            except ValueError:
                pass
    try:
        task.transition_to(terminal_status)
    except ValueError:
        # Already terminal via a race — the outputs were saved above.
        log.warning(
            "execute_agent_stage: task %s could not transition %s -> %s",
            task.guid,
            task.status,
            terminal_status,
        )


def _load_task_outcome_sync(task_pk: int) -> dict[str, Any]:
    """Return the terminal outcome payload for the activity's return value."""
    from astrolift_agents.models import AgentTask

    task = AgentTask.objects.get(pk=task_pk)
    output_key = ""
    if task.brief_id is not None:
        output_key = (task.brief.context or {}).get("output_key", "")
    return {
        "task_guid": str(task.guid),
        "status": task.status,
        "result": task.result,
        "failure": task.failure,
        "output_key": output_key,
    }


def _capture_cancel_signal(task, *, ok: bool, from_status: str) -> None:
    """Record the stop/cancel signal delivered to a running agent task (#1217).

    The control plane telling an agent's container to stop is the one genuine
    control-plane-visible *signal* to an AgentTask, so it feeds the P3
    interaction map's Signals hub. Fully defensive: capture is additive /
    side-effect-only and must never break cancellation.
    """
    try:
        from astrolift_agents.models import AgentInteraction, record_interaction

        record_interaction(
            task,
            kind=AgentInteraction.Kind.SIGNAL,
            name="cancel",
            status="ok" if ok else "error",
            detail={"from_status": from_status, "external_id": task.external_id or ""},
        )
    except Exception:  # noqa: BLE001 — capture must never break cancellation
        log.exception(
            "cancel_agent_stage: failed to capture cancel signal for task %s",
            getattr(task, "guid", None),
        )


def _cancel_agent_task_sync(task_guid: str) -> dict[str, Any]:
    """Stop the container and move the task to CANCELLED only on success.

    DRAFT / QUEUED / PROVISIONING / RUNNING tasks are cancellable; anything else
    is already terminal and left untouched.
    """
    from django.db import transaction

    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner

    # Hold the row lock through the external delete. This deliberately makes a
    # terminal callback wait until the kill decision settles, preventing a
    # stale RUNNING instance from overwriting CANCELLED after the Job is gone.
    with transaction.atomic():
        task = (
            AgentTask.objects.select_for_update()
            .select_related("organization")
            .filter(guid=task_guid, deleted_at__isnull=True)
            .first()
        )
        if task is None:
            log.warning("cancel_agent_stage: task %s not found", task_guid)
            return {"ok": False, "status": "not_found", "error": "task not found"}

        cancellable = {
            AgentTask.Status.DRAFT,
            AgentTask.Status.QUEUED,
            AgentTask.Status.PROVISIONING,
            AgentTask.Status.RUNNING,
        }
        if task.status not in cancellable:
            return {"ok": False, "status": task.status, "error": "task is already terminal"}

        from_status = task.status
        if task.external_id:
            try:
                cluster = _resolve_managed_cluster(task.organization)
                namespace = task.namespace or _agent_namespace(task.organization.slug)
                get_spawner("k8s_job", cluster=cluster, namespace=namespace).stop(task.external_id)
            except Exception as exc:  # noqa: BLE001 — report a failed hard stop truthfully
                _capture_cancel_signal(task, ok=False, from_status=from_status)
                log.warning(
                    "cancel_agent_stage: container stop failed for task %s",
                    task_guid,
                    exc_info=True,
                )
                return {"ok": False, "status": task.status, "error": str(exc) or "container stop failed"}

        _capture_cancel_signal(task, ok=True, from_status=from_status)
        task.transition_to(AgentTask.Status.CANCELLED)
        return {"ok": True, "status": task.status, "error": ""}


# ---------------------------------------------------------------------------
# Temporal activity definitions
# ---------------------------------------------------------------------------


@activity.defn(name="astrolift.agent.execute_stage")
async def execute_agent_stage(params: dict[str, Any]) -> dict[str, Any]:
    """Run one agent workflow stage end-to-end.

    ``params`` keys:
      org_slug              (str, required)
      skill_slug            (str, optional)
      environment_spec_slug (str, optional)
      prompt                (str, optional)
      context               (dict, optional)
      output_key            (str, required)
      timeout_seconds       (int, optional — default 3600)

    Returns ``{"output_key", "result", "status"}`` plus ``task_guid`` and
    ``failure`` for downstream chaining and debugging.

    The activity creates + queues an AgentTask, spawns the container, then
    polls until the task is terminal, heartbeating each cycle so Temporal can
    detect a stuck worker. On Temporal cancellation the in-flight task is
    cancelled best-effort before the CancelledError propagates.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    task_pk = await sync_to_async(_create_agent_task_sync)(params)

    try:
        spawn = await sync_to_async(_spawn_agent_task_sync)(task_pk)

        if spawn["ok"]:
            # Poll until terminal, heartbeating each cycle.
            while True:
                activity.heartbeat()
                poll = await sync_to_async(_poll_agent_task_sync)(task_pk)
                if poll["terminal"]:
                    break
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # Temporal asked the activity to stop — tear the task down before
        # re-raising so the workflow records a clean cancellation.
        task = await sync_to_async(_load_task_outcome_sync)(task_pk)
        await sync_to_async(_cancel_agent_task_sync)(task["task_guid"])
        raise

    outcome = await sync_to_async(_load_task_outcome_sync)(task_pk)
    return {
        "output_key": outcome["output_key"] or params["output_key"],
        "result": outcome["result"],
        "status": outcome["status"],
        "task_guid": outcome["task_guid"],
        "failure": outcome["failure"],
    }


@activity.defn(name="astrolift.agent.cancel_stage")
async def cancel_agent_stage(task_guid: str) -> dict[str, Any]:
    """Cancel an in-flight agent task and return the hard-stop outcome.

    Stops the spawned container and moves the task to a terminal state. Safe to
    call on an already-terminal task; the result identifies that precondition.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_cancel_agent_task_sync)(task_guid)


@activity.defn(name="astrolift.agent.dispatch_task")
async def dispatch_agent_task(task_pk: int) -> dict[str, Any]:
    """Run one *already-created* AgentTask end-to-end (spec 33, PR-1).

    This is the Once-dispatch counterpart to :func:`execute_agent_stage`. The
    ``runAstroliftAgent`` mutation has already created the AgentTask in QUEUED
    with its ``agent_definition`` Workload set (the K8s Job spawner requires
    ``agent_definition`` to render the pod image — see
    ``astrolift_dispatch.spawners.k8s_job``), so this activity does NOT create
    a task; it reuses the same spawn -> poll -> terminal pipeline on the
    existing pk:

      ``_spawn_agent_task_sync`` (QUEUED -> PROVISIONING + ``get_spawner``)
      then ``_poll_agent_task_sync`` until terminal, heartbeating each cycle.

    Returns the task's terminal outcome payload (same shape as
    :func:`execute_agent_stage`). On Temporal cancellation the in-flight task
    is torn down best-effort before the CancelledError propagates.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    try:
        spawn = await sync_to_async(_spawn_agent_task_sync)(task_pk)

        if spawn["ok"]:
            while True:
                activity.heartbeat()
                poll = await sync_to_async(_poll_agent_task_sync)(task_pk)
                if poll["terminal"]:
                    break
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        outcome = await sync_to_async(_load_task_outcome_sync)(task_pk)
        await sync_to_async(_cancel_agent_task_sync)(outcome["task_guid"])
        raise

    return await sync_to_async(_load_task_outcome_sync)(task_pk)
