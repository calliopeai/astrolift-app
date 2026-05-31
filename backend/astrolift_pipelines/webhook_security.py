"""Pipeline webhook security — signature verification, replay protection, rate limiting (#89).

This module provides the security layer for all pipeline webhook endpoints.
It is separate from the payload parsing in webhook_views.py so it can be
tested independently and reused by both GitHub and GitLab receivers.

Security controls:
1. **Signature verification**: HMAC-SHA256 (GitHub) or opaque token (GitLab).
   Already implemented in webhook_views.py; this module adds shared utilities.
2. **Replay protection**: Track seen delivery IDs (X-GitHub-Delivery header).
   Reject duplicates within a rolling 24-hour window.
3. **Payload size limits**: Reject payloads > 25MB (GitHub's own limit).
4. **Rate limiting**: Per-org webhook rate limit. Shared with concurrency.py's
   per-minute run limit but applied at the webhook layer before a run is created.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

from django.core.cache import cache

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB — matches GitHub's limit
_DELIVERY_ID_CACHE_SECONDS = 24 * 60 * 60  # 24 hours
_WEBHOOK_RATE_LIMIT_PER_MINUTE = 300  # per-org; higher than run rate limit


class WebhookSecurityError(Exception):
    """Raised when a webhook request fails a security check."""


def check_payload_size(body: bytes) -> None:
    """Raise WebhookSecurityError if the payload exceeds the size limit."""
    if len(body) > _MAX_PAYLOAD_BYTES:
        raise WebhookSecurityError(
            f"Payload size {len(body)} bytes exceeds the {_MAX_PAYLOAD_BYTES}-byte limit"
        )


def verify_github_hmac(secret: bytes, body: bytes, signature_header: str) -> None:
    """Verify the X-Hub-Signature-256 header.

    Raises WebhookSecurityError on failure.
    """
    if not signature_header.startswith("sha256="):
        raise WebhookSecurityError("Missing or malformed X-Hub-Signature-256 header")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise WebhookSecurityError("Signature mismatch")


def verify_gitlab_token(secret: str, provided: str) -> None:
    """Verify the X-Gitlab-Token header.

    Raises WebhookSecurityError on failure.
    """
    if not provided:
        raise WebhookSecurityError("Missing X-Gitlab-Token header")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise WebhookSecurityError("Token mismatch")


def check_replay(delivery_id: str, org_slug: str) -> None:
    """Reject duplicate webhook deliveries within the 24-hour replay window.

    Uses Django's cache backend as a distributed seen-delivery-id store.
    Raises WebhookSecurityError if the delivery ID was already processed.
    """
    if not delivery_id:
        return  # No delivery ID — can't do replay protection (GitLab doesn't always send one)

    cache_key = f"webhook:delivery:{org_slug}:{delivery_id}"
    if cache.get(cache_key):
        raise WebhookSecurityError(
            f"Duplicate webhook delivery {delivery_id!r} — already processed within 24h"
        )
    cache.set(cache_key, True, _DELIVERY_ID_CACHE_SECONDS)


def check_webhook_rate_limit(org_slug: str) -> None:
    """Reject requests that exceed the per-org webhook rate limit.

    Uses a sliding window counter in the Django cache.
    Raises WebhookSecurityError if over the limit.
    """
    from datetime import datetime, timezone as _tz

    # Bucket key: per-org, per-minute
    minute = datetime.now(_tz.utc).strftime("%Y%m%d%H%M")
    cache_key = f"webhook:rate:{org_slug}:{minute}"

    count = cache.get(cache_key, 0)
    if count >= _WEBHOOK_RATE_LIMIT_PER_MINUTE:
        raise WebhookSecurityError(
            f"Webhook rate limit exceeded for org {org_slug!r} "
            f"({count}/{_WEBHOOK_RATE_LIMIT_PER_MINUTE} per minute)"
        )

    # Increment counter, expire at end of the next minute
    cache.set(cache_key, count + 1, 120)  # 2-minute TTL to handle clock skew


def full_security_check_github(
    *,
    body: bytes,
    secret: bytes,
    signature: str,
    delivery_id: str,
    org_slug: str,
) -> None:
    """Run all security checks for a GitHub webhook request.

    Raises WebhookSecurityError on the first failing check.
    """
    check_payload_size(body)
    check_webhook_rate_limit(org_slug)
    verify_github_hmac(secret, body, signature)
    check_replay(delivery_id, org_slug)


def full_security_check_gitlab(
    *,
    body: bytes,
    secret: str,
    token: str,
    org_slug: str,
) -> None:
    """Run all security checks for a GitLab webhook request."""
    check_payload_size(body)
    check_webhook_rate_limit(org_slug)
    verify_gitlab_token(secret, token)
    # GitLab doesn't consistently send delivery IDs, so no replay check
