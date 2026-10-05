"""Exact-source test delivery; transport acceptance never implies inbox arrival."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# SES message-event correlation has a restricted alphabet. It is distinct from
# resource ownership/cost tags, whose canonical AWS key includes a dot and slash.
EMAIL_DELIVERY_SERVICE_TAG = "astrolift_delivery_service"


class EmailDeliveryUnavailable(ValueError):
    """Safe fixed reason; native errors and message contents stay private."""


@dataclass(frozen=True, slots=True)
class EmailTestMessage:
    test_id: str
    managed_service_id: str
    sender: str
    recipient: str
    subject: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class EmailAcceptance:
    transport: str
    message_id: str
    account_id: str
    region: str
    identity: str
    configuration_set: str
    feedback_topic_arns: tuple[str, ...]
    simulator: bool


class EmailTestDeliveryDriver(Protocol):
    def send_test(self, *, message: EmailTestMessage, checkpoint: Callable[[], None]) -> EmailAcceptance:
        """Send once through the declared source, preserving account suppressions.

        Call checkpoint before each native request. Never retry an ambiguous
        send automatically: a lost response may have followed acceptance.
        Implementations expose only safe reason codes, not native exceptions.
        """
        ...
