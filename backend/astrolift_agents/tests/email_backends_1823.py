"""Controlled mail transports for persisted gate delivery tests; no network I/O."""

from django.core.mail.backends.base import BaseEmailBackend


class RefusedBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError("SES AccessDenied")


class NoDeliveryBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        return 0
