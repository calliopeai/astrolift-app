"""ProvisionManagedDomainWorkflow (#781).

Five-step workflow that provisions a platform-managed DNS zone and
wildcard cert, registers the ManagedDomain row, and marks it active:

  VALIDATE_NS_DELEGATION / Step 1 — ``provision_dns_zone``
    Create the hosted zone via the cluster's DnsDriver. Returns
    zone_id + the authoritative nameservers the operator must set
    at their registrar. (The NS delegation check itself is a
    separate operator-facing validation; this step owns zone creation.)

  CONFIGURE_CERT_POLICY / Step 2 — ``request_wildcard_cert_for_zone``
    Request a wildcard cert for ``*.<zone>`` and write the DNS-01
    CNAME validation records into the zone via ``DnsDriver.ensure_record``.

  Poll loop / Step 3 — ``poll_cert_issuance`` × up to 60
    30-second sleep between polls; fails with a timeout error if the
    cert hasn't reached ``"issued"`` within the budget.

  REGISTER_MANAGED_DOMAIN / Step 4 — ``register_managed_domain_row``
    Upsert the ManagedDomain row with dns_config populated
    (zone_id + certificate ARN). Row starts inactive.

  HEALTH_CHECK + MARK_ACTIVE / Step 5 — ``mark_managed_domain_active``
    Flip dns_config["active"]=True so the platform serves the zone.

Returns ``WorkflowResult(ok=True, message="Zone <zone> provisioned and
active.")`` on success; ``WorkflowResult(ok=False, message=<error>)``
on any activity failure.

Workflow id pattern: ``ProvisionManagedDomainWorkflow-<cluster_id>-<zone>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import (
    ProvisionManagedDomainInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.provision_managed_domain import (
        mark_managed_domain_active,
        poll_cert_issuance,
        provision_dns_zone,
        register_managed_domain_row,
        request_wildcard_cert_for_zone,
    )


_ACTIVITY_TIMEOUT = timedelta(minutes=5)
_ACTIVITY_RETRY = RetryPolicy(maximum_attempts=3)

# Poll interval and attempt ceiling for cert issuance.
# 60 × 30 s = 30 min maximum wait before the workflow fails.
_POLL_INTERVAL = timedelta(seconds=30)
_POLL_MAX_ATTEMPTS = 60


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="ProvisionManagedDomainWorkflow")
class ProvisionManagedDomainWorkflow:
    @workflow.run
    async def run(
        self,
        input: ProvisionManagedDomainInput,
    ) -> WorkflowResult:
        zone = input.zone

        # Step 1 (VALIDATE_NS_DELEGATION): provision the hosted zone.
        try:
            zone_result = await workflow.execute_activity(
                provision_dns_zone,
                args=[input.cluster_id, zone],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"provision_dns_zone failed: {exc}"),
            )

        zone_id: str = zone_result.get("zone_id", "") if isinstance(zone_result, dict) else ""

        # Step 2 (CONFIGURE_CERT_POLICY): request wildcard cert + write
        # DNS-01 validation records.
        try:
            cert_id = await workflow.execute_activity(
                request_wildcard_cert_for_zone,
                args=[input.cluster_id, zone, zone_id],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"request_wildcard_cert_for_zone failed: {exc}"),
            )

        # Step 3 (poll loop): wait for cert to reach "issued".
        issued = False
        for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
            try:
                poll_result = await workflow.execute_activity(
                    poll_cert_issuance,
                    args=[input.cluster_id, zone, cert_id],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                return WorkflowResult(
                    ok=False,
                    message=_truncate(f"poll_cert_issuance failed: {exc}"),
                )

            status = poll_result.get("status", "") if isinstance(poll_result, dict) else ""
            if status == "issued":
                issued = True
                break
            if status == "failed":
                return WorkflowResult(
                    ok=False,
                    message=_truncate(
                        f"cert issuance failed for zone {zone!r}: "
                        + str(poll_result.get("message", ""))
                    ),
                )

            if attempt < _POLL_MAX_ATTEMPTS:
                await workflow.sleep(_POLL_INTERVAL)

        if not issued:
            return WorkflowResult(
                ok=False,
                message=_truncate(
                    f"cert issuance timed out for zone {zone!r} "
                    f"after {_POLL_MAX_ATTEMPTS} attempts"
                ),
            )

        # Step 4 (REGISTER_MANAGED_DOMAIN): persist the ManagedDomain row.
        try:
            await workflow.execute_activity(
                register_managed_domain_row,
                args=[input.cluster_id, zone, zone_id, cert_id],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"register_managed_domain_row failed: {exc}"),
            )

        # Step 5 (HEALTH_CHECK + MARK_ACTIVE): activate the zone.
        try:
            await workflow.execute_activity(
                mark_managed_domain_active,
                args=[input.cluster_id, zone],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"mark_managed_domain_active failed: {exc}"),
            )

        return WorkflowResult(
            ok=True,
            message=f"Zone {zone} provisioned and active.",
            data={
                "zone_id": zone_id,
                "cert_id": cert_id,
                "nameservers": (
                    zone_result.get("nameservers", [])
                    if isinstance(zone_result, dict)
                    else []
                ),
            },
        )
