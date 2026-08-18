"""GCP email-observability driver stub (#629, #631, #632, #633, #634).

GCP has no first-party transactional email service, so the email path
on GCP installs is the vendor-neutral SMTP relay in
:mod:`gcp.managed.email_smtp`.

Every operation here stays :class:`_sdk.UnsupportedOperationError`, and
that is a statement about SMTP rather than a gap in the driver. SMTP is
a submission protocol: it has no quota endpoint, no suppression list,
no per-message event stream and no DKIM status to report. Those live in
whichever relay the operator points the install at, behind that
vendor's own API, and no two of them agree on a shape. The resolver
layer renders a "not supported on this cloud" hint that points the
operator at the relay's console, which is where the answer actually is.

A first-party vendor driver, should one ever be wanted, would arrive as
an additional variant with its own observability implementation; it
would not change this one.
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
    "GCP has no first-party transactional email observability surface, "
    "and SMTP defines none: the email/smtp variant submits mail to the "
    "operator's relay. View quota, suppressions and DKIM status in that "
    "relay's own console."
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
    def get_identity_verification_details(self, identity: str) -> IdentityVerification:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        raise UnsupportedOperationError(_UNSUPPORTED_MSG)

    @driver_op(driver="email", cloud="gcp")
    def list_suppression_entries(self, *, page_size: int = 100) -> list[SuppressionEntry]:
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
