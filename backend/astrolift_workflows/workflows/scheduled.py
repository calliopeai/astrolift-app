"""Scheduled workflows wired by ``astrolift_workflows.schedule_registry``.

Each class below corresponds to one ``ScheduleKind`` enum value in the
registry. Bodies are deliberately compact: most scheduled jobs are
"sweep some table → fire follow-up workflows", and that pattern is the
same shape ``CronDeployTickWorkflow`` already follows. The actual work
lives in single-purpose activities so each workflow stays small and
testable.

These were previously REGISTERED in the schedule catalog but the
classes didn't exist — registering schedules at boot would have crashed
the worker. The implementations below are real but lean: they execute
their canonical activity and return a structured result. As the
platform matures, activities are extended without touching the
workflow scaffolding.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.scheduled import (
        capture_platform_cost_snapshot,
        capture_quota_usage_snapshot,
        detect_drift,
        expire_pending_approval_deployments,
        gc_stale_previews,
        poll_scheduled_job_runs,
        apply_observability_retention,
    probe_app_cronjob_runs_activity,
    probe_app_dns_activity,
    prune_audit_log,
        prune_stale_sessions,
        reconcile_cluster_capabilities,
        reheal_webhook_subscriptions,
    )


# Shared activity timeout — most scheduled work is a single sweep that
# completes in seconds; the 10-minute ceiling exists so a runaway sweep
# that misses cancellation cleanly times out rather than blocking the
# next tick.
_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="PreviewGarbageCollectWorkflow")
class PreviewGarbageCollectWorkflow:
    """Tear down preview environments whose PR closed > grace-period ago."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        torn = await workflow.execute_activity(
            gc_stale_previews,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"gc tore down {torn} stale preview(s)")


@workflow.defn(name="PollScheduledJobRunsWorkflow")
class PollScheduledJobRunsWorkflow:
    """Refresh the status of cluster-side Jobs for app-triggered tasks."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        updated = await workflow.execute_activity(
            poll_scheduled_job_runs,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"refreshed {updated} job run(s)")


@workflow.defn(name="ReconcileClusterCapabilitiesWorkflow")
class ReconcileClusterCapabilitiesWorkflow:
    """Re-probe every managed cluster's capability surface."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            reconcile_cluster_capabilities,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"reconciled {n} cluster(s)")


@workflow.defn(name="DriftDetectionWorkflow")
class DriftDetectionWorkflow:
    """Compare in-cluster Deployments vs. last-applied manifest set."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        drifted = await workflow.execute_activity(
            detect_drift,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"drift detected on {drifted} app(s)")


@workflow.defn(name="RehealWebhookSubscriptionsWorkflow")
class RehealWebhookSubscriptionsWorkflow:
    """Re-create webhook subscriptions that have been pinged-but-not-acked."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            reheal_webhook_subscriptions,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"rehealed {n} webhook subscription(s)")


@workflow.defn(name="PruneAuditLogWorkflow")
class PruneAuditLogWorkflow:
    """Soft-delete audit log entries past the retention window."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            prune_audit_log,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"pruned {n} audit log entr{'y' if n == 1 else 'ies'}")


@workflow.defn(name="ProbeAppDnsWorkflow")
class ProbeAppDnsWorkflow:
    """Refresh the app doctor's cached DNS answers.

    Read-only against the network and idempotent -- it resolves names and
    writes what it found -- so it ships active rather than held.
    """

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            probe_app_dns_activity,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"dns probe changed for {n} app(s)")


@workflow.defn(name="ProbeAppCronjobRunsWorkflow")
class ProbeAppCronjobRunsWorkflow:
    """Refresh the app doctor's cached cronjob-run answers (#1710).

    Read-only against the cluster and idempotent -- it lists Jobs and
    writes what it found -- so it ships active rather than held.
    """

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            probe_app_cronjob_runs_activity,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"cronjob probe changed for {n} app(s)")


@workflow.defn(name="ApplyObservabilityRetentionWorkflow")
class ApplyObservabilityRetentionWorkflow:
    """Apply each org's configured log retention to its CloudWatch groups.

    Sets a retention *policy* rather than deleting anything (#1602): AWS ages
    the data out itself, so there is nothing for a schedule to delete and a
    platform-side delete loop would be strictly worse than the one AWS
    already runs.
    """

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            apply_observability_retention,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"retention applied to {n} log group(s)")


@workflow.defn(name="CapturePlatformCostSnapshotWorkflow")
class CapturePlatformCostSnapshotWorkflow:
    """Capture a daily platform cost snapshot row per organization."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            capture_platform_cost_snapshot,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"snapshot captured for {n} org(s)")


@workflow.defn(name="CaptureQuotaUsageSnapshotWorkflow")
class CaptureQuotaUsageSnapshotWorkflow:
    """Append a usage snapshot row per active quota, on a daily cadence (#1182)."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            capture_quota_usage_snapshot,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"quota usage snapshot captured for {n} quota(s)")


@workflow.defn(name="PruneStaleSessionsWorkflow")
class PruneStaleSessionsWorkflow:
    """Soft-revoke ``AstroliftSession`` rows past the per-kind stale TTL (#498)."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            prune_stale_sessions,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(
            ok=True,
            message=f"pruned {n} stale session{'' if n == 1 else 's'}",
        )


@workflow.defn(name="ExpirePendingApprovalDeploymentsWorkflow")
class ExpirePendingApprovalDeploymentsWorkflow:
    """Auto-fail pending_approval deployments past their magic-link expiry."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        n = await workflow.execute_activity(
            expire_pending_approval_deployments,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"expired {n} pending approval deployment(s)")
