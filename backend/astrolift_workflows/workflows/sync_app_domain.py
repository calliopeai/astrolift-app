"""SyncAppDomainWorkflow (#143, spec 06 §4.17).

Reconciles routing after ``setAppSubdomain`` changed an app's platform
hostname. The mutation is the source-of-truth write; this workflow is what
makes the live traffic follow it, without a full redeploy.

Step order, deadlines, the hostname diff, the wildcard-cert check and the
rollback plan all come from ``astrolift_workflows.sync_domain`` -- the
policy module. This workflow binds each of its steps to an activity:

  UPDATE_DNS        ensure_static_dns        explicit CNAMEs for cdn-backed
                                            workloads; no-op for the
                                            ingress-backed case, where DNS
                                            follows the Ingress
  PATCH_INGRESS     update_secrets,         re-render + server-side apply;
                    apply_manifests         the Ingress keeps its name, so
                                            the host rule is replaced.
                                            update_secrets goes first (#1952),
                                            same order and reason as the
                                            deploy path (#1758)
  REQUEST_CERT      request_wildcard_cert_   skipped when the zone's wildcard
                    for_zone                already covers the new hostname,
                                            which is the common case
  WAIT_PROPAGATION  wait_dns
  VERIFY_E2E        verify_app_hostnames

Any step failing rolls the reversible steps back in reverse order after
restoring the previous subdomain, so a half-applied rename ends up on the
old routing rather than stranded between two names.

Known gap: a cdn-backed (static_site / faas) workload also needs its
distribution alias list refreshed for the new host; that happens on the
next deploy, not here.

Workflow id pattern: ``SyncAppDomainWorkflow-<app-guid>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import SyncAppDomainInput, WorkflowResult
from astrolift_workflows.sync_domain import (
    SYNC_ORDER,
    HostnameAction,
    SyncProgress,
    SyncStep,
    diff_hostnames,
    is_rollback_needed,
    needs_per_domain_cert,
    rollback_steps,
    step_deadline_seconds,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        ensure_static_dns,
        plan_app_domain_sync,
        request_wildcard_cert_for_zone,
        revert_app_subdomain,
        update_secrets,
        verify_app_hostnames,
        wait_dns,
    )


_PLAN_TIMEOUT = timedelta(minutes=2)
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

# Steps whose undo is a re-apply with the old subdomain restored. A zone
# wildcard is shared by every app on the zone, so REQUEST_CERT is never
# undone -- revoking it to unwind one failed rename would break the others.
_REPLAYABLE_ON_ROLLBACK = (SyncStep.UPDATE_DNS, SyncStep.PATCH_INGRESS)


def plan_steps(
    *,
    old_hostnames: list[str],
    new_hostnames: list[str],
    wildcard_sans: list[str],
) -> tuple[tuple[SyncStep, ...], tuple[str, ...]]:
    """Which steps this environment needs, and the hostnames being added.

    Empty steps means the diff is all KEEP -- the rename did not change any
    hostname in this environment (e.g. the app has no public workload) and
    the workflow must not touch its routing.
    """
    deltas = diff_hostnames(old=old_hostnames, new=new_hostnames)
    added = tuple(d.hostname for d in deltas if d.action == HostnameAction.ADD)
    removed = tuple(d.hostname for d in deltas if d.action == HostnameAction.REMOVE)
    if not added and not removed:
        return (), ()

    steps: list[SyncStep] = []
    for step in SYNC_ORDER:
        if step == SyncStep.REQUEST_CERT and not any(
            needs_per_domain_cert(hostname=h, wildcard_sans=wildcard_sans) for h in added
        ):
            continue
        steps.append(step)
    return tuple(steps), added


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="SyncAppDomainWorkflow")
class SyncAppDomainWorkflow:
    @workflow.run
    async def run(self, input: SyncAppDomainInput) -> WorkflowResult:
        plan = await workflow.execute_activity(
            plan_app_domain_sync,
            args=[input.registered_app_id, input.previous_subdomain],
            start_to_close_timeout=_PLAN_TIMEOUT,
            retry_policy=_RETRY,
        )
        env_plans = plan.get("plans", []) if isinstance(plan, dict) else []
        if not env_plans:
            return WorkflowResult(
                ok=True,
                message="no live environment with a platform hostname, nothing to sync",
            )

        synced: list[str] = []
        for env_plan in env_plans:
            steps, added = plan_steps(
                old_hostnames=list(env_plan.get("old_hostnames", [])),
                new_hostnames=list(env_plan.get("new_hostnames", [])),
                wildcard_sans=list(env_plan.get("wildcard_sans", [])),
            )
            if not steps:
                continue

            completed: list[SyncStep] = []
            failed: SyncStep | None = None
            error = ""
            for step in steps:
                try:
                    await self._run_step(step, env_plan=env_plan, added=added)
                except Exception as exc:  # noqa: BLE001 — drives the rollback
                    failed = step
                    error = str(exc)
                    break
                completed.append(step)

            progress = SyncProgress(
                completed_steps=tuple(completed),
                failed_step=failed,
            )
            if failed is not None:
                await self._rollback(input, env_plan=env_plan, progress=progress)
                return WorkflowResult(
                    ok=False,
                    message=_truncate(
                        f"{failed.value} failed for env {env_plan.get('app_environment_id')}; "
                        f"rolled back to {input.previous_subdomain!r}: {error}"
                    ),
                )
            synced.extend(added)

        if not synced:
            return WorkflowResult(ok=True, message="no hostname changed, nothing to sync")
        return WorkflowResult(
            ok=True,
            message=f"routing now serves {', '.join(synced)}",
            data={"hostnames": synced},
        )

    async def _run_step(
        self,
        step: SyncStep,
        *,
        env_plan: dict,
        added: tuple[str, ...],
    ) -> None:
        deadline = timedelta(seconds=step_deadline_seconds(step=step))
        deployment_id = env_plan["deployment_id"]

        if step == SyncStep.UPDATE_DNS:
            await workflow.execute_activity(
                ensure_static_dns,
                deployment_id,
                start_to_close_timeout=deadline,
                retry_policy=_RETRY,
            )
            return
        if step == SyncStep.PATCH_INGRESS:
            # Secrets before the workloads that read them (#1952), same
            # order and reason as the deploy path (#1758): apply_manifests
            # re-renders every workload, pod-template digest included, so
            # running it first can roll pods onto the new digest before
            # update_secrets has written the Secret it names -- the pods
            # then never roll again, because the digest already matches.
            # Patched so a sync in flight when this ships replays in its
            # original order.
            if workflow.patched("sync-domain-secrets-before-apply"):
                await workflow.execute_activity(
                    update_secrets,
                    deployment_id,
                    start_to_close_timeout=deadline,
                    retry_policy=_RETRY,
                )
            await workflow.execute_activity(
                apply_manifests,
                deployment_id,
                start_to_close_timeout=deadline,
                retry_policy=_RETRY,
            )
            return
        if step == SyncStep.REQUEST_CERT:
            await workflow.execute_activity(
                request_wildcard_cert_for_zone,
                args=[
                    env_plan["cluster_id"],
                    env_plan["zone"],
                    env_plan.get("zone_id", ""),
                ],
                start_to_close_timeout=deadline,
                retry_policy=_RETRY,
            )
            return
        if step == SyncStep.WAIT_PROPAGATION:
            await workflow.execute_activity(
                wait_dns,
                args=[deployment_id, int(deadline.total_seconds())],
                start_to_close_timeout=deadline + timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            return
        # VERIFY_E2E
        await workflow.execute_activity(
            verify_app_hostnames,
            args=[list(added), int(deadline.total_seconds())],
            start_to_close_timeout=deadline + timedelta(seconds=30),
            retry_policy=_RETRY,
        )

    async def _rollback(
        self,
        input: SyncAppDomainInput,
        *,
        env_plan: dict,
        progress: SyncProgress,
    ) -> None:
        if not is_rollback_needed(progress=progress):
            return
        # The row first: every renderer derives the hostname from
        # ``RegisteredApp.subdomain``, so the replayed steps below only
        # restore the old routing once the old value is back on the row.
        await workflow.execute_activity(
            revert_app_subdomain,
            args=[input.registered_app_id, input.previous_subdomain],
            start_to_close_timeout=_PLAN_TIMEOUT,
            retry_policy=_RETRY,
        )
        for step in rollback_steps(progress=progress):
            if step not in _REPLAYABLE_ON_ROLLBACK:
                continue
            try:
                await self._run_step(step, env_plan=env_plan, added=())
            except Exception:  # noqa: BLE001
                # Best-effort: a failed undo must not mask the original
                # failure the caller needs to see.
                workflow.logger.exception("rollback step %s failed", step.value)
