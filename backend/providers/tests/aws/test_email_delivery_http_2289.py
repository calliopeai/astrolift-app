"""Native botocore/SigV4 over owned HTTP, with no account or external mail effects."""

import json
import logging
import re
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import boto3
import pytest

from _sdk.cloud_credentials import CloudCredential
from _sdk.email_delivery import EmailDeliveryUnavailable, EmailTestMessage
from aws.email_delivery import AmazonSESTestDelivery, SESTestDeliveryConfig, _private_io

ACCOUNT = "123456789012"
TOPIC = f"arn:aws:sns:us-west-2:{ACCOUNT}:email-events"
SERVICE = str(uuid4())
MARKER = "PRIVATE_EMAIL_MARKER_2289"


@pytest.fixture
def wire():
    state = {
        "requests": [],
        "account": ACCOUNT,
        "owner": SERVICE,
        "verified": True,
        "suppressed": False,
        "send_status": 200,
        "send_id": "native-message-123",
        "feedback": True,
        "after": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, code, data, *, xml=False):
            self.send_response(code)
            self.send_header("Content-Type", "text/xml" if xml else "application/json")
            self.end_headers()
            self.wfile.write(data.encode() if xml else json.dumps(data).encode())

        def do_GET(self):
            state["requests"].append(("GET", self.path, None))
            if self.path.startswith("/v2/email/identities/"):
                reply = {
                    "VerifiedForSendingStatus": state["verified"],
                    "Tags": [
                        {"Key": "astrolift.io/managed-by", "Value": "platform"},
                        {"Key": "astrolift.io/managed_service_id", "Value": state["owner"]},
                    ],
                }
            elif self.path.startswith("/v2/email/suppression/addresses/"):
                if not state["suppressed"]:
                    self.send_response(404)
                    self.send_header("x-amzn-errortype", "NotFoundException")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": MARKER}).encode())
                    return
                reply = {"SuppressedDestination": {"EmailAddress": "qa@example.test", "Reason": "BOUNCE"}}
            else:
                name = self.path.split("/")[-2]
                assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name), "AWS_CONFIGURATION_SET_NAME_RESTRICTION"
                reply = {
                    "EventDestinations": [
                        {
                            "Name": "platform-sns",
                            "Enabled": state["feedback"],
                            "MatchingEventTypes": ["DELIVERY", "BOUNCE", "COMPLAINT"],
                            "SnsDestination": {"TopicArn": TOPIC},
                        }
                    ]
                }
            if state["after"]:
                state["after"](self.path)
            self._reply(200, reply)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
            if self.path == "/":
                state["requests"].append(("POST", self.path, "GetCallerIdentity"))
                self._reply(
                    200,
                    '<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                    f"<GetCallerIdentityResult><Account>{state['account']}</Account>"
                    f"<Arn>arn:aws:iam::{state['account']}:user/fixture</Arn><UserId>fixture</UserId>"
                    "</GetCallerIdentityResult><ResponseMetadata><RequestId>fixture</RequestId>"
                    "</ResponseMetadata></GetCallerIdentityResponse>",
                    xml=True,
                )
            else:
                payload = json.loads(body)
                assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", payload["ConfigurationSetName"])
                state["requests"].append(("POST", self.path, payload))
                if state["send_status"] != 200:
                    self.send_response(state["send_status"])
                    self.send_header("x-amzn-errortype", "AccessDeniedException")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": MARKER}).encode())
                else:
                    self._reply(200, {"MessageId": state["send_id"]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = boto3.Session(aws_access_key_id="fixture-key", aws_secret_access_key="fixture-secret")

    def build(service, **kwargs):
        return session.client(service, endpoint_url=f"http://127.0.0.1:{server.server_port}", **kwargs)

    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    state["build"] = build
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def config():
    return SESTestDeliveryConfig(
        region="us-west-2",
        credential=CloudCredential(cloud="aws", declared_account=ACCOUNT),
        identity="example.test",
        configuration_set="astrolift-example-test",
        feedback_topic_arns=(TOPIC,),
    )


def message():
    return EmailTestMessage(
        test_id=str(uuid4()),
        managed_service_id=SERVICE,
        sender="noreply@example.test",
        recipient="qa@example.test",
        subject=MARKER,
        text=MARKER,
    )


def sends(wire):
    return [row for row in wire["requests"] if row[1] == "/v2/email/outbound-emails"]


def test_acceptance_is_exact_source_and_tagged_not_delivery(wire):
    msg = message()
    calls = []
    receipt = AmazonSESTestDelivery(config(), build=wire["build"]).send_test(
        message=msg, checkpoint=lambda: calls.append(True)
    )
    assert receipt.message_id == "native-message-123"
    assert receipt.account_id == ACCOUNT
    assert receipt.region == "us-west-2"
    assert receipt.feedback_topic_arns == (TOPIC,)
    assert not hasattr(receipt, "delivered")
    assert not receipt.simulator
    assert len(calls) >= 12
    payload = sends(wire)[0][2]
    assert payload["FromEmailAddress"] == msg.sender
    assert payload["ConfigurationSetName"] == config().configuration_set
    assert payload["EmailTags"] == [
        {"Name": "astrolift_test_id", "Value": msg.test_id},
        {"Name": "astrolift_managed_service_id", "Value": SERVICE},
    ]
    assert len(sends(wire)) == 1


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("account", "999999999999", "AWS_ACCOUNT_MISMATCH"),
        ("owner", str(uuid4()), "IDENTITY_OWNERSHIP_MISMATCH"),
        ("verified", False, "IDENTITY_NOT_VERIFIED"),
        ("suppressed", True, "RECIPIENT_SUPPRESSED"),
    ],
)
def test_live_native_refusals_never_send(wire, key, value, reason):
    wire[key] = value
    with pytest.raises(EmailDeliveryUnavailable, match=f"^{reason}$"):
        AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=lambda: None)
    assert sends(wire) == []


@pytest.mark.parametrize("code", [400, 403, 500, 503])
def test_native_errors_do_not_reveal_body_or_retry_send(wire, code, caplog):
    wire["send_status"] = code
    caplog.set_level(logging.DEBUG)
    with pytest.raises(EmailDeliveryUnavailable, match=r"^TEST_TRANSPORT_UNAVAILABLE$") as exc:
        AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=lambda: None)
    assert len(sends(wire)) == 1
    assert MARKER not in str(exc.value)
    assert MARKER not in caplog.text
    assert "fixture-secret" not in caplog.text


def test_successful_native_debug_logs_do_not_capture_message(wire, caplog):
    caplog.set_level(logging.DEBUG)
    AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=lambda: None)
    assert MARKER not in caplog.text


def test_unconfigured_feedback_is_honest_acceptance(wire):
    wire["feedback"] = False
    receipt = AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=lambda: None)
    assert receipt.feedback_topic_arns == ()
    assert receipt.message_id


def test_simulator_receipt_is_explicit(wire):
    receipt = AmazonSESTestDelivery(config(), build=wire["build"]).send_test(
        message=replace(message(), recipient="success@simulator.amazonses.com"), checkpoint=lambda: None
    )
    assert receipt.simulator


def test_authority_withdrawal_after_identity_read_blocks_send(wire):
    current = [True]

    def after(path):
        if "/identities/" in path:
            current[0] = False

    def checkpoint():
        if not current[0]:
            raise EmailDeliveryUnavailable("AUTHORITY_WITHDRAWN")

    wire["after"] = after
    with pytest.raises(EmailDeliveryUnavailable, match=r"^AUTHORITY_WITHDRAWN$"):
        AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=checkpoint)
    assert sends(wire) == []


@pytest.mark.parametrize(
    "msg",
    [
        replace(message(), sender="foreign@other.test"),
        replace(message(), recipient="x@example.test\r\nBcc: attacker@example.test"),
        replace(message(), subject="x\r\nBcc: attacker@example.test"),
        replace(message(), text="x" * 8193),
        replace(message(), test_id="not-a-guid"),
    ],
)
def test_invalid_input_rejected_before_native_io(wire, msg):
    with pytest.raises(EmailDeliveryUnavailable):
        AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=msg, checkpoint=lambda: None)
    assert wire["requests"] == []


def test_native_missing_message_id_is_ambiguous_not_delivered(wire):
    wire["send_id"] = None
    with pytest.raises(EmailDeliveryUnavailable, match=r"^ACCEPTANCE_UNKNOWN$"):
        AmazonSESTestDelivery(config(), build=wire["build"]).send_test(message=message(), checkpoint=lambda: None)
    assert len(sends(wire)) == 1


def test_private_logging_is_context_local_for_new_loggers(caplog):
    caplog.set_level(logging.DEBUG)
    other_done = threading.Event()

    def ordinary():
        logging.getLogger("botocore.email.ordinary").warning("ordinary request retained")
        other_done.set()

    with _private_io():
        logging.getLogger("botocore.email.created_inside").warning(MARKER)
        thread = threading.Thread(target=ordinary)
        thread.start()
        assert other_done.wait(timeout=2)
        thread.join(timeout=2)
    assert MARKER not in caplog.text
    assert "ordinary request retained" in caplog.text


def test_real_production_client_owner_and_driver_send_use_native_transport(wire, monkeypatch, caplog):
    from urllib.parse import urlsplit

    from botocore.httpsession import URLLib3Session

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:1/unowned")
    original_send = URLLib3Session.send

    def native_send(transport, request):
        parsed = urlsplit(request.url)
        assert parsed.hostname in {"sts.us-west-2.amazonaws.com", "email.us-west-2.amazonaws.com"}
        request.url = wire["origin"] + parsed.path
        return original_send(transport, request)

    monkeypatch.setattr(URLLib3Session, "send", native_send)
    caplog.set_level(logging.DEBUG, logger="botocore")
    receipt = AmazonSESTestDelivery(config()).send_test(message=message(), checkpoint=lambda: None)
    assert receipt.message_id == "native-message-123"
    assert receipt.feedback_topic_arns == (TOPIC,) and len(sends(wire)) == 1
    assert MARKER not in caplog.text


def test_configuration_set_names_fit_native_rules_without_secret_ref_changes():
    from aws.managed.email_ses import _safe, configuration_set_name

    dotted = configuration_set_name("example.test")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dotted)
    assert dotted != configuration_set_name("example-test")
    assert configuration_set_name("legacy-valid") == "astrolift-legacy-valid"
    assert _safe("example.test") == "example.test"
    one = "a" * 60 + ".one.test"
    two = "a" * 60 + ".two.test"
    assert len(configuration_set_name(one)) <= 64
    assert configuration_set_name(one) != configuration_set_name(two)
    assert configuration_set_name("example.test", prefix="custom") != dotted
