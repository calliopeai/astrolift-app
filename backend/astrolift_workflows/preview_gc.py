"""
Preview environment garbage collection policy (#88, spec 18 §14-15).

Pure-Python policy. ``PreviewGarbageCollectWorkflow`` runs every
15 minutes and consults this module for two cost-containment
rules:

* **TTL eviction** — previews older than ``preview_ttl``
  (default 14 days) are torn down even if the PR is still open.
* **Max-active eviction** — when an org would exceed
  ``preview_max_active``, evict the oldest by
  ``last_deployed_at`` to make room.

Pinned previews bypass both rules (operator opt-in via
``astro app previews pin``). Returns the eviction REASON so
the PR-comment activity can render the right message
(redeploy link for max-active, 'expired' for TTL).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class PreviewGcError(ValueError):
    pass


DEFAULT_PREVIEW_TTL_DAYS = 14
"""Spec §14 default. Org overrides via app.preview_config.ttl_days."""

DEFAULT_PREVIEW_MAX_ACTIVE = 5
"""Per-app cap so a single project doesn't eat the cluster."""

GC_INTERVAL_SECONDS = 15 * 60
"""Spec §15: GC runs every 15 minutes."""


class EvictionReason(str, Enum):
    """Why GC chose this preview for teardown."""

    TTL_EXPIRED = "ttl_expired"
    """Preview older than preview_ttl. PR comment says
    'preview expired after N days'."""

    MAX_ACTIVE_EXCEEDED = "max_active_exceeded"
    """Newer PR opened; this preview is the oldest. PR comment
    says 'evicted to make room for #<new_pr>; click here to
    redeploy'."""


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewSnapshot:
    """Minimum projection of a PreviewEnvironment row the GC
    policy needs."""

    preview_id: int
    app_id: int
    pr_number: int
    is_pinned: bool
    state: str
    """Lifecycle state: 'pending', 'running', 'failed',
    'torn_down'. GC only considers running previews; the
    others are either still building or already gone."""

    last_deployed_at_unix: int
    """Most recent deploy. TTL is measured from this; max-active
    eviction picks the smallest value as oldest."""


def is_eligible_for_gc(*, preview: PreviewSnapshot) -> bool:
    """Pinned + non-running previews skip GC consideration."""
    if preview.is_pinned:
        return False
    if preview.state != "running":
        # 'pending' is mid-build, 'failed' is already in error
        # state and gets cleaned by the failure flow, 'torn_down'
        # is gone.
        return False
    return True


def is_ttl_expired(
    *,
    preview: PreviewSnapshot,
    now_unix: int,
    ttl_days: int = DEFAULT_PREVIEW_TTL_DAYS,
) -> bool:
    """True if preview's age exceeds ttl_days."""
    if ttl_days <= 0:
        raise PreviewGcError(f"ttl_days must be > 0, got {ttl_days}")
    age_seconds = now_unix - preview.last_deployed_at_unix
    return age_seconds >= ttl_days * 86400


@dataclasses.dataclass(frozen=True, slots=True)
class EvictionDecision:
    preview_id: int
    reason: EvictionReason


def evaluate_ttl_evictions(
    *,
    previews: Sequence[PreviewSnapshot],
    now_unix: int,
    ttl_days: int = DEFAULT_PREVIEW_TTL_DAYS,
) -> tuple[EvictionDecision, ...]:
    """Spec §14: every preview older than ttl_days gets evicted."""
    return tuple(
        EvictionDecision(
            preview_id=p.preview_id,
            reason=EvictionReason.TTL_EXPIRED,
        )
        for p in previews
        if is_eligible_for_gc(preview=p)
        and is_ttl_expired(
            preview=p, now_unix=now_unix, ttl_days=ttl_days,
        )
    )


def evaluate_max_active_evictions(
    *,
    previews: Sequence[PreviewSnapshot],
    max_active: int = DEFAULT_PREVIEW_MAX_ACTIVE,
) -> tuple[EvictionDecision, ...]:
    """Spec §15: when active preview count exceeds max_active,
    evict the oldest by last_deployed_at until count == max_active.

    Pinned previews count toward the limit (the operator
    explicitly chose to keep them) but aren't candidates for
    eviction. If pinned previews alone exceed the limit, no
    further eviction happens — the operator's pinning takes
    precedence over the cap.
    """
    if max_active <= 0:
        raise PreviewGcError(
            f"max_active must be > 0, got {max_active}"
        )

    # Only count running previews; pending/failed don't take
    # active resources at the cluster level (they're either
    # mid-build or already cleaned up).
    active = [p for p in previews if p.state == "running"]
    if len(active) <= max_active:
        return ()

    candidates = [p for p in active if not p.is_pinned]
    candidates.sort(key=lambda p: p.last_deployed_at_unix)

    # We need to evict (count - max_active) previews; pinned
    # previews are off-limits.
    surplus = len(active) - max_active
    to_evict = candidates[:surplus]

    return tuple(
        EvictionDecision(
            preview_id=p.preview_id,
            reason=EvictionReason.MAX_ACTIVE_EXCEEDED,
        )
        for p in to_evict
    )


def evaluate_app_evictions(
    *,
    previews: Sequence[PreviewSnapshot],
    now_unix: int,
    ttl_days: int = DEFAULT_PREVIEW_TTL_DAYS,
    max_active: int = DEFAULT_PREVIEW_MAX_ACTIVE,
) -> tuple[EvictionDecision, ...]:
    """Combined evaluation for one app's previews. TTL fires
    first (more meaningful reason for the PR comment); max-active
    runs over what's left.

    Caller pre-filters to one app_id. Multiple apps in one call
    would over-evict because max_active is per-app.
    """
    ttl_decisions = evaluate_ttl_evictions(
        previews=previews, now_unix=now_unix, ttl_days=ttl_days,
    )
    ttl_evicted_ids = {d.preview_id for d in ttl_decisions}

    survivors = tuple(
        p for p in previews if p.preview_id not in ttl_evicted_ids
    )
    max_decisions = evaluate_max_active_evictions(
        previews=survivors, max_active=max_active,
    )
    return ttl_decisions + max_decisions


# ---- pre-deploy max-active gate ------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Returned by the BuildPreviewWorkflow's pre-flight gate.
    Either admit (room exists) or admit-with-eviction (oldest
    has to go to make room)."""

    admit: bool
    """Always true when admit_with_eviction is non-None — a new
    PR isn't denied, but the oldest is evicted to make room."""

    eviction: EvictionDecision | None


def admission_for_new_preview(
    *,
    existing_previews: Sequence[PreviewSnapshot],
    max_active: int = DEFAULT_PREVIEW_MAX_ACTIVE,
) -> AdmissionDecision:
    """Decide what BuildPreviewWorkflow should do when a new PR
    arrives. Triggered before the new preview row is inserted.

    Three outcomes:
    1. Existing < max → admit, no eviction.
    2. Existing == max with at least one non-pinned → admit, evict
       oldest non-pinned (returned in ``eviction``).
    3. Existing >= max but ALL pinned → admit anyway (operator's
       pin choice wins over the cap; spec §15 footnote).
    """
    if max_active <= 0:
        raise PreviewGcError(
            f"max_active must be > 0, got {max_active}"
        )

    active = [p for p in existing_previews if p.state == "running"]
    if len(active) < max_active:
        return AdmissionDecision(admit=True, eviction=None)

    non_pinned = [p for p in active if not p.is_pinned]
    if not non_pinned:
        # All slots pinned. Admit anyway — operator's pin choices
        # are stronger than the cap.
        return AdmissionDecision(admit=True, eviction=None)

    non_pinned.sort(key=lambda p: p.last_deployed_at_unix)
    oldest = non_pinned[0]
    return AdmissionDecision(
        admit=True,
        eviction=EvictionDecision(
            preview_id=oldest.preview_id,
            reason=EvictionReason.MAX_ACTIVE_EXCEEDED,
        ),
    )


# ---- PR comment text -----------------------------------------------


def pr_comment_for_eviction(
    *,
    decision: EvictionDecision,
    new_pr_number: int = 0,
    redeploy_url: str = "",
) -> str:
    """Render the body of the PR comment that gets posted when
    a preview is evicted. Two reasons → two messages.

    ``new_pr_number``: relevant for MAX_ACTIVE_EXCEEDED so the
    comment can name the PR that pushed this one out.
    ``redeploy_url``: link the operator clicks to bring the
    preview back.
    """
    if decision.reason == EvictionReason.TTL_EXPIRED:
        body = (
            "**Preview environment expired**\n\n"
            "This preview was torn down because it exceeded "
            "the platform's preview TTL. Push to this PR to "
            "trigger a fresh build."
        )
        if redeploy_url:
            body += f"\n\n[Redeploy now]({redeploy_url})"
        return body

    if decision.reason == EvictionReason.MAX_ACTIVE_EXCEEDED:
        prefix = "**Preview environment evicted**\n\n"
        if new_pr_number:
            prefix += (
                f"This preview was torn down to make room for "
                f"#{new_pr_number}. "
            )
        else:
            prefix += "This preview was torn down to free a slot. "
        prefix += "Push or redeploy to bring it back."
        if redeploy_url:
            prefix += f"\n\n[Redeploy now]({redeploy_url})"
        return prefix

    raise PreviewGcError(
        f"unknown eviction reason {decision.reason!r}"
    )
