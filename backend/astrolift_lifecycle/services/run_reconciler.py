"""Run-status reconciler — the missing runtime for ScheduledJobRun / TaskRun.

Both ``ScheduledJobRun`` (created RUNNING by ``job_runner.run_job_once``)
and ``TaskRun`` (created PENDING by the ``runTask`` mutation) record a
``k8s_job_name`` but nothing ever transitions them afterwards: a Job that
succeeded or failed in the cluster leaves its platform row stuck
non-terminal forever, with no ``started_at`` / ``ended_at`` /
``duration_seconds`` / ``exit_code``. This sweep closes that gap.

Run once per tick by the platform schedule registry (mirrors
``alert_engine.run_alert_sweep`` / ``uptime_probe.probe_all_deployed_apps``):

1. Select non-terminal rows that carry a ``k8s_job_name``.
2. Resolve each run's app -> environment -> tenant cluster, and read the
   Job's status through that cluster's driver (per-cloud auth handled by
   the driver, exactly like the deploy path in ``job_runner``).
3. Advance the row: ``status`` (succeeded/failed/running), ``started_at``
   / ``ended_at`` from the Job, computed ``duration_seconds``, a coarse
   status-derived ``exit_code`` (0 ok / 1 fail), and — for ScheduledJobRun,
   which has the field — a ``log_excerpt`` summarising the Job's failure
   conditions.

Safe-by-default (this deploys to prod):

* Per-run ``try/except`` — one unreachable cluster or torn-down Job can't
  abort the sweep.
* A missing Job (404, TTL-GC'd), an unreachable cluster, a gone
  app/cluster, or a driver that doesn't implement job-status reads all
  resolve to "can't determine -> leave the row exactly as it is", never a
  crash and never a mis-mark.
* Idempotent: terminal rows are never selected again, and a still-running
  row whose fields are already up to date is not re-written (no version
  churn each tick).

The real container exit code lives on the Job's *pod*, not the Job status;
reading it would need a second, pod-enumerating call. This read-only sweep
issues a single ``read_namespaced_job_status`` per run and derives a coarse
0/1 exit code from the terminal state instead — the failure detail goes
into ``log_excerpt`` (ScheduledJobRun) where the field exists.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from astrolift_lifecycle.models.jobs import ScheduledJobRun
from astrolift_lifecycle.models.task_run import TaskRun
from core.app_deploy import namespace_for_app

log = logging.getLogger(__name__)


# Rows whose Job could still be running — the only rows worth polling.
# Everything else (SUCCEEDED / FAILED / SUPERSEDED / CANCELLED) is terminal
# and never touched again.
_SCHEDULED_NON_TERMINAL: tuple[str, ...] = (ScheduledJobRun.Status.RUNNING.value,)
_TASK_NON_TERMINAL: tuple[str, ...] = (
    TaskRun.Status.PENDING.value,
    TaskRun.Status.RUNNING.value,
)

_LOG_EXCERPT_MAX = 4000
"""Cap the persisted condition summary well under the TextField limit —
matches the defensive truncation the cluster activities use."""

# Summary buckets. Kept flat so the Temporal activity can mirror it 1:1.
_OUTCOMES = ("succeeded", "failed", "running", "unchanged", "skipped", "errors")


# ---- cluster / job-status resolution -------------------------------


def _read_job_status(cluster: Any, *, namespace: str, job_name: str) -> Any | None:
    """Read one Job's status through the cluster's driver.

    Returns a ``JobStatus``-shaped object (duck-typed: ``active`` /
    ``succeeded`` / ``failed`` / ``start_time`` / ``completion_time`` /
    ``conditions``), or ``None`` when the resolved driver doesn't implement
    ``read_job_status`` (e.g. a third-party plugin) — the caller treats
    that as "can't determine".

    Cluster-auth failures, an unreachable apiserver, or a 404 (missing /
    GC'd Job) propagate as exceptions; the per-run wrapper catches them.
    Honors the test-injected management backend because it routes through
    ``core.cluster_management._driver_for_cluster``.
    """
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    driver = _driver_for_cluster(cluster)
    reader = getattr(driver, "read_job_status", None)
    if reader is None:
        log.info(
            "run reconciler: driver for cluster %s has no read_job_status; leaving run as-is",
            getattr(cluster, "slug", cluster),
        )
        return None
    ctx = _context_for_cluster(cluster)
    return reader(ctx, namespace=namespace, job_name=job_name)


def _classify(job_status: Any) -> str | None:
    """Map a Job status to a verdict: ``failed`` / ``succeeded`` /
    ``running``, or ``None`` when nothing is determinable yet (pod not
    scheduled). Failure wins over success wins over active so a Job that
    both retried-and-failed and had an earlier success reads as failed.
    """
    if int(getattr(job_status, "failed", 0) or 0) >= 1:
        return "failed"
    if int(getattr(job_status, "succeeded", 0) or 0) >= 1:
        return "succeeded"
    if int(getattr(job_status, "active", 0) or 0) >= 1:
        return "running"
    # A Job with a start_time but no active/succeeded/failed count is in a
    # brief between-pods gap — treat as running so we at least stamp
    # started_at rather than reporting "nothing yet".
    if getattr(job_status, "start_time", None) is not None:
        return "running"
    return None


def _excerpt_from(job_status: Any) -> str:
    """Join the Job's ``status.conditions`` messages into a short excerpt.

    This is the best diagnostic a pure status read offers (e.g.
    ``"Job has reached the specified backoff limit"``) — not the pod's
    stdout, but enough for an operator to see *why* a job failed without
    opening the cluster."""
    conds = getattr(job_status, "conditions", ()) or ()
    text = "; ".join(c for c in conds if c)
    return text[:_LOG_EXCERPT_MAX]


def _apply_verdict(run: Any, job_status: Any, verdict: str, *, has_log_field: bool) -> str:
    """Write the verdict + Job-derived fields onto ``run``, touching only
    changed columns. Returns the outcome bucket (``succeeded`` / ``failed``
    / ``running`` / ``unchanged``).

    ``verdict`` values equal the shared ``Status`` enum values
    (``"running"`` / ``"succeeded"`` / ``"failed"``) on both models, so it
    is assigned straight onto ``run.status``.
    """
    changed: list[str] = []
    terminal = verdict in ("succeeded", "failed")

    # started_at: stamp from the Job once; never overwrite an existing value.
    start_time = getattr(job_status, "start_time", None)
    if run.started_at is None and start_time is not None:
        run.started_at = start_time
        changed.append("started_at")

    if terminal:
        # k8s only sets completionTime on success; on failure fall back to
        # now() (within one tick, ~60s, of the real end) so duration is
        # still computable.
        ended = getattr(job_status, "completion_time", None) or timezone.now()
        if run.ended_at != ended:
            run.ended_at = ended
            changed.append("ended_at")
        if run.started_at is not None:
            duration = max(int((run.ended_at - run.started_at).total_seconds()), 0)
            if run.duration_seconds != duration:
                run.duration_seconds = duration
                changed.append("duration_seconds")
        # Coarse status-derived exit code — the true container exit code
        # lives on the pod, not the Job status (see module docstring).
        exit_code = 0 if verdict == "succeeded" else 1
        if run.exit_code != exit_code:
            run.exit_code = exit_code
            changed.append("exit_code")
        if has_log_field:
            excerpt = _excerpt_from(job_status)
            if excerpt and run.log_excerpt != excerpt:
                run.log_excerpt = excerpt
                changed.append("log_excerpt")

    if run.status != verdict:
        run.status = verdict
        changed.append("status")

    if not changed:
        return "unchanged"

    # BaseCoreModel.save() bumps ``version`` and TrackingMixin stamps
    # ``updated_at`` — both must be in update_fields to persist.
    run.save(update_fields=[*changed, "updated_at", "version"])
    return verdict


# ---- per-run reconcilers -------------------------------------------


def _reconcile_scheduled_run(run: ScheduledJobRun) -> str:
    """Advance one non-terminal ScheduledJobRun from its Job status."""
    env = run.app_environment
    cluster = getattr(env, "tenant_cluster", None) if env is not None else None
    if cluster is None:
        log.info("run reconciler: ScheduledJobRun %s has no cluster; skipping", run.pk)
        return "skipped"
    # The row stores the exact namespace the CronJob was rendered into;
    # fall back to the app's canonical namespace if it was never recorded.
    namespace = run.namespace or namespace_for_app(env.registered_app)
    if not namespace:
        return "skipped"

    job_status = _read_job_status(cluster, namespace=namespace, job_name=run.k8s_job_name)
    if job_status is None:
        return "errors"
    verdict = _classify(job_status)
    if verdict is None:
        return "unchanged"
    return _apply_verdict(run, job_status, verdict, has_log_field=True)


def _reconcile_task_run(run: TaskRun) -> str:
    """Advance one non-terminal TaskRun from its Job status.

    TaskRun has no ``namespace`` column and its ``app_environment`` is
    nullable — with no environment there's no cluster to resolve, so we
    skip. Namespace is the app's canonical deploy namespace (the same one
    the runTask Job targets)."""
    env = run.app_environment
    cluster = getattr(env, "tenant_cluster", None) if env is not None else None
    if cluster is None:
        log.info("run reconciler: TaskRun %s has no environment/cluster; skipping", run.pk)
        return "skipped"
    namespace = namespace_for_app(env.registered_app)
    if not namespace:
        return "skipped"

    job_status = _read_job_status(cluster, namespace=namespace, job_name=run.k8s_job_name)
    if job_status is None:
        return "errors"
    verdict = _classify(job_status)
    if verdict is None:
        return "unchanged"
    # TaskRun has no log field — skip the excerpt.
    return _apply_verdict(run, job_status, verdict, has_log_field=False)


# ---- selection -----------------------------------------------------


def _nonterminal_scheduled_runs():
    """Non-terminal ScheduledJobRun rows with a Job to poll.

    ``.objects`` is the soft-delete manager, so deleted rows are excluded
    automatically."""
    return (
        ScheduledJobRun.objects.filter(status__in=_SCHEDULED_NON_TERMINAL)
        .exclude(k8s_job_name="")
        .select_related(
            "app_environment__registered_app__organization",
            "app_environment__tenant_cluster__provider_plugin",
        )
    )


def _nonterminal_task_runs():
    """Non-terminal TaskRun rows with a Job to poll."""
    return (
        TaskRun.objects.filter(status__in=_TASK_NON_TERMINAL)
        .exclude(k8s_job_name="")
        .select_related(
            "app_environment__registered_app__organization",
            "app_environment__tenant_cluster__provider_plugin",
        )
    )


# ---- sweep ---------------------------------------------------------


def _safe_reconcile(fn, run: Any, kind: str) -> str:
    """Run one per-row reconciler under a guard so a single unreachable
    cluster / missing Job can't abort the sweep. Any failure is "can't
    determine" -> the row is left untouched and counted as an error."""
    try:
        return fn(run)
    except Exception:  # noqa: BLE001 — one bad run must not stop the sweep
        log.exception("run reconciler: %s run %s reconcile raised", kind, run.pk)
        return "errors"


def reconcile_runs() -> dict[str, int]:
    """One reconcile pass over every non-terminal ScheduledJobRun + TaskRun.

    Returns a summary: ``evaluated`` rows walked; ``succeeded`` / ``failed``
    / ``running`` transitions written; ``unchanged`` no-ops (steady state);
    ``skipped`` rows with no resolvable cluster; ``errors`` rows whose Job
    status couldn't be read (unreachable / missing / unsupported driver)."""
    summary: dict[str, int] = {"evaluated": 0, **{k: 0 for k in _OUTCOMES}}

    for run in _nonterminal_scheduled_runs():
        summary["evaluated"] += 1
        summary[_safe_reconcile(_reconcile_scheduled_run, run, "scheduled")] += 1

    for run in _nonterminal_task_runs():
        summary["evaluated"] += 1
        summary[_safe_reconcile(_reconcile_task_run, run, "task")] += 1

    return summary


__all__ = ["reconcile_runs"]
