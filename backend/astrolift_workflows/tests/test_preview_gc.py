"""Tests for preview environment GC policy (#88, spec 18 §14-15)."""

from __future__ import annotations

import pytest

from astrolift_workflows.preview_gc import (
    DEFAULT_PREVIEW_MAX_ACTIVE,
    DEFAULT_PREVIEW_TTL_DAYS,
    GC_INTERVAL_SECONDS,
    EvictionDecision,
    EvictionReason,
    PreviewGcError,
    PreviewSnapshot,
    admission_for_new_preview,
    evaluate_app_evictions,
    evaluate_max_active_evictions,
    evaluate_ttl_evictions,
    is_eligible_for_gc,
    is_ttl_expired,
    pr_comment_for_eviction,
)

DAY = 86400


def _preview(
    preview_id: int = 1,
    *,
    pinned: bool = False,
    state: str = "running",
    age_days: float = 0,
    pr: int = 1,
    now_unix: int = 1_700_000_000,
) -> PreviewSnapshot:
    return PreviewSnapshot(
        preview_id=preview_id,
        app_id=1,
        pr_number=pr,
        is_pinned=pinned,
        state=state,
        last_deployed_at_unix=int(now_unix - age_days * DAY),
    )


# ---- defaults locked -----------------------------------------------


def test_defaults_locked():
    """Spec §14 says 14 days TTL, §15 has 5 max active default,
    GC interval 15 minutes. Lock the constants so changing them
    requires a deliberate review."""
    assert DEFAULT_PREVIEW_TTL_DAYS == 14
    assert DEFAULT_PREVIEW_MAX_ACTIVE == 5
    assert GC_INTERVAL_SECONDS == 15 * 60


# ---- eligibility ---------------------------------------------------


def test_running_unpinned_eligible():
    p = _preview()
    assert is_eligible_for_gc(preview=p) is True


def test_pinned_skipped():
    """Operator-pinned previews bypass GC."""
    p = _preview(pinned=True, age_days=30)
    assert is_eligible_for_gc(preview=p) is False


@pytest.mark.parametrize("state", ["pending", "failed", "torn_down"])
def test_non_running_skipped(state):
    """Pending = mid-build, failed/torn_down = already-handled.
    GC stays out of those lifecycles."""
    p = _preview(state=state, age_days=30)
    assert is_eligible_for_gc(preview=p) is False


# ---- TTL expiry ----------------------------------------------------


def test_ttl_not_expired_at_zero_age():
    now = 1_700_000_000
    p = _preview(age_days=0, now_unix=now)
    assert is_ttl_expired(preview=p, now_unix=now) is False


def test_ttl_expired_after_default_ttl():
    now = 1_700_000_000
    p = _preview(age_days=DEFAULT_PREVIEW_TTL_DAYS + 1, now_unix=now)
    assert is_ttl_expired(preview=p, now_unix=now) is True


def test_ttl_boundary_inclusive():
    """Exactly at TTL = expired (>=, not >). Avoids a 1-second
    window where we'd skip an obviously-expired preview."""
    now = 1_700_000_000
    p = _preview(age_days=DEFAULT_PREVIEW_TTL_DAYS, now_unix=now)
    assert is_ttl_expired(preview=p, now_unix=now) is True


def test_ttl_custom_value():
    now = 1_700_000_000
    p = _preview(age_days=3, now_unix=now)
    assert is_ttl_expired(preview=p, now_unix=now, ttl_days=2) is True
    assert is_ttl_expired(preview=p, now_unix=now, ttl_days=7) is False


def test_ttl_zero_rejected():
    """TTL=0 would mean evict everything immediately. Refuse —
    operator probably typo'd the config."""
    now = 1_700_000_000
    with pytest.raises(PreviewGcError):
        is_ttl_expired(
            preview=_preview(now_unix=now),
            now_unix=now,
            ttl_days=0,
        )


# ---- TTL evaluation ------------------------------------------------


def test_evaluate_ttl_skips_pinned():
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=20, now_unix=now),
        _preview(2, age_days=20, pinned=True, now_unix=now),
    ]
    decisions = evaluate_ttl_evictions(previews=previews, now_unix=now)
    assert [d.preview_id for d in decisions] == [1]
    assert decisions[0].reason == EvictionReason.TTL_EXPIRED


def test_evaluate_ttl_skips_young():
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=2, now_unix=now),
        _preview(2, age_days=20, now_unix=now),
    ]
    decisions = evaluate_ttl_evictions(previews=previews, now_unix=now)
    assert [d.preview_id for d in decisions] == [2]


def test_evaluate_ttl_returns_all_expired():
    now = 1_700_000_000
    previews = [_preview(i, age_days=20, now_unix=now) for i in (1, 2, 3)]
    decisions = evaluate_ttl_evictions(previews=previews, now_unix=now)
    assert {d.preview_id for d in decisions} == {1, 2, 3}


# ---- max-active evaluation -----------------------------------------


def test_max_active_under_limit_no_eviction():
    """Spec §15: only kicks in when count > max_active."""
    previews = [_preview(i, age_days=1) for i in (1, 2, 3)]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=5)
    assert decisions == ()


def test_max_active_at_limit_no_eviction():
    """Boundary: count == max_active is fine."""
    previews = [_preview(i, age_days=1) for i in (1, 2, 3, 4, 5)]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=5)
    assert decisions == ()


def test_max_active_over_limit_evicts_oldest():
    """Spec §15: evict by oldest last_deployed_at."""
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=10, now_unix=now),  # oldest
        _preview(2, age_days=5, now_unix=now),
        _preview(3, age_days=2, now_unix=now),
    ]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=2)
    assert len(decisions) == 1
    assert decisions[0].preview_id == 1
    assert decisions[0].reason == EvictionReason.MAX_ACTIVE_EXCEEDED


def test_max_active_pinned_skipped():
    """Pinned previews count toward limit but aren't eviction
    candidates. If oldest is pinned, evict next-oldest."""
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=10, pinned=True, now_unix=now),  # pinned, oldest
        _preview(2, age_days=5, now_unix=now),  # next oldest
        _preview(3, age_days=2, now_unix=now),
    ]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=2)
    assert [d.preview_id for d in decisions] == [2]


def test_max_active_all_pinned_no_eviction():
    """Spec §15 footnote: pinning > cap. If all running previews
    are pinned, GC can't evict anything."""
    now = 1_700_000_000
    previews = [_preview(i, age_days=10, pinned=True, now_unix=now) for i in (1, 2, 3)]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=1)
    assert decisions == ()


def test_max_active_excludes_non_running():
    """Failed/pending previews don't take cluster resources;
    don't count them toward the cap."""
    now = 1_700_000_000
    previews = [
        _preview(1, state="failed", age_days=10, now_unix=now),
        _preview(2, age_days=5, now_unix=now),
        _preview(3, age_days=2, now_unix=now),
    ]
    decisions = evaluate_max_active_evictions(previews=previews, max_active=2)
    assert decisions == ()


def test_max_active_zero_rejected():
    with pytest.raises(PreviewGcError):
        evaluate_max_active_evictions(previews=(), max_active=0)


# ---- combined evaluation -------------------------------------------


def test_combined_ttl_first_then_max_active():
    """TTL fires first; max-active runs over survivors. Avoids
    over-evicting (same preview classified twice)."""
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=20, now_unix=now),  # TTL expired
        _preview(2, age_days=10, now_unix=now),  # would be max-active oldest
        _preview(3, age_days=5, now_unix=now),
        _preview(4, age_days=2, now_unix=now),
    ]
    decisions = evaluate_app_evictions(
        previews=previews,
        now_unix=now,
        max_active=2,
    )
    # Preview 1 evicted by TTL. Then survivors = [2,3,4] which
    # is over max_active=2, so oldest survivor (#2) gets evicted
    # by max_active.
    by_reason = {d.preview_id: d.reason for d in decisions}
    assert by_reason[1] == EvictionReason.TTL_EXPIRED
    assert by_reason[2] == EvictionReason.MAX_ACTIVE_EXCEEDED
    assert 3 not in by_reason and 4 not in by_reason


def test_combined_no_double_eviction():
    """Even if TTL eviction takes count below max_active, no
    extra evictions happen."""
    now = 1_700_000_000
    previews = [
        _preview(1, age_days=30, now_unix=now),
        _preview(2, age_days=30, now_unix=now),
        _preview(3, age_days=30, now_unix=now),
    ]
    decisions = evaluate_app_evictions(
        previews=previews,
        now_unix=now,
        max_active=2,
    )
    assert all(d.reason == EvictionReason.TTL_EXPIRED for d in decisions)
    assert len(decisions) == 3


# ---- admission -----------------------------------------------------


def test_admission_under_limit():
    """New PR; existing < max → straight admit."""
    existing = [_preview(i) for i in (1, 2, 3)]
    decision = admission_for_new_preview(
        existing_previews=existing,
        max_active=5,
    )
    assert decision.admit is True
    assert decision.eviction is None


def test_admission_at_limit_evicts_oldest():
    """Existing == max → admit + evict oldest."""
    now = 1_700_000_000
    existing = [
        _preview(1, age_days=10, now_unix=now),
        _preview(2, age_days=5, now_unix=now),
        _preview(3, age_days=2, now_unix=now),
    ]
    decision = admission_for_new_preview(
        existing_previews=existing,
        max_active=3,
    )
    assert decision.admit is True
    assert decision.eviction is not None
    assert decision.eviction.preview_id == 1
    assert decision.eviction.reason == EvictionReason.MAX_ACTIVE_EXCEEDED


def test_admission_all_pinned_admits_no_eviction():
    """Spec §15 footnote: pinning > cap."""
    now = 1_700_000_000
    existing = [_preview(i, age_days=10, pinned=True, now_unix=now) for i in (1, 2, 3)]
    decision = admission_for_new_preview(
        existing_previews=existing,
        max_active=3,
    )
    assert decision.admit is True
    assert decision.eviction is None


def test_admission_pinned_oldest_skips_to_next_oldest():
    now = 1_700_000_000
    existing = [
        _preview(1, age_days=10, pinned=True, now_unix=now),
        _preview(2, age_days=5, now_unix=now),
        _preview(3, age_days=2, now_unix=now),
    ]
    decision = admission_for_new_preview(
        existing_previews=existing,
        max_active=3,
    )
    assert decision.eviction is not None
    assert decision.eviction.preview_id == 2


def test_admission_excludes_non_running():
    """Failed previews don't count against admission."""
    now = 1_700_000_000
    existing = [
        _preview(1, state="failed", now_unix=now),
        _preview(2, state="failed", now_unix=now),
        _preview(3, age_days=1, now_unix=now),
    ]
    decision = admission_for_new_preview(
        existing_previews=existing,
        max_active=2,
    )
    assert decision.eviction is None


# ---- PR comment text -----------------------------------------------


def test_pr_comment_ttl_expired():
    body = pr_comment_for_eviction(
        decision=EvictionDecision(
            preview_id=1,
            reason=EvictionReason.TTL_EXPIRED,
        ),
    )
    assert "expired" in body.lower()
    assert "Push to this PR" in body


def test_pr_comment_ttl_with_redeploy_link():
    body = pr_comment_for_eviction(
        decision=EvictionDecision(
            preview_id=1,
            reason=EvictionReason.TTL_EXPIRED,
        ),
        redeploy_url="https://app.platform/redeploy/1",
    )
    assert "https://app.platform/redeploy/1" in body


def test_pr_comment_max_active_names_new_pr():
    """Spec acceptance: redeploy link + name of the PR that
    pushed this one out."""
    body = pr_comment_for_eviction(
        decision=EvictionDecision(
            preview_id=1,
            reason=EvictionReason.MAX_ACTIVE_EXCEEDED,
        ),
        new_pr_number=42,
        redeploy_url="https://app.platform/redeploy/1",
    )
    assert "#42" in body
    assert "https://app.platform/redeploy/1" in body


def test_pr_comment_max_active_no_new_pr():
    """No new_pr_number when called from scheduled GC (not
    admission-driven)."""
    body = pr_comment_for_eviction(
        decision=EvictionDecision(
            preview_id=1,
            reason=EvictionReason.MAX_ACTIVE_EXCEEDED,
        ),
    )
    assert "#" not in body or "free a slot" in body
