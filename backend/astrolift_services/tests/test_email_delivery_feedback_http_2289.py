"""Actual signed SNS receiver, native TLS certificate loading and safe mail history."""

import base64
import ipaddress
import json
import ssl
import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from astrolift_services import views
from astrolift_services.models import EmailDeliveryObservation, EmailDeliveryTest, EmailEvent
from astrolift_services.tests.test_email_delivery_http_2289 import MARKER, TOPIC, event, send
from astrolift_services.tests.test_email_delivery_http_2289 import wire as wire_fixture
from astrolift_services.tests.test_email_delivery_http_2289 import world as world_fixture

pytestmark = pytest.mark.django_db
CERT_URL = "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-fixture.pem"


@pytest.fixture
def wire():
    yield from wire_fixture.__wrapped__()


@pytest.fixture
def world(client, wire, monkeypatch, settings):
    return world_fixture.__wrapped__(client, wire, monkeypatch, settings)


@pytest.fixture
def certificate_transport(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Owned mail fixture")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "certificate.pem", tmp_path / "key.pem"
    tmp_path.chmod(0o700)
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    key_path.chmod(0o600)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            assert self.path == "/certificate.pem"
            self.send_response(200)
            self.send_header("Content-Length", str(len(pem)))
            self.end_headers()
            self.wfile.write(pem)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    client_context = ssl.create_default_context(cadata=pem.decode())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"https://127.0.0.1:{server.server_port}/certificate.pem"
    original = urllib.request.urlopen

    def native_open(url, **kwargs):
        assert url == CERT_URL
        return original(origin, context=client_context, **kwargs)

    views._load_signing_cert_public_key.cache_clear()
    monkeypatch.setattr(urllib.request, "urlopen", native_open)
    try:
        yield key, requests
    finally:
        views._load_signing_cert_public_key.cache_clear()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def signed(key, message, *, topic=TOPIC):
    body = {
        "Type": "Notification",
        "MessageId": str(uuid4()),
        "Timestamp": datetime.now(UTC).isoformat(),
        "TopicArn": topic,
        "Message": json.dumps(message),
        "SigningCertURL": CERT_URL,
        "SignatureVersion": "2",
    }
    canonical = "".join(
        f"{field}\n{body[field]}\n" for field in ("Message", "MessageId", "Timestamp", "TopicArn", "Type")
    ).encode()
    body["Signature"] = base64.b64encode(key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())).decode()
    return body


@pytest.mark.parametrize("kind", ["Delivery", "Bounce", "Complaint", "DeliveryDelay", "Reject"])
def test_actual_signed_feedback_updates_safe_history_once(world, certificate_transport, caplog, kind):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    _, message = event(row, kind=kind)
    key, requests = certificate_transport
    envelope = signed(key, message)
    for _ in range(2):
        response = world.client.post(
            "/app/webhooks/ses-events/", data=json.dumps(envelope), content_type="application/json"
        )
        assert response.status_code == 200
    assert requests == ["/certificate.pem"]
    assert EmailDeliveryObservation.objects.count() == 1 and EmailEvent.objects.count() == 0
    assert MARKER not in str(list(EmailDeliveryObservation.objects.values()))
    assert MARKER not in caplog.text


def test_tampered_signed_feedback_refuses_before_history(world, certificate_transport):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    _, message = event(row)
    key, _requests = certificate_transport
    envelope = signed(key, message)
    envelope["Message"] = envelope["Message"].replace("Delivery", "Bounce")
    response = world.client.post(
        "/app/webhooks/ses-events/", data=json.dumps(envelope), content_type="application/json"
    )
    assert response.status_code == 403 and EmailDeliveryObservation.objects.count() == 0
    row.refresh_from_db()
    assert row.status == "accepted"


def test_valid_signature_from_another_topic_cannot_update_test(world, certificate_transport):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    _, message = event(row)
    key, _requests = certificate_transport
    envelope = signed(key, message, topic="arn:aws:sns:us-west-2:123456789012:foreign-topic")
    response = world.client.post(
        "/app/webhooks/ses-events/", data=json.dumps(envelope), content_type="application/json"
    )
    assert response.status_code == 200 and EmailDeliveryObservation.objects.count() == 0
    assert EmailEvent.objects.count() == 0
