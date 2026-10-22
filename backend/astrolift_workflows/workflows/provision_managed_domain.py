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

A run that ends without activating the zone records where and why on the
row (``mark_managed_domain_provision_failed``, #2389), so the zone reads
"stalled at <step>" instead of sitting at the last step it reached.

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
        delegate_parent_zone,
        mark_managed_domain_active,
        mark_managed_domain_provision_failed,
        poll_cert_issuance,
        provision_dns_zone,
        register_managed_domain_row,
        reissue_cert,
        request_wildcard_cert_for_zone,
        validate_ns_delegation,
    )
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT


_ACTIVITY_TIMEOUT = timedelta(minutes=5)
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

# 30 s × 144 attempts = 72 h maximum cert-issuance wait.
_POLL_INTERVAL = timedelta(seconds=30)
_POLL_MAX_ATTEMPTS = 144
# NS delegation is the operator's registrar change, and it propagates on the
# registrar's schedule: checked every 5 minutes for up to 48 hours (#2389).
# A ``revalidate`` signal checks at once.
_DELEGATION_POLL_INTERVAL = timedelta(minutes=5)
_DELEGATION_POLL_MAX_ATTEMPTS = 576


# Records failures on the row and keeps polling when a poll sleep times out
# (#2389). Before it, an unsignalled sleep raised TimeoutError and failed the
# run on the first tick, leaving the row at its last step.
_TERMINAL_STATE_PATCH = "domain-terminal-state-2389"


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _error_kind(exc: BaseException) -> str:
    """The failing activity's exception class: credential-free, unlike its text."""
    cause = getattr(exc, "cause", None)
    return str(getattr(cause, "type", "") or type(cause or exc).__name__)


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

    async def _sleep(self, wake, timeout: timedelta) -> None:
        """Sleep until ``wake()`` or ``timeout``, whichever comes first."""
        try:
            await workflow.wait_condition(wake, timeout=timeout)
        except TimeoutError:
            if not workflow.patched(_TERMINAL_STATE_PATCH):
                raise

    async def _fail(
        self, input: ProvisionManagedDomainInput, step: str, reason: str, message: str
    ) -> WorkflowResult:
        """End the run, recording ``step`` and ``reason`` on the domain row."""
        if workflow.patched(_TERMINAL_STATE_PATCH):
            try:
                await workflow.execute_activity(
                    mark_managed_domain_provision_failed,
                    args=[input.cluster_id, input.zone, step, reason],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception:  # noqa: BLE001 - the run's own result still reports the failure
                pass
        return WorkflowResult(ok=False, message=_truncate(message))

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
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                return await self._fail(
                    input,
                    "provision_dns_zone",
                    f"zone creation failed ({_error_kind(exc)})",
                    f"provision_dns_zone failed: {exc}",
                )
            zone_id = zone_result.get("zone_id", "") if isinstance(zone_result, dict) else ""
            nameservers = zone_result.get("nameservers", []) if isinstance(zone_result, dict) else []

        self._zone_id = zone_id

        # Step 1b: write the delegating NS set into the parent zone when the
        # domain is bound to a provider grant (#1787). Best effort: an
        # unbound, conflicting or failed write leaves the manual path, and
        # the delegation gate below still decides activation.
        if nameservers and workflow.patched("cloudflare-delegation-1787"):
            try:
                await workflow.execute_activity(
                    delegate_parent_zone,
                    args=[input.cluster_id, zone, nameservers],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception:  # noqa: BLE001 — recorded on the domain; never blocks provisioning
                pass

        # Step 2: request wildcard cert + write DNS-01 validation records.
        try:
            cert_id: str = await workflow.execute_activity(
                request_wildcard_cert_for_zone,
                args=[input.cluster_id, zone, zone_id],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(
                input,
                "configure_cert_policy",
                f"certificate request failed ({_error_kind(exc)})",
                f"request_wildcard_cert_for_zone failed: {exc}",
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
                        heartbeat_timeout=HEARTBEAT_TIMEOUT,
                        retry_policy=_ACTIVITY_RETRY,
                    )
                except Exception as exc:  # noqa: BLE001
                    return await self._fail(
                        input,
                        "configure_cert_policy",
                        f"certificate reissue failed ({_error_kind(exc)})",
                        f"reissue_cert failed: {exc}",
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
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                return await self._fail(
                    input,
                    "configure_cert_policy",
                    f"certificate status check failed ({_error_kind(exc)})",
                    f"poll_cert_issuance failed: {exc}",
                )

            status = poll_result.get("status", "") if isinstance(poll_result, dict) else ""
            if status == "issued":
                issued = True
                break
            if status == "failed":
                return await self._fail(
                    input,
                    "configure_cert_policy",
                    "the certificate authority reported the certificate failed",
                    f"cert issuance failed for zone {zone!r}: " + str(poll_result.get("message", "")),
                )

            if attempt >= _POLL_MAX_ATTEMPTS:
                break

            # Sleep, but allow signals to wake us early.
            self._revalidate_requested = False
            await self._sleep(
                lambda: self._revalidate_requested or self._reissue_requested,
                _POLL_INTERVAL,
            )
            # Clear revalidate (reissue is handled at the top of the next
            # iteration so it survives without being cleared here).
            self._revalidate_requested = False

        if not issued:
            return await self._fail(
                input,
                "configure_cert_policy",
                "certificate not issued within 72 hours; check its DNS validation records",
                f"cert issuance timed out for zone {zone!r} after {_POLL_MAX_ATTEMPTS} attempts",
            )

        # Step 4: persist the ManagedDomain row.
        try:
            await workflow.execute_activity(
                register_managed_domain_row,
                args=[input.cluster_id, zone, self._zone_id, self._cert_id],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(
                input,
                "register_managed_domain",
                f"recording the zone failed ({_error_kind(exc)})",
                f"register_managed_domain_row failed: {exc}",
            )

        # Step 5: NS delegation gate. The registrar has to point the zone at
        # the nameservers Step 1 handed back before the zone is fit to serve —
        # activating one that still resolves to the operator's previous DNS
        # provider provisions apps at hostnames nobody can reach.
        # Checked until it holds, not once (#2389): a single check failed
        # every zone whose registrar change had not propagated yet, and the
        # run ended with the zone stuck at validate_ns_delegation.
        attempts = _DELEGATION_POLL_MAX_ATTEMPTS if workflow.patched("ns-delegation-poll-2389") else 1
        delegation = None
        for attempt in range(1, attempts + 1):
            try:
                delegation = await workflow.execute_activity(
                    validate_ns_delegation,
                    args=[input.cluster_id, zone],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            except Exception as exc:  # noqa: BLE001
                return await self._fail(
                    input,
                    "validate_ns_delegation",
                    f"delegation check failed ({_error_kind(exc)})",
                    f"validate_ns_delegation failed: {exc}",
                )
            if (isinstance(delegation, dict) and delegation.get("passed")) or attempt >= attempts:
                break
            self._revalidate_requested = False
            await self._sleep(lambda: self._revalidate_requested, _DELEGATION_POLL_INTERVAL)
            self._revalidate_requested = False
        if not (isinstance(delegation, dict) and delegation.get("passed")):
            reason = delegation.get("reason", "") if isinstance(delegation, dict) else ""
            return await self._fail(
                input,
                "validate_ns_delegation",
                f"NS delegation not in place: {reason}",
                f"NS delegation not in place for zone {zone!r}; zone left inactive: {reason}",
            )

        # Step 6: activate the zone.
        try:
            await workflow.execute_activity(
                mark_managed_domain_active,
                args=[input.cluster_id, zone],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._fail(
                input,
                "mark_active",
                f"activation failed ({_error_kind(exc)})",
                f"mark_managed_domain_active failed: {exc}",
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
