"""BringClusterIntoManagementWorkflow — flip a TenantCluster's
lifecycle from ``registered`` (or ``managed`` on refresh) to
``managed`` after platform RBAC apply + capability probe + preflight
Job all succeed (#316).

Activity sequence:

  1. mark_managing            — flip lifecycle to ``managing`` so the
                                UI shows a spinner.
  2. verify_reachability      — read-only auth check; raises if the
                                row's credentials don't get us to the
                                apiserver.
  3. apply_platform_rbac      — server-side apply of the four-manifest
                                bundle (Namespace / SA / ClusterRole /
                                ClusterRoleBinding). Idempotent.
  4. probe_capabilities       — capability snapshot persisted onto
                                ``TenantCluster.capabilities``.
  5. run_preflight_job        — one-shot nginx-unprivileged Job in
                                ``astrolift-system``. Skipped on
                                refresh unless ``force_preflight=True``.
  6. mark_managed             — flip lifecycle to ``managed`` + stamp
                                ``managed_at``.

Any failure short-circuits to ``mark_error`` with the failure
message in ``last_management_error``.

Workflow id pattern: ``BringClusterIntoManagement-<cluster-guid>``,
so re-firing the same row joins the existing run rather than
spawning a parallel one.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import BringClusterIntoManagementInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_platform_rbac,
        mark_error,
        mark_managed,
        mark_managing,
        probe_capabilities,
        run_preflight_job,
        verify_reachability,
    )


# Each activity gets its own timeout. The preflight Job's 60s wait
# means run_preflight_job is the long pole; everything else should
# complete within seconds against a healthy apiserver.
_QUICK_TIMEOUT = timedelta(minutes=2)
# The driver's PREFLIGHT_TIMEOUT_SECONDS is 600s (Fargate cold-start
# headroom + image pull + first-pod admission). The activity wrapper
# polls on top of that and needs return slack — sit comfortably above
# the driver cap so Temporal doesn't cancel mid-Job.
_PREFLIGHT_TIMEOUT = timedelta(minutes=12)

# Don't retry the mark_error activity — it's the last-resort writer.
# Retrying it on its own failure (DB unreachable) can mask the original
# cluster error, and a single mark_error miss is recoverable on the
# next workflow run.
_MARK_ERROR_RETRY = RetryPolicy(maximum_attempts=1)

# Quick activities get one retry — the cluster-side flakiness window
# is short and the workflow's user is waiting in the UI.
_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)


def _truncate(message: str, *, limit: int = 4000) -> str:
    """Persisted message cap. ``last_management_error`` is a
    TextField but we keep the rendered surface compact."""
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="BringClusterIntoManagementWorkflow")
class BringClusterIntoManagementWorkflow:
    @workflow.run
    async def run(self, input: BringClusterIntoManagementInput) -> WorkflowResult:
        cluster_id = input.cluster_id
        force_preflight = input.force_preflight

        # Step 1: flip lifecycle to managing. The mutation already
        # did this synchronously, but re-asserting here gives a
        # resumable point for a worker that picks up after a crash.
        try:
            await workflow.execute_activity(
                mark_managing,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            # Failure to set the row to managing is a DB-layer issue
            # that won't get better by re-running — fall through to
            # mark_error anyway so the operator sees the message.
            return await self._fail(cluster_id, f"mark_managing: {exc}")

        try:
            await workflow.execute_activity(
                verify_reachability,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(cluster_id, f"verify_reachability: {exc}")

        try:
            await workflow.execute_activity(
                apply_platform_rbac,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(cluster_id, f"apply_platform_rbac: {exc}")

        try:
            await workflow.execute_activity(
                probe_capabilities,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(cluster_id, f"probe_capabilities: {exc}")

        if force_preflight:
            try:
                await workflow.execute_activity(
                    run_preflight_job,
                    cluster_id,
                    start_to_close_timeout=_PREFLIGHT_TIMEOUT,
                    # Preflight is a one-shot Job — retrying it on
                    # failure just runs the same Job over again. Cap at
                    # one attempt; operator inspects + retries via the
                    # mutation.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except Exception as exc:  # noqa: BLE001
                return await self._fail(cluster_id, f"run_preflight_job: {exc}")

        try:
            await workflow.execute_activity(
                mark_managed,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(cluster_id, f"mark_managed: {exc}")

        return WorkflowResult(
            ok=True,
            message="cluster brought into management",
            data={"force_preflight": force_preflight},
        )

    async def _fail(self, cluster_id: int, message: str) -> WorkflowResult:
        truncated = _truncate(message)
        try:
            await workflow.execute_activity(
                mark_error,
                args=[cluster_id, truncated],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_MARK_ERROR_RETRY,
            )
        except Exception:  # noqa: BLE001
            # ``mark_error`` itself failed — log via the workflow's
            # built-in logger (Temporal captures + ships to the UI)
            # and return the original message anyway.
            workflow.logger.exception("mark_error activity failed for cluster %s", cluster_id)
        return WorkflowResult(ok=False, message=truncated)
