"""DeregisterAppWorkflow (#392) — full app teardown (keep-vs-wipe opt-in).

Tears down every per-app resource class in strict dependency order
and ONLY soft-deletes the platform rows once every prior step has
either succeeded or returned an explicit "nothing to do" status. Re-
firing the mutation joins the existing run via a deterministic
workflow id (``DeregisterAppWorkflow-<app-guid>``), so a partial
failure can be resumed without manual rescue.

Grace-period cancel (#436 B): the workflow waits up to 5 minutes for
a ``cancel_teardown`` signal before any destructive step runs. The
``cancelAstroliftDeregister`` mutation sends the signal; if it
arrives within the window the workflow returns ``ok=False`` with a
``cancelled=True`` marker and NO destructive work executes. After
the window elapses the workflow proceeds with the teardown and
subsequent cancel signals are no-ops. The window is applied ONLY on a
first teardown: a RESUME run (re-trigger / reconcile of an app already
``tearing_down`` or ``deregistered``) skips it and drives the
idempotent fan-out straight away, so a stuck teardown finalizes
instead of re-waiting (and re-resetting) the window on every
re-trigger (#1034).

Resource teardown order (each step records its result on
``WorkflowResult.data["teardown"][<resource>]``):

  0. ``in_flight_deploys_aborted`` — signal-abort any running
     ``DeployAppWorkflow`` for the app's envs BEFORE the namespace
     cascade, so a deploy exits on its abort flag instead of polling a
     vanished namespace until timeout (#1004).
  1. ``deployments_cancelled`` — clear the app's runtime via
     ``delete_app_namespaces`` (the namespace delete cascades
     Deployments / ReplicaSets / Pods on every cluster the app is
     bound to) so subsequent steps don't race with new pods.
  2. ``managed_services`` — fan out ``deprovision_managed_service``
     for every ``ManagedService`` row with the operator's
     ``delete_data`` / ``force_destroy`` choice (default False/False —
     RDS final snapshot + S3 contents retained; #1000), then
     ``finalize_managed_service_deletion`` to soft-delete the row so it
     can't outlive its (now-deleted) backend resource (#1034). Every
     non-soft-deleted row is re-deprovisioned regardless of its status,
     so a row left stuck pending/active/deprovisioning by an interrupted
     run is driven to terminal on resume. Driver-level errors propagate
     so we can mark the resource as still-live on partial-failure resume.
  3. ``namespaces`` — re-run ``delete_app_namespaces`` after the
     managed-service deprovision to catch anything the deprovision
     of a stateful service re-emitted (Helm-managed PVCs etc.).
     Idempotent — already-gone namespaces are a clean no-op.
  2b. ``static_dns`` — ``delete_static_dns_records`` removes the
     platform-written ``CNAME host -> CloudFront`` record for every
     public ``static_site`` workload (no Ingress owned it, so it must
     be deleted explicitly). Idempotent; no-op for non-static apps.
  4. ``registry_repo`` — ``deprovision_app_registry_repo`` archives
     the app's image repo (or hard-deletes it when ``force_destroy``)
     and clears the platform's stored URI.
  5. ``identity_role`` — ``deprovision_app_identity_role`` deletes
     the IRSA / WI / FI role bound to the app's ServiceAccount.
  6. ``materialized_secrets`` — fan out ``delete_secret_from_cluster``
     for every (cluster, env, bundle) ref the app holds, then call
     ``revoke_app_secret_bundle_refs`` to soft-delete the binding
     rows so the operator's bundle refs are revoked.
  7. ``source_webhook`` — ``delete_app_source_webhook`` removes the
     push-event webhook on the source repo. Soft-fails on hosts
     that never installed one (GitHub-App, no-repo, no-connection).
  8. ``deploy_tokens`` — ``revoke_app_deploy_tokens`` soft-deletes
     active tokens so CI / cron pointed at the app fails loudly.
  9. ``platform_rows`` — ``soft_delete_app_records`` ONLY after every
     prior step succeeded or was explicitly skipped.

Partial failure semantics: each step is wrapped in a try/except so
one failure doesn't strand the rest of the teardown. The workflow
result's ``ok`` flag is True iff every step succeeded;
``still_live_resources`` lists the resource keys that failed so the
operator can re-fire the mutation. Soft-delete of the platform rows
is gated on a clean prior pass — if any prior step failed, the row
survives and a retry can converge.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import DeregisterAppInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        abort_in_flight_deploys,
        delete_app_namespaces,
        delete_app_source_webhook,
        delete_secret_from_cluster,
        delete_static_dns_records,
        deprovision_app_identity_role,
        deprovision_app_registry_repo,
        deprovision_managed_service,
        finalize_managed_service_deletion,
        list_app_managed_service_ids,
        list_app_secret_targets,
        mark_app_deregistered,
        mark_app_tearing_down,
        revoke_app_deploy_tokens,
        revoke_app_secret_bundle_refs,
        soft_delete_app_records,
    )

_QUICK_TIMEOUT = timedelta(minutes=2)
_NAMESPACE_TIMEOUT = timedelta(minutes=10)
_DEPROVISION_TIMEOUT = timedelta(minutes=30)

# Grace-period window the workflow holds before the first destructive
# activity runs. The FE renders a live countdown banner on the app
# detail page so the operator can cancel a slow-typing mistake. Five
# minutes is short enough that an abandoned cancel doesn't strand a
# real teardown indefinitely; long enough that an "oh no" reaction
# almost always lands inside the window.
GRACE_PERIOD = timedelta(minutes=5)

_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
_CLEANUP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
)


def _step_result(*, ok: bool, detail: str, data: Any = None) -> dict[str, Any]:
    """Standard per-step result shape recorded on ``WorkflowResult.data``."""
    return {"ok": ok, "detail": detail, "data": data}


@workflow.defn(name="DeregisterAppWorkflow")
class DeregisterAppWorkflow:
    def __init__(self) -> None:
        # Grace-period cancellation state (#436 B). Initialised here
        # rather than on the class so a Temporal workflow replay
        # rebuilds it cleanly on every history-driven re-execution
        # (the signal-handler delta is recorded in the workflow
        # history so the flag is deterministic across replays).
        self._cancel_requested: bool = False
        self._cancel_reason: str = ""

    @workflow.signal(name="cancel_teardown")
    def cancel_teardown(self, reason: str = "") -> None:
        """Operator-fired cancel signal (#436 B).

        The mutation ``cancelAstroliftDeregister`` sends this signal
        within the 5-minute grace window. If it lands before the
        ``wait_condition`` below fires, the workflow short-circuits
        without doing any destructive work. Signals received after the
        window are no-ops — the destructive activities have already
        started and Temporal owns retry semantics from there.
        """
        # Idempotent: a second signal arriving before the wait_condition
        # resolves still sets the flag (no-op) and doesn't lose the
        # first reason. Last-wins on the reason string when both are
        # non-empty so the most recent caller's context survives.
        self._cancel_requested = True
        if reason:
            self._cancel_reason = reason

    @workflow.run
    async def run(self, input: DeregisterAppInput) -> WorkflowResult:
        app_id = input.registered_app_id
        delete_data = bool(input.delete_data)
        force_destroy = bool(input.force_destroy)

        # Provisioning-status flip is observable in the UI immediately
        # — re-runs against the same workflow id no-op (Temporal de-
        # dupes), so this is safe to re-execute as a step header. The
        # activity reports whether the app was ALREADY tearing down (or
        # already deregistered) when this run started — i.e. whether this
        # is a resume rather than a first teardown (#1034).
        already_tearing_down = await workflow.execute_activity(
            mark_app_tearing_down,
            app_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        # ---- Grace-period window (#436 B) -----------------------------
        # Hold here so the operator gets a chance to cancel a typo'd
        # confirm before destructive work starts. ``wait_condition``
        # returns when the predicate becomes true; it raises
        # ``asyncio.TimeoutError`` if the timeout elapses with the
        # predicate still false. We only honour cancellation at this
        # single boundary so the FE's "cancel within X" promise is
        # observable and the destructive work below is monotonic.
        #
        # ONLY on a first teardown (#1034). When this run is a RESUME of an
        # already-tearing-down (or terminated) teardown — a re-trigger or a
        # reconcile of a stuck app — re-waiting the grace window stalls the
        # very finalization the resume exists to drive: each re-trigger
        # restarts the workflow (TERMINATE_IF_RUNNING), so re-applying the
        # full window would let back-to-back re-triggers reset the timer
        # forever and the stuck rows would never finalize. A resume has no
        # accidental first click to undo, so it proceeds straight to the
        # idempotent fan-out.
        if not already_tearing_down:
            try:
                await workflow.wait_condition(
                    lambda: self._cancel_requested,
                    timeout=GRACE_PERIOD,
                )
            except TimeoutError:
                # Grace window elapsed without a cancel signal — proceed
                # to the destructive steps. This is the happy path.
                pass
            if self._cancel_requested:
                workflow.logger.info(
                    "deregister cancelled within grace window app=%s reason=%s",
                    app_id,
                    self._cancel_reason,
                )
                return WorkflowResult(
                    ok=False,
                    message="cancelled within grace window",
                    data={
                        "cancelled": True,
                        "cancel_reason": self._cancel_reason,
                        "teardown": {},
                        "still_live_resources": [],
                    },
                )

        teardown: dict[str, dict[str, Any]] = {}
        still_live: list[str] = []

        # 0. Abort any in-flight deploy BEFORE the namespace cascade. Deleting
        #    the k8s namespace does NOT touch the Temporal workflow, so a
        #    running DeployAppWorkflow would otherwise keep polling a vanished
        #    namespace for its full timeout, stranding a worker (#1004). The
        #    signal makes it observe its abort flag and exit cleanly first.
        try:
            aborted = await workflow.execute_activity(
                abort_in_flight_deploys,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            teardown["in_flight_deploys_aborted"] = _step_result(
                ok=True,
                detail=f"signalled {len(aborted)} in-flight deploy(s)",
                data=aborted,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; teardown proceeds
            teardown["in_flight_deploys_aborted"] = _step_result(
                ok=False,
                detail=str(exc),
            )

        # 1. Delete the app's namespaces — cascades the runtime resources
        #    (Deployments/ReplicaSets/Pods/Services/Ingresses) so nothing new
        #    comes up while subsequent steps run.
        try:
            namespaces_deleted = await workflow.execute_activity(
                delete_app_namespaces,
                app_id,
                start_to_close_timeout=_NAMESPACE_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["deployments_cancelled"] = _step_result(
                ok=True,
                detail=f"deleted {len(namespaces_deleted)} namespace(s)",
                data=namespaces_deleted,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["deployments_cancelled"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("deployments_cancelled")

        # 2. Managed-service deprovision — fan out one activity per
        #    active ManagedService row. Failures collect per-id so a
        #    single stuck service doesn't strand the rest of the pass.
        ms_results: list[dict[str, Any]] = []
        ms_failed = False
        try:
            ms_ids = await workflow.execute_activity(
                list_app_managed_service_ids,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            ms_ids = []
            ms_failed = True
            ms_results.append({"ok": False, "message": str(exc)})

        for ms_id in ms_ids:
            # ``list_app_managed_service_ids`` returns EVERY non-soft-deleted
            # row regardless of status — pending/active/deprovisioning all
            # flow through here. We deliberately do NOT skip a row because it
            # looks "in progress" (e.g. left at deprovisioning by an
            # interrupted prior run); a stuck row is re-deprovisioned and
            # driven to terminal, never assumed to be owned by another worker
            # (#1034).
            try:
                res = await workflow.execute_activity(
                    deprovision_managed_service,
                    args=[ms_id, delete_data, force_destroy],
                    start_to_close_timeout=_DEPROVISION_TIMEOUT,
                    retry_policy=_CLEANUP_RETRY,
                )
                if not res.get("ok"):
                    ms_results.append(
                        {
                            "ok": False,
                            "managed_service_id": ms_id,
                            "message": str(res.get("message", "")),
                        },
                    )
                    ms_failed = True
                    continue
                # Finalize: soft-delete the platform row + flip it off its
                # transient status now that the backend resource is gone.
                # Without this the fan-out deleted the cloud resource but left
                # the row stuck pending/active, so the app never finalized and
                # sat at tearing_down forever (#1034). Idempotent — a resumed
                # run over an already-finalized row no-ops.
                await workflow.execute_activity(
                    finalize_managed_service_deletion,
                    ms_id,
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
                ms_results.append(
                    {
                        "ok": True,
                        "managed_service_id": ms_id,
                        "message": str(res.get("message", "")),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                ms_results.append(
                    {
                        "ok": False,
                        "managed_service_id": ms_id,
                        "message": str(exc),
                    },
                )
                ms_failed = True

        teardown["managed_services"] = _step_result(
            ok=not ms_failed,
            detail=(
                f"{sum(1 for r in ms_results if r.get('ok'))}/{len(ms_results)} deprovision result(s) ok"
            ),
            data=ms_results,
        )
        if ms_failed:
            still_live.append("managed_services")

        # 2b. Static-site DNS records (#1010). A static_site workload's CNAME
        #     (host -> CloudFront domain) is written explicitly by the deploy
        #     flow (no Ingress, so external-dns never owned it) — so it must be
        #     removed explicitly here. Idempotent: a missing record is swallowed.
        #     No-op when the app has no public static workload.
        try:
            dns_summary = await workflow.execute_activity(
                delete_static_dns_records,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["static_dns"] = _step_result(
                ok=True,
                detail=f"deleted {len(dns_summary.get('deleted', []))} static CNAME(s)",
                data=dns_summary,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["static_dns"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("static_dns")

        # 3. Second namespace pass — captures anything the managed-
        #    service deprovision re-emitted (Helm cleanup hooks etc.).
        #    Idempotent.
        try:
            namespaces_deleted_2 = await workflow.execute_activity(
                delete_app_namespaces,
                app_id,
                start_to_close_timeout=_NAMESPACE_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["namespaces"] = _step_result(
                ok=True,
                detail=f"second pass: {len(namespaces_deleted_2)} namespace(s)",
                data=namespaces_deleted_2,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["namespaces"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("namespaces")

        # 4. Registry repo deprovision + URI clear. Archive on a safe teardown
        #    so the image history survives a re-register; hard-delete only when
        #    the operator opted into force_destroy (the full wipe corner; #1000).
        archive_repo = not force_destroy
        try:
            repo_summary = await workflow.execute_activity(
                deprovision_app_registry_repo,
                args=[app_id, archive_repo],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["registry_repo"] = _step_result(
                ok=True,
                detail=(f"{'archived' if archive_repo else 'deleted'} repo {repo_summary.get('repo', '')!r}"),
                data=repo_summary,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["registry_repo"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("registry_repo")

        # 5. Identity role delete.
        try:
            id_summary = await workflow.execute_activity(
                deprovision_app_identity_role,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["identity_role"] = _step_result(
                ok=True,
                detail=f"deleted identity role {id_summary.get('role', '')!r}",
                data=id_summary,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["identity_role"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("identity_role")

        # 6. Materialized cluster secrets — drop the per-cluster
        #    Secret then soft-delete the AppSecretBundleRef bindings.
        secret_results: list[dict[str, Any]] = []
        secret_failed = False
        try:
            targets = await workflow.execute_activity(
                list_app_secret_targets,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            targets = []
            secret_failed = True
            secret_results.append({"ok": False, "message": str(exc)})

        for target in targets:
            if target.get("tenant_cluster_id") is None:
                # No cluster bound — nothing to drop on the cluster,
                # the ref soft-delete pass picks it up.
                secret_results.append(
                    {
                        "ok": True,
                        "bundle_slug": target.get("bundle_slug", ""),
                        "detail": "no cluster bound; skipped cluster-side delete",
                    },
                )
                continue
            try:
                summary = await workflow.execute_activity(
                    delete_secret_from_cluster,
                    target,
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_CLEANUP_RETRY,
                )
                errs = list(summary.get("errors", []) or [])
                secret_results.append(
                    {
                        "ok": not errs,
                        "bundle_slug": target.get("bundle_slug", ""),
                        "cluster_slug": summary.get("cluster_slug", ""),
                        "errors": errs,
                    },
                )
                if errs:
                    secret_failed = True
            except Exception as exc:  # noqa: BLE001
                secret_results.append(
                    {
                        "ok": False,
                        "bundle_slug": target.get("bundle_slug", ""),
                        "message": str(exc),
                    },
                )
                secret_failed = True

        if not secret_failed:
            # Only revoke the bindings when every cluster-side delete
            # converged; otherwise a retry needs the refs to know
            # which (cluster, env, bundle) rows to re-target.
            try:
                revoked = await workflow.execute_activity(
                    revoke_app_secret_bundle_refs,
                    app_id,
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
                teardown["materialized_secrets"] = _step_result(
                    ok=True,
                    detail=(
                        f"{len(secret_results)} cluster-side delete(s) ok; "
                        f"{revoked} bundle ref(s) soft-deleted"
                    ),
                    data={"per_target": secret_results, "refs_revoked": revoked},
                )
            except Exception as exc:  # noqa: BLE001
                teardown["materialized_secrets"] = _step_result(
                    ok=False,
                    detail=f"ref soft-delete failed: {exc}",
                    data={"per_target": secret_results},
                )
                still_live.append("materialized_secrets")
        else:
            teardown["materialized_secrets"] = _step_result(
                ok=False,
                detail="cluster-side secret delete had failures; refs kept for retry",
                data={"per_target": secret_results},
            )
            still_live.append("materialized_secrets")

        # 7. Source-host push-event webhook.
        try:
            wh_summary = await workflow.execute_activity(
                delete_app_source_webhook,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_CLEANUP_RETRY,
            )
            teardown["source_webhook"] = _step_result(
                ok=True,
                detail=str(wh_summary.get("detail", "")),
                data=wh_summary,
            )
        except Exception as exc:  # noqa: BLE001
            teardown["source_webhook"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("source_webhook")

        # 8. Revoke deploy tokens — CI / cron pointed at the app
        #    fails loudly rather than silently.
        try:
            tokens_revoked = await workflow.execute_activity(
                revoke_app_deploy_tokens,
                app_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            teardown["deploy_tokens"] = _step_result(
                ok=True,
                detail=f"revoked {tokens_revoked} deploy token(s)",
                data={"revoked": tokens_revoked},
            )
        except Exception as exc:  # noqa: BLE001
            teardown["deploy_tokens"] = _step_result(
                ok=False,
                detail=str(exc),
            )
            still_live.append("deploy_tokens")

        # 9. Platform rows soft-delete — gated on a clean prior pass.
        if not still_live:
            try:
                summary = await workflow.execute_activity(
                    soft_delete_app_records,
                    app_id,
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
                teardown["platform_rows"] = _step_result(
                    ok=True,
                    detail=f"soft-delete summary={summary}",
                    data=summary,
                )
                await workflow.execute_activity(
                    mark_app_deregistered,
                    app_id,
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                teardown["platform_rows"] = _step_result(
                    ok=False,
                    detail=str(exc),
                )
                still_live.append("platform_rows")
        else:
            teardown["platform_rows"] = _step_result(
                ok=False,
                detail=("skipped: prior steps still live — soft-delete is gated on a clean teardown pass"),
            )

        ok = not still_live
        message_parts = [
            f"{sum(1 for k, v in teardown.items() if v['ok'])}/{len(teardown)} step(s) ok",
        ]
        if still_live:
            message_parts.append(f"still_live={still_live}")
        return WorkflowResult(
            ok=ok,
            message=" | ".join(message_parts),
            data={
                "teardown": teardown,
                "still_live_resources": still_live,
            },
        )
