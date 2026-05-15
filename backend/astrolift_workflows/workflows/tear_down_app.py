"""TearDownAppWorkflow (#358) — symmetric inverse of OnboardAppWorkflow.

Orchestrates the per-step ordering encoded in
``astrolift_workflows.app_teardown`` (the pure-policy module):

  1. mark_app_tearing_down — UI shows the in-flight state
  2. delete_app_namespaces — cascade-deletes Deployments / Services /
     Ingresses / Secrets / ConfigMaps / PVCs across every cluster the
     app is deployed to. Soft-fails per-cluster so a single
     unreachable cluster doesn't strand the teardown.
  3. deprovision_app_managed_services — fans out one
     ``DeprovisionManagedServiceWorkflow`` (#320) per active
     ``ManagedService`` row. ``delete_data`` + ``force_destroy``
     flow through from the operator's input. Children execute in
     parallel; the workflow waits for all.
  4. revoke_app_deploy_tokens — soft-deletes deploy tokens so CI /
     cron pointed at the app fails loudly.
  5. soft_delete_app_records — soft-delete the platform rows
     (Deployment, AppEnvironment, AppSecretBundleRef, RegisteredApp).
     Audit + event log kinds are retained per the policy module.
  6. mark_app_deregistered — terminal state.

Per-capability cleanup (DNS records, IRSA / WI / FI roles,
ECR / GAR / ACR repos, TLS certs) composes in here as the
per-capability tickets land their activities (#354 + per-cloud
lifecycle epics). The skeleton shipped now does the part the
platform has activities for.

Workflow id pattern: ``TearDownAppWorkflow-<app-guid>``.
Idempotent — re-runs against an already-deregistered app
short-circuit cleanly via the policy module's ``steps_to_run``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import (
    DeprovisionManagedServiceInput,
    TearDownAppInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        delete_app_namespaces,
        list_app_managed_service_ids,
        mark_app_deregistered,
        mark_app_tearing_down,
        revoke_app_deploy_tokens,
        soft_delete_app_records,
    )
    from astrolift_workflows.workflows.deprovision_managed_service import (
        DeprovisionManagedServiceWorkflow,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
_NAMESPACE_TIMEOUT = timedelta(minutes=10)
# Each managed-service child workflow has its own internal 20-min
# activity timeout (RDS final-snapshot etc.). The parent waits
# generously so a fleet of bound services doesn't time out at the
# orchestration layer before its children get their chance.
_CHILD_DEPROVISION_TIMEOUT = timedelta(minutes=30)

_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
_CLEANUP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=4,
)
_MARK_ERROR_RETRY = RetryPolicy(maximum_attempts=1)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="TearDownAppWorkflow")
class TearDownAppWorkflow:
    @workflow.run
    async def run(self, input: TearDownAppInput) -> WorkflowResult:
        app_id = input.registered_app_id
        delete_data = bool(input.delete_data)
        force_destroy = bool(input.force_destroy)

        await workflow.execute_activity(
            mark_app_tearing_down,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        # Step 2 — namespace delete cascades runtime resources.
        namespaces_deleted: list[str] = []
        try:
            namespaces_deleted = await workflow.execute_activity(
                delete_app_namespaces,
                app_id,
                start_to_close_timeout=_NAMESPACE_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            # Don't abort — the per-cluster activity already logs +
            # skips individual failures; an exception here means
            # something more fundamental (no clusters bound? db
            # error?). Continue to the managed-service deprovision
            # so persistent data still gets handled.
            workflow.logger.warning(
                "delete_app_namespaces raised: %s — continuing", exc,
            )

        # Step 3 — fan out child workflows for managed-service
        # deprovision. One child per active ManagedService row.
        ms_ids = await workflow.execute_activity(
            list_app_managed_service_ids,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        child_results: list[dict[str, Any]] = []
        if ms_ids:
            child_handles = []
            for ms_id in ms_ids:
                handle = await workflow.start_child_workflow(
                    DeprovisionManagedServiceWorkflow.run,
                    DeprovisionManagedServiceInput(
                        managed_service_id=ms_id,
                        actor=input.actor,
                        delete_data=delete_data,
                        force_destroy=force_destroy,
                    ),
                    id=(
                        f"DeprovisionManagedServiceWorkflow"
                        f"-app-{app_id}-svc-{ms_id}"
                    ),
                    task_timeout=_CHILD_DEPROVISION_TIMEOUT,
                )
                child_handles.append(handle)
            for handle in child_handles:
                try:
                    res = await handle
                    child_results.append(
                        {
                            "ok": getattr(res, "ok", False),
                            "message": getattr(res, "message", ""),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    child_results.append(
                        {"ok": False, "message": str(exc)},
                    )

        # Step 4 — revoke deploy tokens (loud-fail CI / cron).
        tokens_revoked = await workflow.execute_activity(
            revoke_app_deploy_tokens,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        # Step 5 — soft-delete the platform rows.
        soft_delete_summary = await workflow.execute_activity(
            soft_delete_app_records,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        # Step 6 — terminal state.
        await workflow.execute_activity(
            mark_app_deregistered,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        failures = [
            r for r in child_results if not r.get("ok", False)
        ]
        message_parts = [
            f"namespaces deleted: {len(namespaces_deleted)}",
            f"managed services: {len(child_results) - len(failures)}/"
            f"{len(child_results)} ok",
            f"tokens revoked: {tokens_revoked}",
            f"soft-deleted: {soft_delete_summary}",
        ]
        if failures:
            message_parts.append(
                f"failures: {[f['message'] for f in failures]}",
            )
        return WorkflowResult(
            ok=not failures,
            message=_truncate(" | ".join(message_parts)),
            data={
                "namespaces_deleted": namespaces_deleted,
                "managed_service_results": child_results,
                "tokens_revoked": tokens_revoked,
                "soft_delete_summary": soft_delete_summary,
            },
        )
