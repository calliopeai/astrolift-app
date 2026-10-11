"""Actual owned UDP/TLS transports, parser correlation and bounded probes."""

import datetime as dt
import ipaddress
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from astrolift_clusters import domain_diagnostic_network as net


@pytest.fixture
def dns_wire(monkeypatch):
    state = {"answers": [], "flags": 0x8180, "queries": []}
    actual = socket.socket
    listener = actual(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                body, peer = listener.recvfrom(8192)
            except TimeoutError:
                continue
            name, offset = net._decode_name(body, 12)
            kind = struct.unpack(">H", body[offset : offset + 2])[0]
            state["queries"].append((name, kind))
            records = []
            for owner, record_type, value in state["answers"]:
                records.append(
                    net._encode_name(owner) + struct.pack(">HHIH", record_type, 1, 300, len(value)) + value
                )
            header = struct.pack(
                ">HHHHHH", struct.unpack(">H", body[:2])[0], state["flags"], 1, len(records), 0, 0
            )
            listener.sendto(header + body[12:] + b"".join(records), peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    class OwnedSocket(actual):
        def connect(self, address):
            if address in (("1.1.1.1", 53), ("8.8.8.8", 53)):
                address = listener.getsockname()
            return super().connect(address)

    monkeypatch.setattr(socket, "socket", OwnedSocket)
    try:
        yield state
    finally:
        stop.set()
        thread.join(1)
        listener.close()


@pytest.mark.parametrize(
    "kind,rdata,expected",
    [
        ("A", ipaddress.ip_address("8.8.4.4").packed, "8.8.4.4"),
        ("AAAA", ipaddress.ip_address("2001:4860:4860::8888").packed, "2001:4860:4860::8888"),
        ("NS", net._encode_name("ns.provider.test"), "ns.provider.test"),
        ("CNAME", net._encode_name("target.provider.test"), "target.provider.test"),
        ("MX", struct.pack(">H", 10) + net._encode_name("mail.provider.test"), "10 mail.provider.test"),
        ("TXT", b"\x03one\x03two", "onetwo"),
        (
            "SOA",
            net._encode_name("ns.provider.test")
            + net._encode_name("hostmaster.example.test")
            + struct.pack(">IIIII", 1, 2, 3, 4, 5),
            "ns.provider.test hostmaster.example.test 1 2 3 4 5",
        ),
        (
            "SRV",
            struct.pack(">HHH", 1, 2, 443) + net._encode_name("target.example.test"),
            "1 2 443 target.example.test",
        ),
        ("CAA", b"\x00\x05issueprovider.test", '0 issue "provider.test"'),
    ],
)
def test_real_dns_wire_record_types(dns_wire, kind, rdata, expected):
    dns_wire["answers"] = [("example.test", net.RECORD_TYPES[kind], rdata)]
    assert net.dns_lookup("example.test", kind, "1.1.1.1", current=lambda: None) == ([expected], "DNS_ANSWER")


def test_foreign_answer_is_not_query_answer_and_cname_chain_is_explicit(dns_wire):
    dns_wire["answers"] = [("foreign.test", 1, ipaddress.ip_address("8.8.4.4").packed)]
    assert net.dns_lookup("example.test", "A", "1.1.1.1", current=lambda: None) == ([], "DNS_NO_DATA")
    dns_wire["answers"] = [
        ("example.test", 5, net._encode_name("target.provider.test")),
        ("target.provider.test", 1, ipaddress.ip_address("8.8.4.4").packed),
        ("foreign.test", 1, ipaddress.ip_address("1.0.0.1").packed),
    ]
    assert net.dns_lookup("example.test", "A", "1.1.1.1", current=lambda: None)[0] == ["8.8.4.4"]


def test_nxdomain_truncated_and_malformed_are_distinct(dns_wire):
    dns_wire["flags"] = 0x8183
    assert net.dns_lookup("example.test", "A", "1.1.1.1", current=lambda: None) == ([], "DNS_NXDOMAIN")
    dns_wire["flags"] = 0x8380
    with pytest.raises(net.DiagnosticTransportError):
        net.dns_lookup("example.test", "A", "1.1.1.1", current=lambda: None)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1", "224.0.0.1"]
)
def test_nonpublic_address_refused_before_socket(address, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_kw: pytest.fail("network must not run"))
    with pytest.raises(ValueError):
        net.https_probe("example.test", address, current=lambda: None)


@contextmanager
def tls_wire(tmp_path, monkeypatch, *, trickle=False, certificate_name="example.test"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.set_servername_callback(lambda _sock, name, _ctx: state["sni"].append(name))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.1)
    stopped = threading.Event()
    state = {"requests": [], "sni": []}

    def serve():
        while not stopped.is_set():
            try:
                raw, _peer = listener.accept()
            except TimeoutError:
                continue
            try:
                with context.wrap_socket(raw, server_side=True) as secure:
                    secure.settimeout(2)
                    state["requests"].append(secure.recv(4096).decode())
                    if trickle:
                        for byte in b"HTTP/1.1 302 " + b"x" * 180 + b"\r\n":
                            if stopped.wait(0.06):
                                break
                            secure.sendall(bytes([byte]))
                    else:
                        secure.sendall(
                            b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/private-marker\r\n\r\nprivate-body-marker"
                        )
            except (OSError, ssl.SSLError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    actual_connection, actual_context = socket.create_connection, ssl.create_default_context

    def pinned(address, **kwargs):
        assert address == ("8.8.4.4", 443)
        return actual_connection(listener.getsockname(), **kwargs)

    monkeypatch.setattr(socket, "create_connection", pinned)
    monkeypatch.setattr(ssl, "create_default_context", lambda: actual_context(cafile=str(cert_path)))
    try:
        yield state
    finally:
        stopped.set()
        thread.join(2)
        listener.close()


def test_actual_tls_sni_and_redirect_body_are_not_followed_or_projected(tmp_path, monkeypatch, caplog):
    with tls_wire(tmp_path, monkeypatch) as state:
        status, latency = net.https_probe("example.test", "8.8.4.4", current=lambda: None)
    assert status == 302 and latency > 0
    assert state["sni"] == ["example.test"] and len(state["requests"]) == 1
    assert "HEAD / HTTP/1.1\r\nHost: example.test" in state["requests"][0]
    assert "private-marker" not in caplog.text and "private-body-marker" not in caplog.text


def test_real_tls_wrong_hostname_refused(tmp_path, monkeypatch):
    with tls_wire(tmp_path, monkeypatch, certificate_name="other.test") as state:
        with pytest.raises(ssl.SSLCertVerificationError):
            net.https_probe("example.test", "8.8.4.4", current=lambda: None)
    assert state["requests"] == []


def test_real_tls_slow_trickle_has_one_total_deadline(tmp_path, monkeypatch):
    started = time.monotonic()
    with tls_wire(tmp_path, monkeypatch, trickle=True):
        with pytest.raises((TimeoutError, net.DiagnosticTransportError)):
            net.https_probe("example.test", "8.8.4.4", current=lambda: None)
    assert time.monotonic() - started < 9.5


@pytest.mark.parametrize(
    "tool,code,expected",
    [
        ("PING", "print('8.8.4.4')", ("OK", "ICMP_REPLY", [])),
        ("TRACEROUTE", "print('1 10.0.0.1 2 8.8.4.4')", ("OK", "ICMP_REPLY", ["8.8.4.4"])),
        ("PING", "import sys;sys.stdout.write('x'*2000000)", ("ERROR", "TOOL_OUTPUT_BOUND_EXCEEDED", [])),
        ("PING", "import time;time.sleep(30)", ("UNKNOWN", "ICMP_TIMEOUT", [])),
    ],
)
def test_owned_process_is_bounded_joined_and_uses_exact_fixed_argv(monkeypatch, tool, code, expected):
    original = subprocess.Popen
    processes = []

    def own_process(args, **kwargs):
        assert args == (
            ["/bin/ping", "-n", "-c", "1", "8.8.4.4"]
            if tool == "PING"
            else ["/usr/bin/traceroute", "-n", "-m", "8", "-q", "1", "-w", "1", "8.8.4.4"]
        )
        assert kwargs["shell"] is False and kwargs["stderr"] == subprocess.DEVNULL
        process = original([sys.executable, "-c", code], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(net.shutil, "which", lambda path: path)
    monkeypatch.setattr(subprocess, "Popen", own_process)
    started = time.monotonic()
    assert net.icmp_probe(tool, "8.8.4.4", current=lambda: None) == expected
    assert time.monotonic() - started < 6
    assert processes and all(process.poll() is not None for process in processes)


def test_missing_icmp_binary_is_supported_refusal_without_process(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda _path: None)

    def refused(*_args, **_kwargs):
        pytest.fail("missing tool must not start a process")

    monkeypatch.setattr(subprocess, "Popen", refused)
    assert net.icmp_probe("PING", "8.8.4.4", current=lambda: None) == (
        "UNSUPPORTED",
        "TOOL_NOT_INSTALLED",
        [],
    )


def test_withdrawn_process_admission_kills_and_joins_owned_child(monkeypatch):
    original = subprocess.Popen
    children = []

    def own_process(_args, **kwargs):
        child = original([sys.executable, "-c", "import time;time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    calls = 0

    def current():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("CURRENT_AUTHORITY_WITHDRAWN")

    monkeypatch.setattr(net.shutil, "which", lambda path: path)
    monkeypatch.setattr(subprocess, "Popen", own_process)
    with pytest.raises(ValueError, match="CURRENT_AUTHORITY_WITHDRAWN"):
        net.icmp_probe("PING", "8.8.4.4", current=current)
    assert len(children) == 1 and children[0].poll() is not None
