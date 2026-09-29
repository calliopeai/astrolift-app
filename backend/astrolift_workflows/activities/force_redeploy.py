"""Force-redeploy recovery activity (#389).

When an app gets stuck — a Deployment row that never reconciles, a
CrashLoopBackOff that survives a normal restart, leftover orphaned K8s
objects from a botched rename, a mid-deploy webhook delivery storm that
got cancelled — the operator's manual recovery is:

  1. Mark in-flight Deployment rows as terminal (no CANCELLED state on
     ``Deployment``; FAILED is the documented terminal-via-abort path,
     mirroring ``abort_deployment``).
  2. ``kubectl delete`` the app's per-workload k8s objects so the next
     reconcile re-creates them clean — plus any legacy bare-slug
     fallbacks left over from rename / migration history.
  3. Re-dispatch the deploy CI workflow so the rebuild + redeploy
     flows through the normal pipeline (approver flow still applies
     when the env requires it).

This module consolidates that recovery path so SRE doesn't have to
drop to a shell. The sync helpers are the public surface — the
mutation resolver (``forceAstroliftRedeploy``) calls them directly in
Django request context; the activity is also exposed for workflows
that want the same recovery from a worker.

Per the policy module: namespaces are NOT deleted (cross-app shared
namespaces are possible when ``k8s_namespace`` is operator-pinned).
We delete per-workload objects only.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

# Module-level re-export of the dispatch service so the same
# monkeypatch surface covers the CI-redispatch step. The redirection
# costs nothing at runtime and keeps the test fixtures aligned with
# the actual import surface of this module.
from astrolift_scm.services.workflows import (
    WorkflowDispatchError,
    dispatch_astrolift_ci_workflow,
)

# Module-level re-exports so test fixtures can monkeypatch the driver
# resolvers cleanly. The underlying functions are private to
# ``core.cluster_management``; re-binding here means a test can swap
# them out without reaching into another package's internals.
from core.cluster_management import _context_for_cluster, _driver_for_cluster

log = logging.getLogger("astrolift_workflows.activities.force_redeploy")


# ---------------------------------------------------------------------------
# Status sets
# ---------------------------------------------------------------------------


# Rows we'll mark FAILED on a force-redeploy. These are the states
# where a Deployment row can be stuck (approval queue, scheduled,
# rolling, or mid-redeploy). Terminal rows (RUNNING / FAILED /
# SUPERSEDED / ROLLED_BACK) are left alone — a force-redeploy is not
# a history rewrite.
_IN_FLIGHT_STATUSES: tuple[str, ...] = (
    "pending_approval",
    "pending",
    "deploying",
    "redeploying",
)


# ---------------------------------------------------------------------------
# Step 1 — cancel in-flight Deployment rows
# ---------------------------------------------------------------------------


def _cancel_in_flight_deploys_sync(
    app_id: int,
    environment_id: int | None,
) -> int:
    """Mark in-flight ``Deployment`` rows on the (app, env) as FAILED.

    Uses ``transition_to(FAILED)`` rather than a direct status write so
    the audit / event hooks fire and ``ended_at`` / ``failed_at`` get
    populated consistently with the rest of the lifecycle. Per-row
    transition errors are logged + skipped so a single stuck row in a
    state that can't reach FAILED (shouldn't happen given the
    _TRANSITIONS table) doesn't block recovery.

    Returns the number of rows that actually transitioned. If
    ``environment_id`` is None, every active env on the app is in
    scope — useful when the operator says "blow the whole app away".
    """
    from astrolift_lifecycle.models import Deployment

    qs = Deployment.objects.filter(
        registered_app_id=app_id,
        status__in=_IN_FLIGHT_STATUSES,
        deleted_at__isnull=True,
    )
    if environment_id is not None:
        qs = qs.filter(app_environment_id=environment_id)

    n = 0
    for deploy in qs.select_related("workflow_run"):
        # Try a graceful signal on the bound workflow first so the
        # worker stops cleanly; abort the running workflow if signal
        # isn't accepted. Best-effort — recovery proceeds regardless.
        if deploy.workflow_run_id:
            try:
                from astrolift_workflows.client import (
                    signal_workflow,
                    terminate_workflow,
                )

                wf_id = deploy.workflow_run.workflow_id
                if not signal_workflow(wf_id, "abort"):
                    terminate_workflow(wf_id, reason="force_redeploy mutation")
            except Exception:  # noqa: BLE001 — best-effort
                log.warning(
                    "force_redeploy: workflow abort failed for deploy %s",
                    deploy.pk,
                    exc_info=True,
                )
        try:
            deploy.transition_to(Deployment.Status.FAILED)
            n += 1
        except ValueError:
            # Row was in a state that can't reach FAILED by the time
            # we got here (rare race with a worker). Log + skip.
            log.warning(
                "force_redeploy: could not transition deploy %s from %s to FAILED",
                deploy.pk,
                deploy.status,
            )
    return n


# ---------------------------------------------------------------------------
# Step 2 — delete the app's per-workload K8s objects
# ---------------------------------------------------------------------------


# Stubbing strategy: the cluster driver's ``delete_manifests`` only
# reads ``apiVersion`` / ``kind`` / ``metadata.{name,namespace}`` to
# issue the delete RPC. Building stubs by hand (rather than re-
# rendering the manifest) keeps the recovery path useful when the
# manifest itself is the thing that's busted.
_STUB_KINDS: tuple[tuple[str, str], ...] = (
    ("apps/v1", "Deployment"),
    ("v1", "Service"),
    ("networking.k8s.io/v1", "Ingress"),
    ("batch/v1", "CronJob"),
)


def _stub_for(api_version: str, kind: str, namespace: str, name: str) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
    }


def _candidate_names_for_app(app, workloads) -> list[str]:
    """Object names to try deleting.

    Two flavors:
      * Per-workload names: ``<app-slug>-<workload-slug>`` for each
        active workload row. This is the canonical name the renderer
        emits.
      * Bare-slug fallbacks: ``<app-slug>`` alone. Covers legacy
        single-workload apps and orphaned objects left over from
        rename / migration history.
    """
    names: list[str] = []
    seen: set[str] = set()
    for w in workloads:
        n = f"{app.slug}-{w.slug}" if w.slug else app.slug
        if n and n not in seen:
            names.append(n)
            seen.add(n)
    # Bare-slug fallback. Always tried, even when the workload list is
    # populated — that's the whole point of "force redeploy after a
    # botched rename".
    if app.slug and app.slug not in seen:
        names.append(app.slug)
        seen.add(app.slug)
    return names


def _delete_app_k8s_objects_sync(
    app_id: int,
    environment_id: int | None,
) -> dict[str, Any]:
    """Delete the app's per-workload k8s objects on each environment.

    Iterates the (app, env) pairs in scope, resolves each env's
    cluster + driver, builds stub manifests for every workload across
    the four object kinds (Deployment / Service / Ingress / CronJob)
    plus bare-slug fallbacks, and asks the driver to delete them.

    Per the SDK contract, ``delete_manifests`` treats not-found as a
    success (no row in ``errors``). That means the stub list can be
    aggressive — we don't need to know which objects actually exist.

    Returns ``{deleted: int, errors: list[str]}`` where ``deleted`` is
    the count of stubs the driver accepted without error.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp, Workload
    from core.app_deploy import namespace_for_environment

    app = RegisteredApp.all_objects.select_related("organization").get(pk=app_id)
    envs_qs = AppEnvironment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    ).select_related("tenant_cluster__provider_plugin")
    if environment_id is not None:
        envs_qs = envs_qs.filter(pk=environment_id)
    envs = list(envs_qs)

    workloads = list(
        Workload.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ),
    )
    names = _candidate_names_for_app(app, workloads)

    deleted = 0
    errors: list[str] = []
    namespaces: list[str] = []
    for env in envs:
        cluster = env.tenant_cluster
        if cluster is None:
            continue
        # Each environment's objects live in its own namespace when it has
        # one (#1922), else in the app namespace.
        namespace = namespace_for_environment(env)
        namespaces.append(namespace)
        try:
            driver = _driver_for_cluster(cluster)
        except Exception as exc:  # noqa: BLE001 — log + continue
            errors.append(
                f"{cluster.slug}: could not build driver: {exc}",
            )
            log.warning(
                "force_redeploy: driver build failed for cluster=%s app=%s: %s",
                cluster.slug,
                app.slug,
                exc,
            )
            continue
        ctx = _context_for_cluster(cluster)
        stubs: list[dict[str, Any]] = []
        for api_version, kind in _STUB_KINDS:
            for name in names:
                stubs.append(_stub_for(api_version, kind, namespace, name))
        if not stubs:
            continue
        try:
            result = driver.delete_manifests(ctx.slug, namespace, stubs)
        except Exception as exc:  # noqa: BLE001 — log + continue
            errors.append(f"{cluster.slug}: delete_manifests raised: {exc}")
            log.warning(
                "force_redeploy: delete_manifests failed cluster=%s app=%s ns=%s: %s",
                cluster.slug,
                app.slug,
                namespace,
                exc,
            )
            continue
        # Each stub counts as a successful delete unless the driver
        # surfaces it in ``errors``. The driver's not-found-is-ok
        # contract is what makes the "deleted" number a useful audit
        # signal even when most stubs target absent objects.
        per_cluster_errors = list(getattr(result, "errors", []) or [])
        deleted += max(0, len(stubs) - len(per_cluster_errors))
        for e in per_cluster_errors:
            errors.append(f"{cluster.slug}: {e}")
    return {"deleted": deleted, "errors": errors, "namespaces": sorted(set(namespaces))}


# ---------------------------------------------------------------------------
# Step 3 — re-dispatch the deploy CI workflow
# ---------------------------------------------------------------------------


def _redispatch_ci_workflow_sync(app_id: int) -> dict[str, Any]:
    """Trigger the app's CI workflow so a fresh build + deploy lands.

    Mirrors ``triggerAstroliftDeployWorkflow`` (#387). The dispatched
    workflow flows through the env's approval gate when one applies,
    matching the issue's acceptance criterion: "trying to force-
    redeploy an env in a ``requiresApproval`` state still goes through
    the approval gate" — we don't bypass it, we re-fire the path that
    respects it.

    Returns ``{dispatched: bool, run_url: str, message: str}``. On
    dispatch failure the mutation surfaces a partial-success envelope
    (cancellation + delete counts are still reported).
    """
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related("organization").get(pk=app_id)
    try:
        result = dispatch_astrolift_ci_workflow(app)
    except NotImplementedError as exc:
        return {"dispatched": False, "run_url": "", "message": str(exc)}
    except WorkflowDispatchError as exc:
        return {"dispatched": False, "run_url": "", "message": exc.message}
    if not result.ok:
        return {
            "dispatched": False,
            "run_url": "",
            "message": result.error_message or "workflow dispatch failed",
        }
    return {
        "dispatched": True,
        "run_url": result.run_url,
        "message": "",
    }


# ---------------------------------------------------------------------------
# Activity entry — composes the three steps
# ---------------------------------------------------------------------------


@activity.defn(name="astrolift.app.force_redeploy")
async def force_redeploy_app(
    registered_app_id: int,
    app_environment_id: int | None = None,
) -> dict[str, Any]:
    """Activity wrapping cancel → delete → dispatch.

    Returns the same envelope the mutation surfaces:
    ``{deployments_cancelled, k8s_objects_deleted, workflow_dispatched,
       run_url, dispatch_message, errors}``.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    cancelled = await sync_to_async(_cancel_in_flight_deploys_sync)(
        registered_app_id,
        app_environment_id,
    )
    activity.heartbeat()
    delete_summary = await sync_to_async(_delete_app_k8s_objects_sync)(
        registered_app_id,
        app_environment_id,
    )
    activity.heartbeat()
    dispatch = await sync_to_async(_redispatch_ci_workflow_sync)(registered_app_id)

    summary = {
        "deployments_cancelled": cancelled,
        "k8s_objects_deleted": int(delete_summary["deleted"]),
        "workflow_dispatched": bool(dispatch["dispatched"]),
        "run_url": dispatch["run_url"],
        "dispatch_message": dispatch["message"],
        "errors": list(delete_summary["errors"]),
    }
    log.info(
        "force_redeploy_app summary=%s",
        summary,
        extra={"registered_app_id": registered_app_id},
    )
    return summary


__all__ = [
    "_cancel_in_flight_deploys_sync",
    "_delete_app_k8s_objects_sync",
    "_redispatch_ci_workflow_sync",
    "force_redeploy_app",
]
