"""Bounded SES v2 diagnostics through one registered account and owned identity."""

import contextvars
import importlib
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from _sdk.cloud_credentials import CloudCredential
from _sdk.email_delivery import EmailAcceptance, EmailDeliveryUnavailable, EmailTestMessage
from aws.managed._base import live_ownership_refusal
from aws.session import aws_client

_PRIVATE = contextvars.ContextVar("email_delivery_private_io", default=False)
_LOCK = threading.Lock()
_MAILBOX = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
_IDENTITY = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_TOPIC = re.compile(r"arn:(aws|aws-cn|aws-us-gov):sns:([a-z0-9-]+):(\d{12}):[A-Za-z0-9_-]{1,256}")
_SIMULATORS = frozenset({"success", "bounce", "complaint", "ooto", "suppressionlist"})


class _PrivateLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _PRIVATE.get()


_FILTER = _PrivateLogFilter()
_FACTORY_INSTALLED = False


def _install_private_logging() -> None:
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if _PRIVATE.get() and record.name.startswith(("boto3", "botocore", "urllib3")):
            record.msg = "Private email transport operation."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    logging.setLogRecordFactory(record_factory)
    _FACTORY_INSTALLED = True


@contextmanager
def _private_io() -> Iterator[None]:
    with _LOCK:
        _install_private_logging()
        for name, logger in tuple(logging.root.manager.loggerDict.items()):
            if (
                name.startswith(("boto3", "botocore", "urllib3"))
                and isinstance(logger, logging.Logger)
                and _FILTER not in logger.filters
            ):
                logger.addFilter(_FILTER)
    marker = _PRIVATE.set(True)
    try:
        try:
            suppress = importlib.import_module("opentelemetry.instrumentation.utils").suppress_instrumentation
        except ImportError:
            yield
        else:
            with suppress():
                yield
    finally:
        _PRIVATE.reset(marker)


@dataclass(frozen=True, slots=True)
class SESTestDeliveryConfig:
    region: str
    credential: CloudCredential
    identity: str
    configuration_set: str
    feedback_topic_arns: tuple[str, ...] = ()


def _mailbox(value: str) -> None:
    if not isinstance(value, str) or len(value) > 254 or not _MAILBOX.fullmatch(value):
        raise EmailDeliveryUnavailable("INVALID_EMAIL_ADDRESS")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or ".." in local or ".." in domain or "." not in domain:
        raise EmailDeliveryUnavailable("INVALID_EMAIL_ADDRESS")


def _validate(config: SESTestDeliveryConfig, message: EmailTestMessage) -> None:
    for value in (message.test_id, message.managed_service_id):
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise EmailDeliveryUnavailable("INVALID_TEST_ID") from None
    _mailbox(message.sender)
    _mailbox(message.recipient)
    identity = config.identity
    if not isinstance(identity, str) or len(identity) > 254:
        raise EmailDeliveryUnavailable("INVALID_SENDING_IDENTITY")
    if "@" in identity:
        _mailbox(identity)
        matches = message.sender == identity
    else:
        if not _IDENTITY.fullmatch(identity) or ".." in identity or "." not in identity:
            raise EmailDeliveryUnavailable("INVALID_SENDING_IDENTITY")
        matches = message.sender.rsplit("@", 1)[1].lower() == identity
    if not matches:
        raise EmailDeliveryUnavailable("SENDER_OUTSIDE_IDENTITY")
    if (
        not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", config.region)
        or not re.fullmatch(r"\d{12}", config.credential.declared_account)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config.configuration_set)
        or len(config.feedback_topic_arns) > 8
    ):
        raise EmailDeliveryUnavailable("EXACT_SOURCE_REQUIRED")
    for arn in config.feedback_topic_arns:
        match = _TOPIC.fullmatch(arn)
        if match is None or match[2] != config.region or match[3] != config.credential.declared_account:
            raise EmailDeliveryUnavailable("FEEDBACK_TOPIC_SOURCE_MISMATCH")
    if not isinstance(message.subject, str) or not isinstance(message.text, str):
        raise EmailDeliveryUnavailable("INVALID_TEST_CONTENT")
    try:
        subject_bytes = len(message.subject.encode("utf-8"))
        text_bytes = len(message.text.encode("utf-8"))
    except UnicodeEncodeError:
        raise EmailDeliveryUnavailable("INVALID_TEST_CONTENT") from None
    if (
        not 1 <= subject_bytes <= 200
        or any(ord(c) < 32 or ord(c) == 127 for c in message.subject)
        or not 1 <= text_bytes <= 8192
    ):
        raise EmailDeliveryUnavailable("INVALID_TEST_CONTENT")


class AmazonSESTestDelivery:
    def __init__(self, config: SESTestDeliveryConfig, *, build: Callable[..., Any] | None = None) -> None:
        self.config = config
        self._build = build

    def send_test(self, *, message: EmailTestMessage, checkpoint: Callable[[], None]) -> EmailAcceptance:
        _validate(self.config, message)
        from botocore.config import Config
        from botocore.exceptions import ClientError

        deadline = time.monotonic() + 30

        def gate(**_kwargs: Any) -> None:
            if time.monotonic() >= deadline:
                raise EmailDeliveryUnavailable("TEST_DEADLINE_EXCEEDED")
            checkpoint()

        clients = []
        owner = None
        try:
            gate()
            with _private_io():
                if self._build is None:
                    from aws.email_delivery_clients import EmailNativeClients

                    owner = EmailNativeClients(self.config.credential, self.config.region, gate)
                    sts, ses = owner.client("sts"), owner.client("sesv2")
                else:
                    # The injected native constructor is an owned-transport test seam.
                    options = dict(
                        region=self.config.region,
                        credential=self.config.credential,
                        build=self._build,
                        config=Config(
                            connect_timeout=3,
                            read_timeout=5,
                            retries={"total_max_attempts": 1},
                            ignore_configured_endpoint_urls=True,
                        ),
                    )
                    if not self.config.credential.is_ambient:
                        gate()
                        ambient_sts = aws_client(
                            "sts", region=self.config.region, build=self._build, config=options["config"]
                        )
                        ambient_sts.meta.events.register("before-send.*.*", gate)
                        clients.append(ambient_sts)
                        options["sts"] = ambient_sts
                    service_clients = []
                    for service in ("sts", "sesv2"):
                        gate()
                        client = aws_client(service, **options)
                        client.meta.events.register("before-send.*.*", gate)
                        clients.append(client)
                        service_clients.append(client)
                    sts, ses = service_clients
                account = sts.get_caller_identity().get("Account")
                gate()
                if account != self.config.credential.declared_account:
                    raise EmailDeliveryUnavailable("AWS_ACCOUNT_MISMATCH")
                identity = ses.get_email_identity(EmailIdentity=self.config.identity)
                gate()
                if live_ownership_refusal(
                    identity.get("Tags"), managed_service_id=message.managed_service_id, resource="email identity"
                ):
                    raise EmailDeliveryUnavailable("IDENTITY_OWNERSHIP_MISMATCH")
                if identity.get("VerifiedForSendingStatus") is not True:
                    raise EmailDeliveryUnavailable("IDENTITY_NOT_VERIFIED")
                try:
                    ses.get_suppressed_destination(EmailAddress=message.recipient)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "NotFoundException":
                        raise EmailDeliveryUnavailable("SUPPRESSION_CHECK_UNAVAILABLE") from None
                else:
                    raise EmailDeliveryUnavailable("RECIPIENT_SUPPRESSED")
                gate()
                response = ses.get_configuration_set_event_destinations(
                    ConfigurationSetName=self.config.configuration_set
                )
                gate()
                destinations = response.get("EventDestinations", [])
                if not isinstance(destinations, list) or len(destinations) > 100:
                    raise EmailDeliveryUnavailable("FEEDBACK_CONFIGURATION_UNAVAILABLE")
                topics = tuple(
                    sorted(
                        {
                            row["SnsDestination"]["TopicArn"]
                            for row in destinations
                            if isinstance(row, dict)
                            and row.get("Enabled") is True
                            and {"DELIVERY", "BOUNCE", "COMPLAINT"}.issubset(row.get("MatchingEventTypes", []))
                            and isinstance(row.get("SnsDestination"), dict)
                            and row["SnsDestination"].get("TopicArn") in self.config.feedback_topic_arns
                        }
                    )
                )
                gate()
                response = ses.send_email(
                    FromEmailAddress=message.sender,
                    Destination={"ToAddresses": [message.recipient]},
                    Content={
                        "Simple": {
                            "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                            "Body": {"Text": {"Data": message.text, "Charset": "UTF-8"}},
                        }
                    },
                    ConfigurationSetName=self.config.configuration_set,
                    EmailTags=[
                        {"Name": "astrolift_test_id", "Value": message.test_id},
                        {"Name": "astrolift_managed_service_id", "Value": message.managed_service_id},
                    ],
                )
            identifier = response.get("MessageId")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", identifier):
                raise EmailDeliveryUnavailable("ACCEPTANCE_UNKNOWN")
            local, domain = message.recipient.lower().rsplit("@", 1)
            return EmailAcceptance(
                transport="aws_ses",
                message_id=identifier,
                account_id=account,
                region=self.config.region,
                identity=self.config.identity,
                configuration_set=self.config.configuration_set,
                feedback_topic_arns=topics,
                simulator=domain == "simulator.amazonses.com" and local in _SIMULATORS,
            )
        except EmailDeliveryUnavailable:
            raise
        except Exception:
            raise EmailDeliveryUnavailable("TEST_TRANSPORT_UNAVAILABLE") from None
        finally:
            with _private_io():
                if owner is not None:
                    owner.close()
                for client in clients:
                    client.close()
