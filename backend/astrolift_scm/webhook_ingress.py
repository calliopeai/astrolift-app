"""
Webhook ingress policy + per-source HMAC verifiers (#93, spec 27 §14).

Pure-Python module for the receivers at:
  POST /api/webhooks/github/
  POST /api/webhooks/gitlab/
  POST /api/webhooks/bitbucket/
  POST /api/webhooks/gitea/
  POST /api/webhooks/generic/

Each provider signs its webhook payload differently:
  GitHub:    X-Hub-Signature-256 = sha256=<hex>
  GitLab:    X-Gitlab-Token = <secret> (no HMAC, opaque token)
  Bitbucket: X-Hub-Signature = sha256=<hex>
  Gitea:     X-Gitea-Signature = <hex>
  Generic:   our own scheme; reuses the egress signer (#37)

Per spec 27 §14:
* HMAC verified against per-source secret
* Response 200 within 1s; processing async via Temporal workflows
* Replays detected via per-source delivery id (record-and-reject)
* Rate limit: 60/sec/source — dropped beyond that

The async processing path lives in workflows
(``ProcessGitHubWebhookWorkflow`` etc.); this module is the
synchronous accept/reject layer the receiver runs first.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
from collections.abc import Mapping
from enum import Enum


class WebhookSource(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GITEA = "gitea"
    GENERIC = "generic"


# Spec 27 §8 rate limit.
WEBHOOK_RATE_LIMIT_PER_SECOND = 60


# Header conventions per source. Caller projects the request's
# headers into a dict and passes through here.
_SIG_HEADER: dict[WebhookSource, str] = {
    WebhookSource.GITHUB: "X-Hub-Signature-256",
    WebhookSource.GITLAB: "X-Gitlab-Token",
    WebhookSource.BITBUCKET: "X-Hub-Signature",
    WebhookSource.GITEA: "X-Gitea-Signature",
    WebhookSource.GENERIC: "X-Astrolift-Signature",
}

_DELIVERY_ID_HEADER: dict[WebhookSource, str] = {
    WebhookSource.GITHUB: "X-GitHub-Delivery",
    WebhookSource.GITLAB: "X-Gitlab-Event-UUID",
    WebhookSource.BITBUCKET: "X-Hook-UUID",
    WebhookSource.GITEA: "X-Gitea-Delivery",
    WebhookSource.GENERIC: "X-Astrolift-Delivery-Id",
}


class WebhookRejected(Exception):
    """Receiver returns 4xx with this exception's message. Never
    bubbles details that could leak the secret."""


# ---- signature verification ----------------------------------------


def _get_header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive lookup. HTTP headers are case-insensitive
    per RFC 7230; some receivers normalize to title-case, others
    don't."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return ""


def _normalize_sig(value: str) -> tuple[str, str]:
    """Return (algorithm_prefix, hex_digest). GitHub-style
    'sha256=...' splits cleanly; bare hex is treated as
    sha256."""
    if "=" in value:
        prefix, _, digest = value.partition("=")
        return prefix.lower(), digest
    return "sha256", value


def verify_signature(
    *,
    source: WebhookSource,
    secret: bytes,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> None:
    """Per-source signature verification. Raises WebhookRejected
    on any failure with a generic message (no per-check details
    so an attacker can't probe which check fired)."""
    if not secret:
        raise WebhookRejected("no secret configured for source")

    sig_header_name = _SIG_HEADER[source]
    presented = _get_header(headers, sig_header_name)
    if not presented:
        raise WebhookRejected("signature missing")

    if source == WebhookSource.GITLAB:
        # GitLab uses an opaque token, not HMAC. constant-time
        # compare the bytes.
        try:
            secret_str = secret.decode("utf-8")
        except UnicodeDecodeError:
            raise WebhookRejected("bad secret encoding") from None
        if not hmac.compare_digest(presented, secret_str):
            raise WebhookRejected("signature mismatch")
        return

    # All HMAC providers compute SHA-256 over the raw body.
    _, digest = _normalize_sig(presented)
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise WebhookRejected("signature mismatch")


# ---- delivery id (replay detection) --------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryRef:
    """Identifies one webhook delivery for replay detection."""

    source: WebhookSource
    delivery_id: str


def extract_delivery_id(
    *,
    source: WebhookSource,
    headers: Mapping[str, str],
) -> DeliveryRef:
    """Read the per-source delivery id header. The receiver hands
    this to the replay store (Redis SETNX or DB unique constraint)
    to detect duplicates."""
    name = _DELIVERY_ID_HEADER[source]
    delivery_id = _get_header(headers, name)
    if not delivery_id:
        raise WebhookRejected(f"missing delivery id ({name})")
    return DeliveryRef(source=source, delivery_id=delivery_id)


def is_replay(*, ref: DeliveryRef, seen_ids: set[str]) -> bool:
    """Pure check: caller projects the recently-seen delivery ids
    (e.g. from Redis with a 24h TTL) and asks whether this one's
    a duplicate."""
    return ref.delivery_id in seen_ids


# ---- ingress decision ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IngressDecision:
    """The receiver hands these to the async-handoff layer."""

    accepted: bool
    reason: str
    delivery_ref: DeliveryRef | None
    """Set on accept; used by the async handler to find the
    original payload row."""


def evaluate(
    *,
    source: WebhookSource,
    secret: bytes,
    raw_body: bytes,
    headers: Mapping[str, str],
    seen_ids: set[str],
) -> IngressDecision:
    """Run the synchronous accept/reject decision: signature →
    replay → accept. The receiver should respond 200 within 1s
    and queue the async workflow only on accept."""
    try:
        verify_signature(
            source=source, secret=secret, raw_body=raw_body, headers=headers,
        )
    except WebhookRejected as e:
        return IngressDecision(accepted=False, reason=str(e), delivery_ref=None)

    try:
        ref = extract_delivery_id(source=source, headers=headers)
    except WebhookRejected as e:
        return IngressDecision(accepted=False, reason=str(e), delivery_ref=None)

    if is_replay(ref=ref, seen_ids=seen_ids):
        # Replays return 200 (idempotent) but the workflow doesn't
        # re-fire. Caller stores the decision in its delivery log.
        return IngressDecision(
            accepted=False, reason="replay (already processed)",
            delivery_ref=ref,
        )

    return IngressDecision(accepted=True, reason="ok", delivery_ref=ref)
