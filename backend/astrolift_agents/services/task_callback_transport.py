"""TLS-verified, DNS-pinned VPC callback transport with no body telemetry."""

from __future__ import annotations

import contextlib
import http.client
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from urllib.parse import urlsplit

from astrolift_agents.completion_webhook import (
    REQUEST_TIMEOUT_SECONDS,
    CallbackConfigurationError,
    blocked_address,
    delivery_headers,
    validate_destination,
)

_DNS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="callback-dns")
_DNS_SLOTS = threading.BoundedSemaphore(8)


def _resolve(host, port):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise TimeoutError("callback resolution unavailable")
    future = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    addresses = future.result(timeout=REQUEST_TIMEOUT_SECONDS)
    if not addresses or any(blocked_address(cast(str, item[4][0])) for item in addresses):
        raise CallbackConfigurationError("callback address is not permitted")
    return list(dict.fromkeys(item[4][0] for item in addresses))


def post_completion_callback(url: str, allowed_hosts: list[str], body: bytes, secret: str) -> int:
    host = validate_destination(url, allowed_hosts)
    parsed = urlsplit(url)
    port = parsed.port or 443
    deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    addresses = _resolve(host, port)
    connection = http.client.HTTPSConnection(
        host, port, context=ssl.create_default_context(), timeout=max(0.001, deadline - time.monotonic())
    )
    connection.set_debuglevel(0)

    def connect(_address, timeout=None, source_address=None):
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("callback request timed out")
            try:
                return socket.create_connection(
                    (address, port), timeout=remaining, source_address=source_address
                )
            except OSError:
                continue
        raise OSError("callback connection unavailable")

    connection._create_connection = connect  # type: ignore[attr-defined]

    def expire():
        if connection.sock:
            with contextlib.suppress(OSError):
                connection.sock.shutdown(socket.SHUT_RDWR)
        connection.close()

    timer = threading.Timer(max(0.001, deadline - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    try:
        telemetry: contextlib.AbstractContextManager[None]
        try:
            from opentelemetry.instrumentation.utils import suppress_http_instrumentation

            telemetry = suppress_http_instrumentation()
        except ImportError:
            telemetry = contextlib.nullcontext()
        with telemetry:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request(
                "POST",
                path,
                body=body,
                headers=delivery_headers(secret.encode("utf-8"), body, timestamp=int(time.time())),
            )
            return connection.getresponse().status
    finally:
        timer.cancel()
        connection.close()
