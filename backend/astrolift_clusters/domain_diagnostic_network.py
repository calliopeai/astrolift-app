"""Bounded fixed-resolver DNS and pinned public-address diagnostic transports."""

import ipaddress
import secrets
import selectors
import shutil
import socket
import ssl
import struct
import subprocess
import time

from _sdk._dns_probe import _decode_name, _encode_name

RESOLVERS = ("1.1.1.1", "8.8.8.8")
RECORD_TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33, "CAA": 257}


class DiagnosticTransportError(ValueError):
    pass


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise DiagnosticTransportError("PUBLIC_ADDRESS_REQUIRED")
    return str(address)


def dns_lookup(hostname, record_type, resolver, *, current):
    current()
    if resolver not in RESOLVERS or record_type not in RECORD_TYPES:
        raise DiagnosticTransportError("DNS_QUERY_UNSUPPORTED")
    transaction = secrets.randbits(16)
    question = _encode_name(hostname) + struct.pack(">HH", RECORD_TYPES[record_type], 1)
    packet = struct.pack(">HHHHHH", transaction, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.connect((resolver, 53))
        current()
        sock.send(packet)
        data = sock.recv(8193)
    current()
    if len(data) < 12 or len(data) > 8192:
        raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
    txid, flags, questions, answers, _, _ = struct.unpack(">HHHHHH", data[:12])
    if txid != transaction or not flags & 0x8000 or flags & 0x7A00 or questions != 1 or answers > 128:
        raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
    name, offset = _decode_name(data, 12)
    if name.lower().rstrip(".") != hostname or data[offset : offset + 4] != question[-4:]:
        raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
    offset += 4
    code = flags & 15
    if code == 3:
        return [], "DNS_NXDOMAIN"
    if code:
        raise DiagnosticTransportError("DNS_RESOLVER_ERROR")
    records = []
    for _ in range(answers):
        owner, offset = _decode_name(data, offset)
        kind, record_class, _, size = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        end = offset + size
        if end > len(data):
            raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
        if kind in (RECORD_TYPES[record_type], 5) and record_class == 1:
            if kind in (1, 28):
                value = socket.inet_ntop(socket.AF_INET if kind == 1 else socket.AF_INET6, data[offset:end])
            elif kind in (2, 5):
                value, _ = _decode_name(data, offset)
                value = value.lower().rstrip(".")
            elif kind == 15:
                target, _ = _decode_name(data, offset + 2)
                value = str(struct.unpack(">H", data[offset : offset + 2])[0]) + " " + target
            elif kind == 33:
                priority, weight, port = struct.unpack(">HHH", data[offset : offset + 6])
                target, target_end = _decode_name(data, offset + 6)
                if target_end != end:
                    raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
                value = f"{priority} {weight} {port} {target}"
            elif kind == 257:
                if size < 3 or data[offset + 1] < 1 or offset + 2 + data[offset + 1] > end:
                    raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
                tag_end = offset + 2 + data[offset + 1]
                tag = data[offset + 2 : tag_end].decode("ascii")
                value = f'{data[offset]} {tag} "' + data[tag_end:end].decode("utf-8") + '"'
            elif kind == 6:
                first, second_at = _decode_name(data, offset)
                second, numbers_at = _decode_name(data, second_at)
                if numbers_at + 20 != end:
                    raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
                value = " ".join(
                    (first, second, *(str(n) for n in struct.unpack(">IIIII", data[numbers_at:end])))
                )
            else:
                chunks, index = [], offset
                while index < end:
                    length = data[index]
                    index += 1
                    if index + length > end:
                        raise DiagnosticTransportError("DNS_RESPONSE_INVALID")
                    chunks.append(data[index : index + length].decode("utf-8", errors="replace"))
                    index += length
                value = "".join(chunks)
            if len(value) > 2048 or len(records) >= 128:
                raise DiagnosticTransportError("DNS_RESPONSE_BOUND_EXCEEDED")
            records.append((owner.lower().rstrip("."), kind, value))
        offset = end
    admitted = {hostname}
    for _ in range(16):
        targets = {value for owner, kind, value in records if owner in admitted and kind == 5}
        if targets <= admitted:
            break
        admitted.update(targets)
    else:
        raise DiagnosticTransportError("DNS_ALIAS_BOUND_EXCEEDED")
    values = sorted(
        {value for owner, kind, value in records if owner in admitted and kind == RECORD_TYPES[record_type]}
    )
    if len(values) > 64:
        raise DiagnosticTransportError("DNS_RESPONSE_BOUND_EXCEEDED")
    return values, "DNS_ANSWER" if values else "DNS_NO_DATA"


def https_probe(hostname, address, *, current):
    address = public_address(address)
    current()
    started = time.monotonic()
    deadline = started + 8

    def remaining():
        current()
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise DiagnosticTransportError("HTTPS_DEADLINE_EXCEEDED")
        return seconds

    context = ssl.create_default_context()
    with socket.create_connection((address, 443), timeout=remaining()) as raw:
        current()
        raw.settimeout(remaining())
        with context.wrap_socket(raw, server_hostname=hostname) as secure:
            current()
            secure.settimeout(remaining())
            secure.sendall(
                f"HEAD / HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            # The status line alone is observed; no response headers/body or
            # Location is consumed, forwarded, logged or followed.
            line = bytearray()
            while len(line) <= 256 and not line.endswith(b"\r\n"):
                secure.settimeout(remaining())
                chunk = secure.recv(1)
                if not chunk:
                    break
                line.extend(chunk)
            parts = bytes(line).split(b" ")
            if (
                len(line) > 256
                or not line.endswith(b"\r\n")
                or len(parts) < 2
                or parts[0] not in (b"HTTP/1.0", b"HTTP/1.1")
                or not parts[1].isdigit()
            ):
                raise DiagnosticTransportError("HTTPS_RESPONSE_INVALID")
            status = int(parts[1])
            if not 100 <= status <= 599:
                raise DiagnosticTransportError("HTTPS_RESPONSE_INVALID")
    remaining()
    return status, (time.monotonic() - started) * 1000


def icmp_probe(tool, address, *, current):
    address = public_address(address)
    # Server-installed binaries only. No PATH lookup or caller arguments.
    binary = "/bin/ping" if tool == "PING" else "/usr/bin/traceroute"
    if shutil.which(binary) is None:
        return "UNSUPPORTED", "TOOL_NOT_INSTALLED", []
    args = (
        [binary, "-n", "-c", "1", address]
        if tool == "PING"
        else [binary, "-n", "-m", "8", "-q", "1", "-w", "1", address]
    )
    current()
    deadline = time.monotonic() + 5
    body = bytearray()
    with subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        bufsize=0,
    ) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    current()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return "UNKNOWN", "ICMP_TIMEOUT", []
                    if not selector.select(min(remaining, 0.1)):
                        continue
                    chunk = process.stdout.read(min(1024, 8193 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > 8192:
                        return "ERROR", "TOOL_OUTPUT_BOUND_EXCEEDED", []
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            current()
            return "UNKNOWN", "ICMP_TIMEOUT", []
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)
    current()
    # Never project raw command output or internal hop addresses.
    hops = []
    if tool == "TRACEROUTE":
        for token in body.decode("ascii", errors="replace").split():
            try:
                hops.append(public_address(token))
            except ValueError:
                continue
    return (
        ("OK", "ICMP_REPLY", sorted(set(hops)))
        if process.returncode == 0
        else ("UNKNOWN", "ICMP_UNCONFIRMED", [])
    )
