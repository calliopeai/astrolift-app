"""GCP email-observability driver stub (#629, #631, #632, #633, #634).

GCP has no first-party transactional email service; the email path on
GCP installs is a third-party sender (SendGrid, Mailgun, Postmark,
Resend) talking directly to the workload via API key.

Those third-parties expose their own observability APIs but the shape
is not portable across vendors. Until the matrix learns "external
provider with API key" and the platform binds to a specific third-
party, every email-observability operation on GCP raises
:class:`_sdk.UnsupportedOperationError`. The resolver layer renders a
"not supported on this cloud" hint that points operators at the
upstream vendor's console.

Mirrors the lifecycle stub in :mod:`gcp.managed.email_thirdparty` on
purpose.
"""

from __future__ import annotations

from _sdk import (
    AccountSendStatus,
    DnsAuthStatus,
    EmailObservabilityDriver,
    EmailTemplate,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
    TemplateSendStatPoint,
    UnsupportedOperationError,
    driver_op,
)

_UNSUPPORTED_MSG = (
    "GCP has no first-party transactional email observability surface. "
    "Provision a third-party sender (SendGrid / Mailgun / Postmark / "
    "Resend) via the operator portal and view send health in the "
    "third-party's console."
)


class GcpEmailObservabilityDriver(EmailObservabilityDriver):
    """Always raises :class:`UnsupportedOperationError`.

    Kept so the resolver can call into a driver instance polymorphically
    without ``isinstance`` checks; the exception lets the resolver
    render the empty-state with the correct copy.
    """

    @driver_op(driver="email", cloud="gcp")
    def get_send_quota(self) -> SendQuota:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def get_send_statistics(self) -> list[SendStatPoint]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def get_account_send_status(self) -> AccountSendStatus:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def get_identity_verification_details(
        self, identity: str
    ) -> IdentityVerification:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def list_suppression_entries(
        self, *, page_size: int = 100
    ) -> list[SuppressionEntry]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="gcp",
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
        cloud="gcp",
        audit=True,
        sensitive_kind="email.suppression.remove",
    )
    def remove_suppression_entry(self, *, address: str) -> bool:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def list_templates(self) -> list[EmailTemplate]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def get_template(self, *, name: str) -> EmailTemplate:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="gcp",
        audit=True,
        sensitive_kind="email.template.create",
    )
    def create_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="gcp",
        audit=True,
        sensitive_kind="email.template.update",
    )
    def update_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(
        driver="email",
        cloud="gcp",
        audit=True,
        sensitive_kind="email.template.delete",
    )
    def delete_template(self, *, name: str) -> bool:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def get_template_send_statistics(
        self,
        *,
        name: str,
        days: int = 14,
    ) -> list[TemplateSendStatPoint]:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)
