"""ProvisionManagedDomainWorkflow (#781).

Cloud-agnostic two-step domain provisioning:

Step 1 — Zone creation (platform-managed DNS only):
  When ``input.is_platform_managed_zone`` is True: ``provision_dns_zone``
  creates the hosted zone via the cluster's DnsDriver and returns the
  zone_id + authoritative nameservers. The ManagedDomain row stores the
  nameservers so the operator can delegate at their registrar.
  When False the caller owns the zone; this step is skipped and zone_id
  stays empty.

Step 2 — Cert request:
  ``request_wildcard_cert_for_zone`` requests a wildcard cert for
  ``*.<zone>`` and writes the DNS-01 CNAME validation records into the
  zone. The cert_id is stored in workflow-local state so signals can
  reference it.

The workflow then enters a poll loop (30 s sleep × 144 attempts = 72 h
maximum) calling ``poll_cert_issuance``. Two signals interrupt the loop:

  revalidate — wake the current sleep and check cert status immediately
               without changing cert_id.
  reissue    — call ``reissue_cert`` to revoke the current cert and
               request a fresh one, obtain a new cert_id, then resume
               polling the replacement.

Once the cert reaches "issued": ``register_managed_domain_row`` upserts
the ManagedDomain row, ``validate_ns_delegation`` confirms the registrar
actually delegates the zone to the nameservers Step 1 returned, and only
then does ``mark_managed_domain_active`` flip the zone active.

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
        reissue_cert,
        request_wildcard_cert_for_zone,
        validate_ns_delegation,
    )


_ACTIVITY_TIMEOUT = timedelta(minutes=5)
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

# 30 s × 144 attempts = 72 h maximum cert-issuance wait.
_POLL_INTERVAL = timedelta(seconds=30)
_POLL_MAX_ATTEMPTS = 144


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="ProvisionManagedDomainWorkflow")
class ProvisionManagedDomainWorkflow:
    def __init__(self) -> None:
        # Mutable state modified by signal handlers — must be instance
        # attributes (not frozen dataclass fields) so signals can write them.
        self._revalidate_requested: bool = False
        self._reissue_requested: bool = False
        # cert_id and zone_id are set after Step 2 and updated by reissue.
        self._cert_id: str = ""
        self._zone_id: str = ""

    @workflow.signal
    async def revalidate(self) -> None:
        """Wake the poll sleep early and check cert status immediately."""
        self._revalidate_requested = True

    @workflow.signal
    async def reissue(self) -> None:
        """Revoke the current cert and request a replacement.

        The poll loop detects this flag, calls ``reissue_cert``, updates
        ``_cert_id``, resets the flag, and resumes polling the new cert.
        """
        self._reissue_requested = True

    @workflow.run
    async def run(
        self,
        input: ProvisionManagedDomainInput,
    ) -> WorkflowResult:
        zone = input.zone
        zone_id = ""
        nameservers: list[str] = []

        # Step 1: provision the hosted zone (platform-managed only).
        if input.is_platform_managed_zone:
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
            zone_id = zone_result.get("zone_id", "") if isinstance(zone_result, dict) else ""
            nameservers = (
                zone_result.get("nameservers", []) if isinstance(zone_result, dict) else []
            )

        self._zone_id = zone_id

        # Step 2: request wildcard cert + write DNS-01 validation records.
        try:
            cert_id: str = await workflow.execute_activity(
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

        self._cert_id = cert_id

        # Step 3: poll loop — 30 s × 144 attempts = 72 h maximum.
        issued = False
        for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
            # Handle reissue signal: revoke old cert and request a new one.
            if self._reissue_requested:
                self._reissue_requested = False
                try:
                    reissue_result = await workflow.execute_activity(
                        reissue_cert,
                        args=[input.cluster_id, zone, self._cert_id, self._zone_id],
                        start_to_close_timeout=_ACTIVITY_TIMEOUT,
                        retry_policy=_ACTIVITY_RETRY,
                    )
                except Exception as exc:  # noqa: BLE001
                    return WorkflowResult(
                        ok=False,
                        message=_truncate(f"reissue_cert failed: {exc}"),
                    )
                self._cert_id = (
                    reissue_result.get("cert_id", self._cert_id)
                    if isinstance(reissue_result, dict)
                    else self._cert_id
                )

            # Poll cert status.
            try:
                poll_result = await workflow.execute_activity(
                    poll_cert_issuance,
                    args=[input.cluster_id, zone, self._cert_id],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                return WorkflowResult(
                    ok=False,
                    message=_truncate(f"poll_cert_issuance failed: {exc}"),
                )

            status = (
                poll_result.get("status", "") if isinstance(poll_result, dict) else ""
            )
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

            if attempt >= _POLL_MAX_ATTEMPTS:
                break

            # Sleep, but allow signals to wake us early.
            self._revalidate_requested = False
            await workflow.wait_condition(
                lambda: self._revalidate_requested or self._reissue_requested,
                timeout=_POLL_INTERVAL,
            )
            # Clear revalidate (reissue is handled at the top of the next
            # iteration so it survives without being cleared here).
            self._revalidate_requested = False

        if not issued:
            return WorkflowResult(
                ok=False,
                message=_truncate(
                    f"cert issuance timed out for zone {zone!r} "
                    f"after {_POLL_MAX_ATTEMPTS} attempts"
                ),
            )

        # Step 4: persist the ManagedDomain row.
        try:
            await workflow.execute_activity(
                register_managed_domain_row,
                args=[input.cluster_id, zone, self._zone_id, self._cert_id],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"register_managed_domain_row failed: {exc}"),
            )

        # Step 5: NS delegation gate. The registrar has to point the zone at
        # the nameservers Step 1 handed back before the zone is fit to serve —
        # activating one that still resolves to the operator's previous DNS
        # provider provisions apps at hostnames nobody can reach.
        try:
            delegation = await workflow.execute_activity(
                validate_ns_delegation,
                args=[input.cluster_id, zone],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(f"validate_ns_delegation failed: {exc}"),
            )
        if not (isinstance(delegation, dict) and delegation.get("passed")):
            reason = delegation.get("reason", "") if isinstance(delegation, dict) else ""
            return WorkflowResult(
                ok=False,
                message=_truncate(
                    f"NS delegation not in place for zone {zone!r}; zone left "
                    f"inactive: {reason}"
                ),
            )

        # Step 6: activate the zone.
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
                "zone_id": self._zone_id,
                "cert_id": self._cert_id,
                "nameservers": nameservers,
            },
        )
