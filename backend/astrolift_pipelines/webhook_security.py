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
from datetime import UTC
from typing import TYPE_CHECKING

from django.core.cache import cache

if TYPE_CHECKING:
    pass

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
    from datetime import datetime

    # Bucket key: per-org, per-minute
    minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
    cache_key = f"webhook:rate:{org_slug}:{minute}"

    # `add` then `incr`, not `get` then `set`. The original was a
    # read-modify-write: concurrent workers all read the same count and all
    # passed, so the limit did not hold under exactly the load it exists
    # for. `add` is a no-op when the key exists, and `incr` is atomic on
    # every backend that matters here (Redis, memcached); the locmem
    # backend used in tests serialises anyway.
    #
    # 2-minute TTL on a 1-minute bucket so a request that straddles the
    # boundary cannot land on an already-expired key.
    cache.add(cache_key, 0, 120)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # The key expired between `add` and `incr`. Treat as the first
        # request of a fresh bucket rather than failing the webhook.
        cache.set(cache_key, 1, 120)
        count = 1

    if count > _WEBHOOK_RATE_LIMIT_PER_MINUTE:
        raise WebhookSecurityError(
            f"Webhook rate limit exceeded for org {org_slug!r} "
            f"({count}/{_WEBHOOK_RATE_LIMIT_PER_MINUTE} per minute)"
        )


# The two `full_security_check_*` wrappers that used to live here are
# gone. Both ran `check_webhook_rate_limit` *before* verifying the
# signature, and `org_slug` comes from the URL -- so anyone who knew an
# org's slug could spend that org's 300/minute budget and lock out its real
# webhooks, unauthenticated. The correct order also differs per host
# (GitLab has no reliable delivery id, so no replay check), which makes the
# receiver the honest place for it. Each receiver now orders the checks
# explicitly: size, then signature, then rate, then replay.


# ---------------------------------------------------------------------------
# The shared webhook secret
# ---------------------------------------------------------------------------


def org_webhook_secret(org, *, source_kind: str) -> bytes | None:
    """Return the org's shared webhook secret, or None when unset.

    Both pipeline receivers used to look this up through
    ``astrolift_lifecycle.services.secrets.read_org_secret`` -- a module that
    does not exist -- with a fallback to ``Organization.extra_data``, a field
    that was dropped from the model. So the lookup returned None
    unconditionally and **every pipeline webhook was answered 401**.

    There was never a missing capability here. The secret already lives,
    encrypted, on the org's ``SourceConnection``
    (``webhook_secret_backend_kind`` + ``webhook_secret_ciphertext``), written
    at App-install time by ``auth1/scm_app_manifest.py`` and read by two other
    live receivers -- ``auth1/scm_webhook.py`` and
    ``astrolift_scm/webhook_views.py`` -- through exactly the decrypt below.
    The pipeline receivers were reaching past a populated, purpose-built
    column for a store that was never built.

    Scoped by ``source_kind`` deliberately: verifying a GitLab delivery
    against a GitHub connection's secret would be a cross-host confusion, and
    the two hosts use the secret differently (HMAC key vs. bearer token).

    App installations rank first because that is the connection the manifest
    flow populates; an org that onboarded with an OAuth or PAT connection
    carrying a secret still resolves.

    No global fallback on purpose. A single platform-wide webhook secret would
    verify one tenant's deliveries with another's credential, which is the
    same anti-pattern the commit-status credential lookup shipped with.
    """
    from astrolift_scm.models import SourceConnection
    from core.secrets import EncryptedSecret, decrypt

    rows = list(
        SourceConnection.objects.filter(
            organization_id=getattr(org, "pk", org),
            kind__startswith=f"{source_kind}_",
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
        .exclude(webhook_secret_backend_kind="")
        .exclude(webhook_secret_ciphertext=b"")
    )
    rows.sort(key=lambda r: (0 if r.kind.endswith("_app_install") else 1, r.pk))

    for connection in rows:
        try:
            return decrypt(
                EncryptedSecret(
                    backend_kind=connection.webhook_secret_backend_kind,
                    backend_ref=bytes(connection.webhook_secret_ciphertext),
                )
            )
        except Exception:  # noqa: BLE001
            # A single undecryptable row must not mask a sibling that works
            # (a rotated key, a half-migrated backend). Try the next one.
            logger.warning(
                "pipelines.webhook_security: could not decrypt webhook secret on " "connection %s",
                connection.guid,
                exc_info=True,
            )
    return None
