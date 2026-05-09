"""
Approval workflow policy + magic-link tokens (#125, spec 06 §4.6).

Pure-Python policy. ``ApprovalWorkflow`` (Temporal) consults this
for token mint/verify, signal counting, and timeout decisions.
The workflow itself is the long-running thing that holds a deploy
in ``pending_approval`` until enough approvers respond or the
timeout fires.

Three pieces:

* **Magic-link tokens** — single-use, time-limited, hash-at-rest.
  Plaintext returned exactly once on mint; storage carries only
  the SHA-256 hash. Verifier looks up by hash + checks
  expiry/used flags.
* **Quorum counting** — approvers vote via signal; we track
  who has approved/rejected. Reaching ``min_approvers``
  approvals advances the workflow; ANY rejection short-circuits
  to failure (one nay from a required approver kills the deploy).
* **Timeout policy** — default 7 days; configurable per env. On
  timeout, deployment moves to ``failed`` with reason
  ``approval_timeout``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum


# Spec 06 §4.6 default. Operators override per environment via
# AppEnvironment.approval_timeout_seconds.
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 7 * 24 * 3600


# Magic-link tokens follow the same pattern as deploy_tokens (#143):
# alft_<kind>_ prefix + base64url payload, hash-at-rest.
MAGIC_LINK_PREFIX = "alft_ml_"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


# ---- magic link tokens ---------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedMagicLink:
    """Output of ``mint_magic_link``. Plaintext returned exactly
    once — caller emails/Slacks/webhooks it to the approver."""

    plaintext_token: str
    token_hash: str
    last4: str
    expires_at: datetime


def mint_magic_link(
    *,
    now: datetime,
    ttl_seconds: int = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> IssuedMagicLink:
    """Mint a single-use, time-limited approval token. Caller
    persists ``token_hash`` + ``expires_at`` on the row;
    plaintext_token is delivered to the approver and discarded
    by us."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    plaintext = MAGIC_LINK_PREFIX + secrets.token_urlsafe(32)
    return IssuedMagicLink(
        plaintext_token=plaintext,
        token_hash=_hash(plaintext),
        last4=plaintext[-4:],
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class MagicLinkRecord:
    """Persisted state for one magic link."""

    token_hash: str
    approver_user_id: int
    expires_at: datetime
    consumed_at: datetime | None = None


class MagicLinkInvalid(Exception):
    """Verification failed. Caller responds 401 with a generic
    message — never leak which check fired."""


def verify_magic_link(
    *,
    presented_plaintext: str,
    record: MagicLinkRecord,
    now: datetime,
) -> int:
    """Verify the presented token against the record. Returns the
    approver's user_id on success.

    Order: hash match -> not consumed -> not expired. Each step
    raises with the same message so an attacker can't probe
    'wrong token' vs 'already used' vs 'expired' via timing.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    if not presented_plaintext.startswith(MAGIC_LINK_PREFIX):
        raise MagicLinkInvalid("token invalid")

    presented_hash = _hash(presented_plaintext)
    # secrets.compare_digest avoids timing leaks
    if not secrets.compare_digest(presented_hash, record.token_hash):
        raise MagicLinkInvalid("token invalid")

    if record.consumed_at is not None:
        raise MagicLinkInvalid("token invalid")

    if now >= record.expires_at:
        raise MagicLinkInvalid("token invalid")

    return record.approver_user_id


# ---- quorum counting ------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalVote:
    """One signal received from an approver."""

    user_id: int
    decision: ApprovalDecision
    """APPROVED or REJECTED only — PENDING is not a vote;
    TIMEOUT is workflow-emitted."""

    reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class QuorumState:
    """Output of evaluating votes against quorum policy."""

    decision: ApprovalDecision
    approvers_for: tuple[int, ...]
    approvers_against: tuple[int, ...]
    reason: str


class QuorumError(ValueError):
    pass


def evaluate_quorum(
    *,
    votes: Sequence[ApprovalVote],
    eligible_approvers: Sequence[int],
    min_approvers: int,
) -> QuorumState:
    """Tally votes and decide.

    Rules:
      * ANY rejection from an eligible approver -> REJECTED
        (one nay kills the deploy; no override path).
      * ``min_approvers`` distinct eligible approvers in favor
        -> APPROVED.
      * Otherwise PENDING.

    Votes from non-eligible users are ignored — workflow signal
    handler should reject those upstream, but defense in depth.
    """
    if min_approvers <= 0:
        raise QuorumError("min_approvers must be positive")
    if min_approvers > len(eligible_approvers):
        raise QuorumError(
            f"min_approvers ({min_approvers}) exceeds eligible "
            f"approver count ({len(eligible_approvers)})"
        )

    eligible = set(eligible_approvers)
    approvers_for: list[int] = []
    approvers_against: list[int] = []

    # De-duplicate: latest vote per user wins. Iterate in order;
    # later overwrites earlier.
    seen: dict[int, ApprovalDecision] = {}
    reasons: dict[int, str] = {}
    for v in votes:
        if v.user_id not in eligible:
            continue
        if v.decision in (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED):
            seen[v.user_id] = v.decision
            reasons[v.user_id] = v.reason

    for user_id, decision in seen.items():
        if decision == ApprovalDecision.APPROVED:
            approvers_for.append(user_id)
        elif decision == ApprovalDecision.REJECTED:
            approvers_against.append(user_id)

    if approvers_against:
        first_rejector = approvers_against[0]
        return QuorumState(
            decision=ApprovalDecision.REJECTED,
            approvers_for=tuple(approvers_for),
            approvers_against=tuple(approvers_against),
            reason=(
                f"rejected by user {first_rejector}: "
                f"{reasons.get(first_rejector, '')}"
            ).rstrip(": "),
        )

    if len(approvers_for) >= min_approvers:
        return QuorumState(
            decision=ApprovalDecision.APPROVED,
            approvers_for=tuple(approvers_for),
            approvers_against=(),
            reason=f"approved by {len(approvers_for)} of {min_approvers} required",
        )

    return QuorumState(
        decision=ApprovalDecision.PENDING,
        approvers_for=tuple(approvers_for),
        approvers_against=(),
        reason=(
            f"pending: {len(approvers_for)} of {min_approvers} approvals received"
        ),
    )


# ---- timeout decision ----------------------------------------------


def is_timed_out(
    *, requested_at: datetime, now: datetime,
    timeout_seconds: int = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> bool:
    """Has the approval window elapsed? Workflow polls/awaits
    with this; on True it transitions the deployment to failed
    with reason ``approval_timeout``."""
    if requested_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return (now - requested_at).total_seconds() >= timeout_seconds
