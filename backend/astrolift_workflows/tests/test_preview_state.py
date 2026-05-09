"""Tests for PreviewEnvironment state + projection (#81, spec 18 §3)."""

from __future__ import annotations

import pytest

from astrolift_workflows.preview_state import (
    PreviewListFilter,
    PreviewProjection,
    PreviewStateError,
    PreviewStatus,
    assert_transition,
    can_transition,
    filter_previews,
    find_active_for_pr,
    is_active,
    parse_status_filter,
)


def _projection(**overrides) -> PreviewProjection:
    base = dict(
        preview_id=1, guid="gp_abc",
        registered_app_id=1, pr_number=42,
        branch="feat/x", commit_sha="aaa1111",
        status=PreviewStatus.RUNNING,
        hostname="pr-42.acme.platform.example",
        namespace="acme-api-pr-42",
        app_environment_id=10,
        last_deployed_at_unix=1_700_000_000,
        torn_down_at_unix=None,
        workflow_run_id="build-preview-acme-42-x",
    )
    base.update(overrides)
    return PreviewProjection(**base)


# ---- transitions ---------------------------------------------------


def test_building_to_running():
    assert can_transition(
        current=PreviewStatus.BUILDING,
        target=PreviewStatus.RUNNING,
    ) is True


def test_running_to_building_for_redeploy():
    """New SHA on existing preview → re-enters BUILDING."""
    assert can_transition(
        current=PreviewStatus.RUNNING,
        target=PreviewStatus.BUILDING,
    ) is True


def test_failed_to_building_for_retry():
    assert can_transition(
        current=PreviewStatus.FAILED,
        target=PreviewStatus.BUILDING,
    ) is True


def test_torn_down_is_terminal():
    """No transitions out — operator pushes a new commit, a
    new row is created."""
    for target in PreviewStatus:
        assert can_transition(
            current=PreviewStatus.TORN_DOWN, target=target,
        ) is False


def test_assert_transition_raises_on_invalid():
    with pytest.raises(PreviewStateError, match="invalid"):
        assert_transition(
            current=PreviewStatus.TORN_DOWN,
            target=PreviewStatus.RUNNING,
        )


def test_building_can_short_circuit_to_torn_down():
    """Teardown signal mid-build."""
    assert can_transition(
        current=PreviewStatus.BUILDING,
        target=PreviewStatus.TORN_DOWN,
    ) is True


# ---- projection invariants ----------------------------------------


def test_projection_basic():
    p = _projection()
    assert p.pr_number == 42


def test_projection_rejects_zero_pr():
    with pytest.raises(PreviewStateError):
        _projection(pr_number=0)


def test_projection_rejects_empty_guid():
    with pytest.raises(PreviewStateError):
        _projection(guid="")


def test_projection_rejects_empty_namespace():
    with pytest.raises(PreviewStateError):
        _projection(namespace="")


def test_projection_rejects_torn_down_at_on_running():
    """Inconsistency: timestamp set but status is RUNNING."""
    with pytest.raises(PreviewStateError):
        _projection(
            status=PreviewStatus.RUNNING,
            torn_down_at_unix=1_700_000_000,
        )


def test_projection_torn_down_with_timestamp_ok():
    p = _projection(
        status=PreviewStatus.TORN_DOWN,
        torn_down_at_unix=1_700_000_000,
    )
    assert p.status == PreviewStatus.TORN_DOWN


def test_projection_torn_down_without_timestamp_tolerated():
    """Bookkeeping-tail recovery (#87) tolerates this state
    temporarily."""
    p = _projection(
        status=PreviewStatus.TORN_DOWN,
        torn_down_at_unix=None,
    )
    assert p.status == PreviewStatus.TORN_DOWN


# ---- active + uniqueness ------------------------------------------


def test_is_active_running():
    assert is_active(preview=_projection(status=PreviewStatus.RUNNING)) is True


def test_is_active_building():
    assert is_active(preview=_projection(status=PreviewStatus.BUILDING)) is True


def test_is_active_failed():
    """Failed previews still count as active for uniqueness —
    operator might push a fix and revive."""
    assert is_active(preview=_projection(status=PreviewStatus.FAILED)) is True


def test_is_active_torn_down_false():
    assert is_active(preview=_projection(
        status=PreviewStatus.TORN_DOWN,
        torn_down_at_unix=1_700_000_000,
    )) is False


def test_find_active_for_pr_returns_match():
    previews = [
        _projection(preview_id=1, pr_number=42, status=PreviewStatus.RUNNING),
        _projection(preview_id=2, pr_number=99, status=PreviewStatus.RUNNING),
    ]
    found = find_active_for_pr(
        previews=previews, registered_app_id=1, pr_number=42,
    )
    assert found is not None
    assert found.preview_id == 1


def test_find_active_for_pr_skips_torn_down():
    """Old torn-down preview for same PR shouldn't shadow."""
    previews = [
        _projection(
            preview_id=1, pr_number=42,
            status=PreviewStatus.TORN_DOWN,
            torn_down_at_unix=1_700_000_000,
        ),
    ]
    found = find_active_for_pr(
        previews=previews, registered_app_id=1, pr_number=42,
    )
    assert found is None


def test_find_active_for_pr_none_when_absent():
    found = find_active_for_pr(
        previews=[], registered_app_id=1, pr_number=42,
    )
    assert found is None


def test_find_active_for_pr_raises_on_uniqueness_violation():
    """DB partial unique should prevent this; refuse loudly if
    it slipped through."""
    previews = [
        _projection(preview_id=1, pr_number=42, status=PreviewStatus.RUNNING),
        _projection(preview_id=2, pr_number=42, status=PreviewStatus.BUILDING),
    ]
    with pytest.raises(PreviewStateError, match="uniqueness"):
        find_active_for_pr(
            previews=previews, registered_app_id=1, pr_number=42,
        )


# ---- list filter ---------------------------------------------------


def test_filter_previews_by_app():
    previews = [
        _projection(preview_id=1, registered_app_id=1),
        _projection(preview_id=2, registered_app_id=2),
    ]
    out = filter_previews(
        previews=previews,
        f=PreviewListFilter(registered_app_id=1),
    )
    assert {p.preview_id for p in out} == {1}


def test_filter_previews_by_status():
    previews = [
        _projection(
            preview_id=1, status=PreviewStatus.RUNNING,
        ),
        _projection(
            preview_id=2, status=PreviewStatus.TORN_DOWN,
            torn_down_at_unix=1_700_000_000,
        ),
        _projection(
            preview_id=3, status=PreviewStatus.BUILDING,
        ),
    ]
    out = filter_previews(
        previews=previews,
        f=PreviewListFilter(
            registered_app_id=1,
            statuses=(PreviewStatus.RUNNING, PreviewStatus.BUILDING),
        ),
    )
    assert {p.preview_id for p in out} == {1, 3}


def test_filter_previews_empty_status_returns_all():
    """Empty tuple = all statuses (the default for the
    'list everything' UI)."""
    previews = [
        _projection(preview_id=1, status=PreviewStatus.RUNNING),
        _projection(
            preview_id=2, status=PreviewStatus.TORN_DOWN,
            torn_down_at_unix=1_700_000_000,
        ),
    ]
    out = filter_previews(
        previews=previews, f=PreviewListFilter(registered_app_id=1),
    )
    assert len(out) == 2


# ---- parse_status_filter -------------------------------------------


def test_parse_status_filter_basic():
    out = parse_status_filter(raw=["running", "building"])
    assert out == (PreviewStatus.RUNNING, PreviewStatus.BUILDING)


def test_parse_status_filter_dedupes():
    out = parse_status_filter(raw=["running", "running"])
    assert out == (PreviewStatus.RUNNING,)


def test_parse_status_filter_unknown_rejected():
    with pytest.raises(PreviewStateError, match="unknown"):
        parse_status_filter(raw=["running", "exploded"])


def test_parse_status_filter_empty_passes():
    """Empty tuple is a valid 'no filter' value."""
    assert parse_status_filter(raw=[]) == ()
