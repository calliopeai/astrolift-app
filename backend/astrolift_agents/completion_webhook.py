"""Task completion wire contract and retry policy, independent of transport."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import uuid
from urllib.parse import urlsplit

EVENT = "agent_task.finished"
REQUEST_TIMEOUT_SECONDS = 10
RETRY_WINDOW_SECONDS = 24 * 60 * 60
RETRY_INTERVALS_SECONDS = (30, 120, 600, 1800)
MAX_RETRY_INTERVAL_SECONDS = 3600
MAX_CLOCK_SKEW_SECONDS = 300


class CallbackConfigurationError(ValueError):
    """A safe message that contains neither the destination nor secret material."""


def canonical_host(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CallbackConfigurationError("Callback hosts must be nonempty hostnames")
    if any(character in value for character in "/@?#\\\x00"):
        raise CallbackConfigurationError("Callback hosts cannot contain URL components")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        pass
    try:
        host = value.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CallbackConfigurationError("Invalid callback hostname") from exc
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
    ):
        raise CallbackConfigurationError("Invalid callback hostname")
    return host


def normalize_allowed_hosts(values: object) -> list[str]:
    if not isinstance(values, list) or len(values) > 100:
        raise CallbackConfigurationError("Callback allow-list must contain at most 100 hosts")
    patterns = []
    for value in values:
        if not isinstance(value, str):
            raise CallbackConfigurationError("Callback allow-list entries must be strings")
        wildcard = value.startswith("*.")
        host = canonical_host(value[2:] if wildcard else value)
        if wildcard:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if "." not in host:
                    raise CallbackConfigurationError("Wildcard callback hosts require a domain") from None
            else:
                raise CallbackConfigurationError("Wildcard callback hosts cannot be IP addresses")
        pattern = "*." + host if wildcard else host
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def blocked_address(value: str) -> bool:
    """Private VPC addresses are allowed; local and metadata routes are not."""
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address == ipaddress.ip_address("100.100.100.200")
    )


def validate_destination(url: str, allowed_hosts: object) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise CallbackConfigurationError("callbackUrl must be an HTTPS URL of at most 2048 characters")
    if any(ord(character) < 33 or ord(character) == 127 for character in url) or "\\" in url:
        raise CallbackConfigurationError("callbackUrl contains invalid URL characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port == 0
        ):
            raise ValueError
        host = canonical_host(parsed.hostname)
    except (ValueError, UnicodeError) as exc:
        raise CallbackConfigurationError(
            "callbackUrl must be HTTPS without credentials or a fragment"
        ) from exc
    patterns = normalize_allowed_hosts(allowed_hosts)
    if not any(
        host == pattern or pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]
        for pattern in patterns
    ):
        raise CallbackConfigurationError("callbackUrl host is not on the organization callback allow-list")
    if host == "localhost" or host.endswith(".localhost"):
        raise CallbackConfigurationError("Local callback destinations are not allowed")
    try:
        is_blocked = blocked_address(host)
    except ValueError:
        is_blocked = False
    if is_blocked:
        raise CallbackConfigurationError("Local and metadata callback destinations are not allowed")
    return host


def encoded_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def signature(secret: bytes, timestamp: str, body: bytes) -> str:
    if not secret:
        raise CallbackConfigurationError("Callback signing secret is unavailable")
    digest = hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return "v1=" + digest


def delivery_headers(secret: bytes, body: bytes, *, timestamp: int) -> dict[str, str]:
    value = str(timestamp)
    return {
        "Content-Type": "application/json",
        "X-Astrolift-Event": EVENT,
        "X-Astrolift-Delivery": str(uuid.uuid4()),
        "X-Astrolift-Timestamp": value,
        "X-Astrolift-Signature": signature(secret, value, body),
    }


def verify_signature(secret: bytes, timestamp: str, body: bytes, received: str, *, now: int) -> bool:
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,12}", timestamp):
        return False
    if (
        abs(now - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS
        or not secret
        or not isinstance(received, str)
        or not re.fullmatch(r"v1=[0-9a-f]{64}", received)
    ):
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), received)


def classify_response(status_code: int | None) -> str:
    if status_code is not None and 200 <= status_code < 300:
        return "delivered"
    if status_code is not None and 400 <= status_code < 500 and status_code not in (408, 429):
        return "failed"
    return "retry"


def retry_delay(attempt: int, elapsed_seconds: float, *, jitter: float) -> float | None:
    """An outage gets an attempt at/beyond 24h, including with negative jitter."""
    if attempt < 1 or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Invalid callback retry state")
    if not math.isfinite(jitter) or not 0 <= jitter <= 1:
        raise ValueError("Invalid callback retry jitter")
    if elapsed_seconds >= RETRY_WINDOW_SECONDS:
        return None
    base = (
        RETRY_INTERVALS_SECONDS[attempt - 1]
        if attempt <= len(RETRY_INTERVALS_SECONDS)
        else MAX_RETRY_INTERVAL_SECONDS
    )
    return min(
        base * (0.8 + 0.4 * jitter),
        MAX_RETRY_INTERVAL_SECONDS * 1.2,
        RETRY_WINDOW_SECONDS - elapsed_seconds,
    )
