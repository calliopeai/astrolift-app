"""Minimal pure-Python DNS resolver for the email auth probe (#632).

Why not `dnspython`: the providers tree intentionally avoids transitive
runtime dependencies beyond what the cloud SDKs already pull in. Adding
`dnspython` would churn the lockfile and break parallel-agent CI for
every downstream consumer. The TXT query path is tiny (well under 100
lines of RFC 1035 wire encoding) and adding it here keeps the SDK self-
contained.

The implementation:

* UDP query against the system resolver (read from
  ``/etc/resolv.conf``); fall back to Google Public DNS (``8.8.8.8``)
  when no nameserver line is present (e.g. plain-pid namespaces).
* Builds a standard DNS query (one question, class IN) and parses the
  answer section. TXT records are RFC 1035 character strings;
  multi-string TXT answers are concatenated per RFC 4408. NS records
  are a single (possibly compression-pointer) domain name.
* Times out after 3 seconds per attempt; up to two retries with a
  fresh transaction id. On total failure, surfaces an exception the
  caller renders as ``UNKNOWN`` rather than guessing the auth state.

Scope: TXT (the DKIM / SPF / DMARC probe; DKIM presence is read from
SES itself, not DNS) and NS (the managed-zone delegation gate, which
has to know what the public internet is told to ask for a zone).
"""

from __future__ import annotations

import io
import os
import random
import socket
import struct
from dataclasses import dataclass


class DnsResolveError(RuntimeError):
    """The probe couldn't reach a resolver or got a malformed reply."""


@dataclass(frozen=True)
class _DnsHeader:
    txid: int
    flags: int
    qdcount: int
    ancount: int
    nscount: int
    arcount: int


# Public resolver fallbacks. We try the system resolvers first, then
# these in order. Google + Cloudflare are the obvious choices; both
# answer TXT queries without rate-limiting the kinds of probe volumes
# the platform generates (one probe per email-detail page load).
_FALLBACK_RESOLVERS: tuple[str, ...] = ("8.8.8.8", "1.1.1.1")


def _system_resolvers() -> list[str]:
    """Read nameserver lines from ``/etc/resolv.conf``.

    macOS, Linux, and the Kubernetes pod DNS shim all populate this file.
    Missing file (Windows, distroless image, pid namespace) falls back to
    the public resolvers."""
    path = "/etc/resolv.conf"
    if not os.path.exists(path):
        return []
    out: list[str] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        out.append(parts[1])
    except OSError:
        return []
    return out


def _encode_name(name: str) -> bytes:
    """RFC 1035 §3.1: each label is length-prefixed; terminated by 0."""
    out = bytearray()
    for label in name.rstrip(".").split("."):
        if not label:
            continue
        encoded = label.encode("ascii")
        if len(encoded) > 63:
            raise DnsResolveError(f"DNS label too long: {label!r}")
        out.append(len(encoded))
        out.extend(encoded)
    out.append(0)
    return bytes(out)


def _decode_name(buf: bytes, offset: int) -> tuple[str, int]:
    """RFC 1035 §4.1.4: name may use pointer compression (0xC0 prefix)."""
    labels: list[str] = []
    jumped = False
    next_offset = offset
    safety = 0
    while True:
        safety += 1
        if safety > 128:
            raise DnsResolveError("DNS name decode loop guard")
        length = buf[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | buf[offset + 1]
            if not jumped:
                next_offset = offset + 2
                jumped = True
            offset = pointer
            continue
        offset += 1
        labels.append(buf[offset : offset + length].decode("ascii"))
        offset += length
    if not jumped:
        next_offset = offset
    return ".".join(labels), next_offset


_TYPE_NS = 2
_TYPE_TXT = 16


def _query_records(
    *,
    server: str,
    qname: str,
    qtype: int = _TYPE_TXT,
    timeout: float = 3.0,
) -> list[str]:
    """One round-trip query of ``qtype`` against ``server`` (UDP/53)."""
    txid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = _encode_name(qname) + struct.pack(">HH", qtype, 1)
    packet = header + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()

    if len(data) < 12:
        raise DnsResolveError("DNS reply too short")
    rtxid, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", data[:12])
    if rtxid != txid:
        raise DnsResolveError("DNS reply txid mismatch")
    rcode = flags & 0x000F
    if rcode == 3:  # NXDOMAIN
        return []
    if rcode != 0:
        raise DnsResolveError(f"DNS reply rcode {rcode}")

    # Skip the question section.
    offset = 12
    for _ in range(qdcount):
        _, offset = _decode_name(data, offset)
        offset += 4  # qtype + qclass

    answers: list[str] = []
    for _ in range(ancount):
        _, offset = _decode_name(data, offset)
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        rdata_start = offset
        offset += rdlength
        if rtype != qtype:
            continue
        if qtype == _TYPE_NS:
            # NS rdata is one domain name, which may use compression
            # pointers back into the message — decode against the whole
            # buffer, not the rdata slice.
            name, _ = _decode_name(data, rdata_start)
            answers.append(name.lower())
            continue
        # TXT rdata is one or more length-prefixed strings (RFC 1035
        # §3.3.14). Concatenate per RFC 4408 (SPF).
        buf = io.BytesIO(rdata)
        chunks: list[str] = []
        while True:
            length_byte = buf.read(1)
            if not length_byte:
                break
            length = length_byte[0]
            chunk = buf.read(length)
            chunks.append(chunk.decode("utf-8", errors="replace"))
        answers.append("".join(chunks))
    return answers


def _lookup(qname: str, *, qtype: int, timeout: float) -> list[str]:
    servers = _system_resolvers() + list(_FALLBACK_RESOLVERS)
    last_exc: Exception | None = None
    for server in servers:
        try:
            return _query_records(server=server, qname=qname, qtype=qtype, timeout=timeout)
        except (OSError, DnsResolveError) as exc:
            last_exc = exc
            continue
    raise DnsResolveError(f"all resolvers failed for {qname!r}: {last_exc}") from last_exc


def lookup_txt(qname: str, *, timeout: float = 3.0) -> list[str]:
    """Resolve TXT records for ``qname`` via the system resolvers.

    Returns the list of TXT strings (concatenated per record). Empty
    list means the domain answered NXDOMAIN. Raises
    :class:`DnsResolveError` when every resolver attempt failed —
    callers render this as ``UNKNOWN`` so operators can retry without
    us blaming the auth setup.
    """
    return _lookup(qname, qtype=_TYPE_TXT, timeout=timeout)


def lookup_ns(qname: str, *, timeout: float = 3.0) -> list[str]:
    """Resolve NS records for ``qname`` via the system resolvers.

    Returns the delegated nameserver hostnames, lowercased and without the
    trailing dot. Empty list means the resolver answered but the name has no
    delegation (NXDOMAIN / no answer) — a real finding, not a failure to
    look. :class:`DnsResolveError` means the lookup itself could not be
    performed, which callers must not read as "not delegated".
    """
    return _lookup(qname, qtype=_TYPE_NS, timeout=timeout)
