"""Controlled mail transports for persisted gate delivery tests; no network I/O."""

from botocore.stub import ANY, Stubber
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django_ses import SESBackend

# The quota of an SES account with production access.
SEND_QUOTA = {"Max24HourSend": 50000.0, "MaxSendRate": 14.0, "SentLast24Hours": 0.0}


class RefusedBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError("SES AccessDenied")


class NoDeliveryBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        return 0


class _StubbedSESBackend(SESBackend):
    """The production django-ses backend, its SES client answering from a stub."""

    def open(self):
        if self.connection:
            return False
        super().open()
        stub = Stubber(self.connection)
        self.answer(stub)
        stub.activate()

    def answer(self, stub: Stubber) -> None:
        raise NotImplementedError


class SESAcceptedBackend(_StubbedSESBackend):
    """SES accepts the message, sent from the configured sender."""

    def answer(self, stub):
        stub.add_response("get_send_quota", SEND_QUOTA)
        stub.add_response(
            "send_raw_email",
            {"MessageId": "gate-review-message", "ResponseMetadata": {"RequestId": "gate-review-request"}},
            {"Source": settings.FROM_EMAIL, "Destinations": ANY, "RawMessage": ANY},
        )


class SESQuotaDeniedBackend(_StubbedSESBackend):
    """The Conflict failure: a task role with no ``ses:GetSendQuota`` grant."""

    def answer(self, stub):
        stub.add_client_error(
            "get_send_quota",
            service_error_code="AccessDenied",
            service_message=(
                "User: arn:aws:sts::123456789012:assumed-role/conflict-astrolift-task/worker "
                "is not authorized to perform: ses:GetSendQuota"
            ),
            http_status_code=403,
        )


class SESUnverifiedSenderBackend(_StubbedSESBackend):
    """SES refuses a sender no verified identity covers."""

    def answer(self, stub):
        stub.add_response("get_send_quota", SEND_QUOTA)
        stub.add_client_error(
            "send_raw_email",
            service_error_code="MessageRejected",
            service_message=(
                "Email address is not verified. The following identities failed the check "
                "in region US-WEST-2: no-reply@example.com"
            ),
        )
