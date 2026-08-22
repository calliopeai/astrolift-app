"""Unit tests for the pure-Python DNS TXT probe (#632).

The wire-format encoder + decoder is small but load-bearing; bit-rot
here would silently degrade every DKIM/SPF/DMARC probe. The tests
exercise the round-trip via a hand-rolled DNS reply mirroring real
SES + DMARC TXT records, plus the system-resolver discovery fallback.
"""

from __future__ import annotations

import struct

from _sdk._dns_probe import (
    _decode_name,
    _encode_name,
    _system_resolvers,
)


def test_encode_name_simple() -> None:
    out = _encode_name("example.com")
    # 7 'example' 3 'com' 0
    assert out == b"\x07example\x03com\x00"


def test_encode_name_with_trailing_dot() -> None:
    # A trailing dot is stripped (apex marker).
    assert _encode_name("example.com.") == _encode_name("example.com")


def test_decode_name_no_compression() -> None:
    encoded = _encode_name("_dmarc.example.com")
    name, next_offset = _decode_name(encoded, 0)
    assert name == "_dmarc.example.com"
    assert next_offset == len(encoded)


def test_decode_name_with_pointer_compression() -> None:
    # Build a synthetic buffer:
    #   offset 0..n: 'example.com' encoded inline
    #   offset n+: a pointer (0xC0 0x00) referring back to byte 0
    suffix = _encode_name("example.com")
    pointer = bytes([0xC0, 0x00])
    buf = suffix + pointer
    name, next_offset = _decode_name(buf, len(suffix))
    assert name == "example.com"
    assert next_offset == len(buf)


def test_system_resolvers_runs_without_raising() -> None:
    # Either returns a list (possibly empty) — the function MUST NOT
    # raise even on a Windows / distroless / pid-namespaced host.
    out = _system_resolvers()
    assert isinstance(out, list)


def test_query_txt_round_trip(monkeypatch) -> None:
    """End-to-end: build a fake DNS reply byte string and run it through
    the decoder via a patched socket."""
    from _sdk import _dns_probe

    class _FakeSock:
        def __init__(self) -> None:
            self.last_packet: bytes = b""

        def settimeout(self, _t: float) -> None:
            pass

        def sendto(self, packet: bytes, _addr: tuple[str, int]) -> None:
            self.last_packet = packet

        def recvfrom(self, _bufsize: int):
            # Pull the txid off the question packet so the reply matches.
            txid = struct.unpack(">H", self.last_packet[0:2])[0]
            # 1 question, 1 answer
            header = struct.pack(">HHHHHH", txid, 0x8180, 1, 1, 0, 0)
            qname = _encode_name("example.com")
            question = qname + struct.pack(">HH", 16, 1)
            # Answer: name pointer to question (offset 12), type=16, class=1,
            # ttl=300, rdlength = 1 + len(content)
            content = b"v=spf1 include:amazonses.com ~all"
            rdata = bytes([len(content)]) + content
            answer = bytes([0xC0, 0x0C]) + struct.pack(">HHIH", 16, 1, 300, len(rdata)) + rdata
            return header + question + answer, ("127.0.0.1", 53)

        def close(self) -> None:
            pass

    fake = _FakeSock()

    def _factory(*_a, **_k):
        return fake

    monkeypatch.setattr(_dns_probe.socket, "socket", _factory)

    answers = _dns_probe._query_records(
        server="127.0.0.1",
        qname="example.com",
    )
    assert answers == ["v=spf1 include:amazonses.com ~all"]


def test_query_txt_nxdomain_returns_empty(monkeypatch) -> None:
    from _sdk import _dns_probe

    class _FakeSock:
        def __init__(self) -> None:
            self.last_packet = b""

        def settimeout(self, _t: float) -> None:
            pass

        def sendto(self, packet: bytes, _addr) -> None:
            self.last_packet = packet

        def recvfrom(self, _bufsize: int):
            txid = struct.unpack(">H", self.last_packet[0:2])[0]
            # rcode=3 (NXDOMAIN)
            header = struct.pack(">HHHHHH", txid, 0x8183, 1, 0, 0, 0)
            qname = _encode_name("missing.example.com")
            question = qname + struct.pack(">HH", 16, 1)
            return header + question, ("127.0.0.1", 53)

        def close(self) -> None:
            pass

    fake = _FakeSock()
    monkeypatch.setattr(
        _dns_probe.socket,
        "socket",
        lambda *a, **k: fake,
    )
    answers = _dns_probe._query_records(
        server="127.0.0.1",
        qname="missing.example.com",
    )
    assert answers == []


def test_query_ns_round_trip(monkeypatch) -> None:
    """NS rdata is a domain name that may point back into the message, so it
    has to be decoded against the whole buffer rather than the rdata slice."""
    from _sdk import _dns_probe

    class _FakeSock:
        def __init__(self) -> None:
            self.last_packet: bytes = b""

        def settimeout(self, _t: float) -> None:
            pass

        def sendto(self, packet: bytes, _addr) -> None:
            self.last_packet = packet

        def recvfrom(self, _bufsize: int):
            txid = struct.unpack(">H", self.last_packet[0:2])[0]
            header = struct.pack(">HHHHHH", txid, 0x8180, 1, 1, 0, 0)
            qname = _encode_name("apps.example.com")
            question = qname + struct.pack(">HH", 2, 1)
            # NS target 'ns-1.awsdns-01.com' with its final label replaced by a
            # pointer to the question's 'com' label.
            com_offset = 12 + len(_encode_name("apps.example")) - 1
            rdata = b"\x04ns-1\x09awsdns-01" + bytes([0xC0, com_offset])
            answer = bytes([0xC0, 0x0C]) + struct.pack(">HHIH", 2, 1, 300, len(rdata)) + rdata
            return header + question + answer, ("127.0.0.1", 53)

        def close(self) -> None:
            pass

    fake = _FakeSock()
    monkeypatch.setattr(_dns_probe.socket, "socket", lambda *a, **k: fake)

    answers = _dns_probe._query_records(
        server="127.0.0.1",
        qname="apps.example.com",
        qtype=2,
    )
    assert answers == ["ns-1.awsdns-01.com"]
