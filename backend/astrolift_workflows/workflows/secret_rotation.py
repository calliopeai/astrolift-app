"""Secret-bundle rotation + cleanup workflows (#365).

Three workflows:

* :class:`RotateSecretBundleWorkflow` — operator-fired (or scheduled
  refresh-fired). Re-fetches the bundle's values from the
  SecretsBackend and re-applies the materialized k8s Secret on every
  cluster the bundle is referenced on, then optionally bounces the
  workloads consuming it.

* :class:`DeleteSecretBundleFromClustersWorkflow` — fires when a
  ``SecretBundle`` is soft-deleted. Cleans up the in-cluster Secret on
  every cluster the bundle was materialized to. Best-effort: failures
  on individual clusters are logged but don't fail the workflow —
  re-running deprovision is cheap and the platform DB is already the
  source of truth.

* :class:`SecretBundleScheduledRefreshWorkflow` — hourly sweep that
  fans out a no-bounce refresh for every actively-referenced bundle.
  Picks up external rotations (AWS Secrets Manager scheduled
  rotation, Vault TTL renewals) without operator action. Bouncing is
  off on the scheduled path so long-running pods only restart on
  operator-fired rotations or next deploy.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import (
    DeleteSecretBundleFromClustersInput,
    RotateSecretBundleInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.secret_rotation import (
        bounce_workloads_consuming_bundle,
        delete_secret_from_cluster,
        list_active_secret_bundle_targets,
        list_bundles_due_for_refresh,
        refresh_secret_bundle_in_cluster,
    )

# Each activity is a single backend / cluster apply call — generous
# enough to absorb a slow secrets-manager fetch but tight enough that
# a hung activity doesn't block the workflow indefinitely.
_ACTIVITY_TIMEOUT = timedelta(minutes=5)


@workflow.defn(name="RotateSecretBundleWorkflow")
class RotateSecretBundleWorkflow:
    """Re-fetch + re-apply a SecretBundle across every cluster it's
    referenced from, then optionally bounce consumers."""

    @workflow.run
    async def run(self, input: RotateSecretBundleInput) -> WorkflowResult:
        targets = await workflow.execute_activity(
            list_active_secret_bundle_targets,
            input.secret_bundle_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        if not targets:
            return WorkflowResult(
                ok=True,
                message="no active refs — nothing to rotate",
            )
        refreshed = 0
        bounced_total = 0
        errors: list[str] = []
        for t in targets:
            try:
                await workflow.execute_activity(
                    refresh_secret_bundle_in_cluster,
                    t,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                )
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"refresh failed on cluster_id={t['tenant_cluster_id']}: {exc}",
                )
                # Continue to the next target — partial-rotate is
                # better than all-or-nothing. The mutation surfaces
                # the error count back to the operator.
                continue
            if input.bounce_workloads:
                try:
                    n = await workflow.execute_activity(
                        bounce_workloads_consuming_bundle,
                        t,
                        start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    )
                    bounced_total += int(n or 0)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"bounce failed on cluster_id={t['tenant_cluster_id']}: {exc}",
                    )
        msg = (
            f"rotated bundle id={input.secret_bundle_id} across "
            f"{refreshed}/{len(targets)} cluster(s); bounced "
            f"{bounced_total} workload(s)"
        )
        if errors:
            msg += f"; errors={errors}"
        return WorkflowResult(
            ok=not errors,
            message=msg,
            data={
                "refreshed": refreshed,
                "bounced": bounced_total,
                "target_count": len(targets),
                "errors": errors,
            },
        )


@workflow.defn(name="DeleteSecretBundleFromClustersWorkflow")
class DeleteSecretBundleFromClustersWorkflow:
    """Cleanup hook on SecretBundle soft-delete (#365). Drops the
    materialized k8s Secret on every cluster it ever reached."""

    @workflow.run
    async def run(
        self,
        input: DeleteSecretBundleFromClustersInput,
    ) -> WorkflowResult:
        targets = await workflow.execute_activity(
            list_active_secret_bundle_targets,
            input.secret_bundle_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        if not targets:
            return WorkflowResult(
                ok=True,
                message="no active refs — nothing to delete",
            )
        deleted = 0
        errors: list[str] = []
        for t in targets:
            try:
                await workflow.execute_activity(
                    delete_secret_from_cluster,
                    t,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                )
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"delete failed on cluster_id={t['tenant_cluster_id']}: {exc}",
                )
                continue
        msg = f"deleted bundle id={input.secret_bundle_id} from " f"{deleted}/{len(targets)} cluster(s)"
        if errors:
            msg += f"; errors={errors}"
        return WorkflowResult(
            ok=not errors,
            message=msg,
            data={
                "deleted": deleted,
                "target_count": len(targets),
                "errors": errors,
            },
        )


@workflow.defn(name="SecretBundleScheduledRefreshWorkflow")
class SecretBundleScheduledRefreshWorkflow:
    """Hourly sweep — re-applies every active SecretBundle without
    bouncing. Catches external rotations (AWS SM scheduled rotation,
    Vault TTL renewal) so long-running pods don't run with values
    that drift behind the upstream secret manager."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        bundle_ids = await workflow.execute_activity(
            list_bundles_due_for_refresh,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        if not bundle_ids:
            return WorkflowResult(
                ok=True,
                message="no active bundles — nothing to refresh",
            )
        total_refreshed = 0
        total_targets = 0
        errors: list[str] = []
        for bundle_id in bundle_ids:
            targets = await workflow.execute_activity(
                list_active_secret_bundle_targets,
                bundle_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            total_targets += len(targets)
            for t in targets:
                try:
                    await workflow.execute_activity(
                        refresh_secret_bundle_in_cluster,
                        t,
                        start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    )
                    total_refreshed += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"bundle_id={bundle_id} cluster_id={t['tenant_cluster_id']}: {exc}",
                    )
                    continue
        msg = (
            f"scheduled refresh: {total_refreshed}/{total_targets} "
            f"target(s) across {len(bundle_ids)} bundle(s)"
        )
        if errors:
            msg += f"; errors={errors}"
        return WorkflowResult(
            ok=not errors,
            message=msg,
            data={
                "bundle_count": len(bundle_ids),
                "refreshed": total_refreshed,
                "target_count": total_targets,
                "errors": errors,
            },
        )
