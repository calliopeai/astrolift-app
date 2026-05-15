"""ValidateCustomDomainWorkflow (#397).

Drives the custom-domain handshake validation loop:

  1. ensure_platform_managed_records — when the domain's parent zone
     is platform-managed, write the required records via the bound
     cluster's ``DnsDriver``. No-op for operator-self-serve.
  2. probe_required_records — query authoritative nameservers + flip
     per-row ``propagated`` state. Retries with exponential backoff
     to cover the propagation window (typically 5-15 min on first
     add); we poll a few times so a slow propagation eventually
     succeeds without operator-side action.
  3. transition_domain_status — flip the parent row to ``validated``
     (all rows propagated) or ``failed`` (still pending past the
     last retry — operator sees the message and clicks Recheck once
     they've fixed their records).

Workflow id pattern: ``ValidateCustomDomainWorkflow-<domain-guid>``.
Re-firing joins the existing run. Cert issuance lands in PR 5 of
the umbrella ticket.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import (
    ValidateCustomDomainInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        ensure_platform_managed_records,
        probe_required_records,
        transition_domain_status,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
_PROBE_TIMEOUT = timedelta(seconds=60)
_ENSURE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
# Probe budget is generous — DNS propagation can take 5-15 min on a
# fresh record at most authoritative providers. We retry the probe
# itself rather than the workflow because each tick re-reads the
# database for fresh expectations.
_PROBE_ATTEMPT_CAP = 12
_PROBE_BACKOFF = timedelta(seconds=30)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="ValidateCustomDomainWorkflow")
class ValidateCustomDomainWorkflow:
    @workflow.run
    async def run(self, input: ValidateCustomDomainInput) -> WorkflowResult:
        domain_id = input.custom_domain_id

        # Step 1 — driver-side record creation when the parent zone
        # is platform-managed.
        try:
            ensure_result = await workflow.execute_activity(
                ensure_platform_managed_records,
                domain_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_ENSURE_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            await workflow.execute_activity(
                transition_domain_status,
                args=[
                    domain_id,
                    False,
                    f"platform DNS write failed: {exc}",
                ],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_ENSURE_RETRY,
            )
            return WorkflowResult(
                ok=False,
                message=_truncate(f"ensure failed: {exc}"),
            )

        # Step 2 — probe the authoritative NS until either all rows
        # propagated or we exhaust the attempt budget.
        last_probe: dict | None = None
        attempt = 0
        while attempt < _PROBE_ATTEMPT_CAP:
            attempt += 1
            try:
                last_probe = await workflow.execute_activity(
                    probe_required_records,
                    domain_id,
                    start_to_close_timeout=_PROBE_TIMEOUT,
                    retry_policy=_ENSURE_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                last_probe = {
                    "all_propagated": False,
                    "error": str(exc),
                }
            if last_probe and last_probe.get("all_propagated"):
                break
            await workflow.sleep(_PROBE_BACKOFF)

        success = bool(last_probe and last_probe.get("all_propagated"))
        if success:
            await workflow.execute_activity(
                transition_domain_status,
                args=[domain_id, True],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_ENSURE_RETRY,
            )
            return WorkflowResult(
                ok=True,
                message="custom domain validated",
                data={
                    "ensure": ensure_result if isinstance(ensure_result, dict) else None,
                    "probe": last_probe,
                },
            )

        # Compose a single operator-facing reason from the per-row
        # messages on the last probe.
        rows = (
            last_probe.get("records", []) if isinstance(last_probe, dict)
            else []
        )
        failed_msgs = [
            r.get("message", "") for r in rows
            if not r.get("propagated", False)
        ]
        reason = (
            "; ".join(m for m in failed_msgs if m)
            or "DNS records have not propagated"
        )
        await workflow.execute_activity(
            transition_domain_status,
            args=[domain_id, False, reason],
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_ENSURE_RETRY,
        )
        return WorkflowResult(
            ok=False,
            message=_truncate(reason),
            data={
                "ensure": ensure_result if isinstance(ensure_result, dict) else None,
                "probe": last_probe,
            },
        )
