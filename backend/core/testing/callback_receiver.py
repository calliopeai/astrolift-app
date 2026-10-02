"""A real private-interface HTTPS receiver for completion callback tests."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _private_interface() -> str:
    candidates = []
    try:
        candidates.extend(
            item[4][0]
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
        )
    except OSError:
        pass
    # Route discovery sends no packet. Prefer a LAN route over a default VPN
    # route, which may advertise a private address that cannot receive locally.
    for destination in ("192.168.1.1", "192.168.0.1", "192.0.2.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((destination, 9))
                candidates.append(route.getsockname()[0])
        except OSError:
            continue
    for host in dict.fromkeys(candidates):
        address = ipaddress.ip_address(host)
        if not address.is_private or address.is_loopback or address.is_link_local:
            continue
        try:
            # Binding alone is insufficient for VPN interfaces. Prove this
            # private address accepts a local TCP connection before TLS setup.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((host, 0))
                listener.listen(1)
                listener.settimeout(5)
                with socket.create_connection(listener.getsockname(), timeout=5):
                    connection, _ = listener.accept()
                    connection.close()
            return host
        except OSError:
            continue
    raise RuntimeError("Callback integration tests require a reachable private network interface")


@pytest.fixture
def callback_receiver(tmp_path, monkeypatch):
    host = _private_interface()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable callback receiver")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "callback-ca.pem"
    key_path = tmp_path / "callback-server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    receiver = SimpleNamespace(
        host=host,
        url="",
        responses=[200],
        requests=[],
        response_body=b"",
        delay_seconds=0.0,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            receiver.requests.append((dict(self.headers), body))
            time.sleep(receiver.delay_seconds)
            response = receiver.responses.pop(0) if len(receiver.responses) > 1 else receiver.responses[0]
            if response is None:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response)
            self.end_headers()
            self.wfile.write(receiver.response_body)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # A receiver outage is an intentional test input, not a body-bearing
            # traceback on stderr. Assertions inspect the durable delivery row.
            pass

    server = Server((host, 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    receiver.url = f"https://{host}:{server.server_port}/completion"
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield receiver
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
