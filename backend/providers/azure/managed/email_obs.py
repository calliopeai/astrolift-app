"""Azure ACS email-observability driver stub (#629, #631, #632, #633, #634).

Azure Communication Services (Email) exposes a different operations
surface than SES. Per-message tracing lives in ACS Diagnostic Settings
rather than a pull API; suppression-style "stop sending to address X"
isn't a first-class concept (ACS uses recipient ``UnsubscribeOptions``
+ tenant-level engagement tracking instead); the send-rate quota
limits are documented as tenant-level static values rather than
discoverable via API.

Until the ACS surface grows comparable primitives — or the platform
wires the ACS-style tracking model into a separate driver protocol —
every email-observability operation raises
:class:`_sdk.UnsupportedOperationError`. The resolver renders a "not
supported on this cloud" hint that links operators at the Azure Portal
ACS diagnostics page.

Mirrors the lifecycle implementation in :mod:`azure.managed.email_acs`
on purpose — observability lifts cleanly when ACS catches up.
"""

from __future__ import annotations

from _sdk import (
    AccountSendStatus,
    DnsAuthStatus,
    EmailObservabilityDriver,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
    UnsupportedOperationError,
    driver_op,
)

_UNSUPPORTED_MSG = (
    "Azure Communication Services (Email) doesn't expose a comparable "
    "observability surface — use ACS Diagnostic Settings + Log Analytics "
    "queries for per-message tracing, and the Azure Portal ACS engagement "
    "tracking surface for send-rate health."
)


class AzureAcsEmailObservabilityDriver(EmailObservabilityDriver):
    """Always raises :class:`UnsupportedOperationError`."""

    @driver_op(driver="email", cloud="azure")
    def get_send_quota(self) -> SendQuota:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="azure")
    def get_send_statistics(self) -> list[SendStatPoint]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="azure")
    def get_account_send_status(self) -> AccountSendStatus:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="azure")
    def get_identity_verification_details(
        self, identity: str
    ) -> IdentityVerification:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="azure")
    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="azure")
    def list_suppression_entries(
        self, *, page_size: int = 100
    ) -> list[SuppressionEntry]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="azure",
        audit=True,
        sensitive_kind="email.suppression.add",
        redact_args=("note",),
    )
    def add_suppression_entry(
        self,
        *,
        address: str,
        reason: SuppressionReason,
        note: str = "",
    ) -> SuppressionEntry:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="azure",
        audit=True,
        sensitive_kind="email.suppression.remove",
    )
    def remove_suppression_entry(self, *, address: str) -> bool:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)
