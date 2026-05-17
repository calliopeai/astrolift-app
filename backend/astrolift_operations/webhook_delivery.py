"""
Webhook delivery pipeline policy (#37, spec 17 §6-§7).

Pure-Python module covering the bits of the delivery pipeline the
DeliverWebhookWorkflow consults:

* **HMAC-SHA256 signing** — payload signed as
  ``HMAC-SHA256(secret, "<timestamp>.<raw_body>")``.
* **Headers builder** — every delivery carries the spec 17 §6.1
  envelope (Content-Type, X-Astrolift-Event-Type, etc.).
* **Retry schedule** — 8 attempts on jittered exponential backoff
  (5s, 30s, 2m, 10m, 30m, 2h, 6h, 24h).
* **Response classifier** — maps HTTP status / connection errors
  to one of {SUCCESS, RETRY, PERMANENT, IMMEDIATE_DISABLE}. The
  workflow consumes the classification to decide what to do.

The actual HTTP POST + the per-subscription concurrency limiter
live in workers; this module is the policy + crypto layer.
``record_delivery_outcome`` from astrolift_operations/delivery.py
(#161) handles the auto-disable bookkeeping after 50 transient
failures — that wiring stays unchanged.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import random
from enum import StrEnum

# Spec 17 §6.3 retry schedule, in order. 8 attempts max.
RETRY_SCHEDULE_SECONDS: tuple[int, ...] = (
    5,
    30,
    2 * 60,
    10 * 60,
    30 * 60,
    2 * 3600,
    6 * 3600,
    24 * 3600,
)
MAX_ATTEMPTS = len(RETRY_SCHEDULE_SECONDS) + 1  # initial + retries

# Spec 17 §7 — per-subscription concurrency limit.
MAX_INFLIGHT_PER_SUBSCRIPTION = 100


class DeliveryClassification(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    PERMANENT_FAILURE = "permanent_failure"
    IMMEDIATE_DISABLE = "immediate_disable"


# ---- signing --------------------------------------------------------


def sign_payload(
    *,
    secret: bytes,
    timestamp_unix: int,
    raw_body: bytes,
) -> str:
    """Return ``"sha256=<hex>"`` per spec 17 §6.1.

    Signed input is ``"<timestamp>.<raw_body>"`` so the verifier can
    detect both tampering AND replays beyond a freshness window
    (verifier checks ``abs(now - timestamp) <= window``)."""
    if not secret:
        raise ValueError("secret is required")
    if timestamp_unix <= 0:
        raise ValueError("timestamp_unix must be positive")
    signing_input = f"{timestamp_unix}.".encode("ascii") + raw_body
    digest = hmac.new(secret, signing_input, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    *,
    secret: bytes,
    timestamp_unix: int,
    raw_body: bytes,
    presented: str,
    freshness_window_seconds: int = 300,
    now_unix: int | None = None,
    previous_secret: bytes | None = None,
) -> bool:
    """Constant-time signature check + freshness window. Returns
    True only when both pass — caller turns False into 401.

    ``previous_secret`` lets the platform honour a rotated-out
    secret during its grace window (#426). When set, the current
    secret is checked first; on mismatch the previous secret is
    checked. The caller is responsible for enforcing the grace
    timeout (only pass the previous secret while it's still
    within window) — this layer just does the crypto.
    """
    if presented is None or not presented.startswith("sha256="):
        return False
    if now_unix is not None:
        if abs(now_unix - timestamp_unix) > freshness_window_seconds:
            return False
    expected = sign_payload(
        secret=secret,
        timestamp_unix=timestamp_unix,
        raw_body=raw_body,
    )
    if hmac.compare_digest(expected, presented):
        return True
    if previous_secret:
        expected_prev = sign_payload(
            secret=previous_secret,
            timestamp_unix=timestamp_unix,
            raw_body=raw_body,
        )
        return hmac.compare_digest(expected_prev, presented)
    return False


# ---- headers --------------------------------------------------------


def build_headers(
    *,
    event_type: str,
    event_id: str,
    delivery_id: str,
    schema_version: str,
    signature: str,
    timestamp_unix: int,
    user_agent: str = "astrolift-platform/1",
    content_type: str = "application/json",
) -> dict[str, str]:
    """Build the spec 17 §6.1 header envelope. The workflow merges
    these with whatever the worker's HTTP client adds (Host, etc.)."""
    return {
        "Content-Type": content_type,
        "X-Astrolift-Event-Type": event_type,
        "X-Astrolift-Event-Id": event_id,
        "X-Astrolift-Delivery-Id": delivery_id,
        "X-Astrolift-Signature": signature,
        "X-Astrolift-Timestamp": str(timestamp_unix),
        "X-Astrolift-Schema": schema_version,
        "User-Agent": user_agent,
    }


# ---- retry schedule -------------------------------------------------


def next_retry_delay_seconds(
    *,
    attempt: int,
    jitter_ratio: float = 0.2,
    rng: random.Random | None = None,
) -> int | None:
    """Return the delay before attempt N+1 (where N is the just-failed
    attempt, 1-indexed). Returns ``None`` when no more retries are
    permitted (attempt >= MAX_ATTEMPTS).

    Jitter is ±``jitter_ratio`` of the base delay so 1000 simultaneous
    deliveries to a flapping endpoint don't dogpile on the same retry
    second.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if attempt >= MAX_ATTEMPTS:
        return None
    # attempt=1 just failed → use schedule[0] (5s) before attempt 2.
    base = RETRY_SCHEDULE_SECONDS[attempt - 1]
    rng = rng or random
    jitter = base * jitter_ratio
    spread = rng.uniform(-jitter, jitter)
    return max(1, int(base + spread))


# ---- response classifier --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryAttemptResult:
    """One attempt's outcome the worker hands to ``classify``.

    ``status_code`` is None when a connection error / timeout
    prevented us from getting an HTTP response at all.
    """

    status_code: int | None
    connection_error: str = ""


def classify(result: DeliveryAttemptResult) -> DeliveryClassification:
    """Map an attempt outcome to one of the classification states.

    Rules (spec 17 §6.3):
      - 2xx          → SUCCESS
      - 410 Gone     → IMMEDIATE_DISABLE (permanent: subscriber
        explicitly says 'don't contact me')
      - 4xx (other)  → PERMANENT_FAILURE (retrying won't help; e.g.
        401, 404, 422)
      - 5xx          → RETRY (transient)
      - connection error / timeout → RETRY (transient)
    """
    if result.status_code is None:
        return DeliveryClassification.RETRY
    code = result.status_code
    if 200 <= code < 300:
        return DeliveryClassification.SUCCESS
    if code == 410:
        return DeliveryClassification.IMMEDIATE_DISABLE
    if 400 <= code < 500:
        return DeliveryClassification.PERMANENT_FAILURE
    return DeliveryClassification.RETRY


# ---- in-flight rate limit -------------------------------------------


def can_send(*, current_inflight: int) -> bool:
    """Per-subscription concurrency check. Worker consults before
    starting a new attempt."""
    return current_inflight < MAX_INFLIGHT_PER_SUBSCRIPTION
