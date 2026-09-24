"""MigrateAppWorkflow — move an AppEnvironment from one cluster to
another.

Both clusters run the app simultaneously between apply-to-target and
the FK switch — that overlap is the safety net for target-side
failures (operator aborts before the switch and the source keeps
serving traffic).

Activity sequence:

  1. validate_migration_target:      target is MANAGED + different from source.
  2. materialize_secrets_on_target:  namespace + the Secrets the workloads
                                      name in envFrom, on the target.
  3. apply_to_target_cluster:        render against the deployment's config,
                                      apply to target namespace + driver.
  4. poll_rollout_on_target:         wait for workloads to roll out on target.
  5. switch_app_env_binding:         atomic FK flip; future deploys land on target.
  6. drain_source_cluster:           optional best-effort cleanup of source resources.

Workflow id pattern: ``MigrateAppWorkflow-<env-guid>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import MigrateAppInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_to_target_cluster,
        drain_source_cluster,
        materialize_secrets_on_target,
        poll_rollout_on_target,
        switch_app_env_binding,
        validate_migration_target,
    )


_TIMEOUT = timedelta(minutes=15)
_DRAIN_TIMEOUT = timedelta(minutes=10)

_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
# Validation is a read-only check; one attempt suffices.
_VALIDATE_RETRY = RetryPolicy(maximum_attempts=1)
# The switch is a single atomic SQL UPDATE — don't retry it. If the DB
# was unreachable, the operator manually re-runs the migration.
_SWITCH_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="MigrateAppWorkflow")
class MigrateAppWorkflow:
    @workflow.run
    async def run(self, input: MigrateAppInput) -> WorkflowResult:
        # Step 1: validate.
        try:
            await workflow.execute_activity(
                validate_migration_target,
                args=[input.app_environment_id, input.target_cluster_id],
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_VALIDATE_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            return WorkflowResult(ok=False, message=f"validation failed: {exc}")

        # Step 2: the literal, bundle and binding Secrets the workloads
        # name in envFrom. apply_to_target_cluster applies only the
        # workloads, so without this their pods reference Secrets the
        # target never had (#1758 review, M1). Patched so a migration
        # already running when this shipped replays without the step.
        if workflow.patched("migrate-materialize-target-secrets"):
            try:
                await workflow.execute_activity(
                    materialize_secrets_on_target,
                    args=[input.deployment_id, input.target_cluster_id],
                    start_to_close_timeout=_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
            except Exception as exc:  # noqa: BLE001 - abort before touching target workloads
                return WorkflowResult(
                    ok=False,
                    message=f"secrets on target failed; source still serving traffic: {exc}",
                )

        # Step 3: apply to target. Source still owns traffic.
        try:
            await workflow.execute_activity(
                apply_to_target_cluster,
                args=[input.deployment_id, input.target_cluster_id],
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — abort before switch
            return WorkflowResult(
                ok=False,
                message=f"apply to target failed; source still serving traffic: {exc}",
            )

        # Step 4: verify rollout on target.
        try:
            await workflow.execute_activity(
                poll_rollout_on_target,
                args=[input.deployment_id, input.target_cluster_id],
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — abort before switch
            return WorkflowResult(
                ok=False,
                message=f"target rollout failed; source still serving traffic: {exc}",
            )

        # Step 5: atomic flip. From here on the target is the binding.
        source_cluster_id = await workflow.execute_activity(
            switch_app_env_binding,
            args=[input.app_environment_id, input.target_cluster_id],
            start_to_close_timeout=_TIMEOUT,
            retry_policy=_SWITCH_RETRY,
        )

        # Step 6: optional drain. Failure is non-fatal; the migration
        # is complete once the switch lands.
        if not input.drain_source:
            return WorkflowResult(
                ok=True,
                message="migration complete; source resources left in place (drain_source=false)",
            )

        try:
            errors = await workflow.execute_activity(
                drain_source_cluster,
                args=[input.registered_app_id, input.app_environment_id, source_cluster_id],
                start_to_close_timeout=_DRAIN_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal cleanup
            return WorkflowResult(
                ok=True,
                message=f"migration complete; source drain raised {exc} — clean up manually",
            )
        if errors:
            return WorkflowResult(
                ok=True,
                message=f"migration complete; source drain had {len(errors)} error(s) — clean up manually",
            )
        return WorkflowResult(ok=True, message="migration complete; source cluster drained")
