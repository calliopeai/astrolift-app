"""Tests for preview policy (#92, #94, #96 — spec 18 §2/§6/§9)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.preview import (
    DEFAULTS,
    PreviewConfig,
    PreviewHostnameError,
    PreviewSnapshot,
    merge_preview_config,
    preview_hostname,
    previews_to_evict,
    wildcard_zone_for_org,
)


# ---- hostname pattern ----------------------------------------------


def test_single_workload_hostname():
    out = preview_hostname(
        pr_number=42, app_slug="api", org_slug="acme",
        platform_domain="myastrolift.net",
    )
    assert out == "pr-42-api.pr.acme.myastrolift.net"


def test_multi_workload_hostname():
    out = preview_hostname(
        pr_number=42, app_slug="api", workload="web",
        org_slug="acme", platform_domain="myastrolift.net",
    )
    assert out == "pr-42-api-web.pr.acme.myastrolift.net"


def test_hostname_rejects_long_label():
    """The leftmost DNS label has a 63-char cap. Long app/workload
    names hit it."""
    with pytest.raises(PreviewHostnameError, match="exceeds 63 chars"):
        preview_hostname(
            pr_number=99,
            app_slug="a" * 80,
            org_slug="acme",
            platform_domain="myastrolift.net",
        )


def test_hostname_rejects_zero_pr_number():
    with pytest.raises(PreviewHostnameError, match="positive"):
        preview_hostname(
            pr_number=0, app_slug="api", org_slug="acme",
            platform_domain="x.com",
        )


def test_hostname_rejects_empty_required_fields():
    with pytest.raises(PreviewHostnameError):
        preview_hostname(
            pr_number=1, app_slug="", org_slug="acme",
            platform_domain="x.com",
        )


def test_wildcard_zone_format():
    out = wildcard_zone_for_org(
        org_slug="acme", platform_domain="myastrolift.net",
    )
    assert out == "*.pr.acme.myastrolift.net"


# ---- configuration --------------------------------------------------


def test_defaults_match_spec_18():
    """Cost containment: previews are time-boxed (auto_teardown),
    bounded (max_active=5), and isolated by default
    (share_main_secrets=False)."""
    assert DEFAULTS.enabled is True
    assert DEFAULTS.max_active == 5
    assert DEFAULTS.auto_teardown_on_close is True
    assert DEFAULTS.auto_redeploy_on_push is True
    assert DEFAULTS.share_main_secrets is False


def test_config_rejects_invalid_max_active():
    with pytest.raises(ValueError, match="max_active"):
        PreviewConfig(max_active=0)
    with pytest.raises(ValueError, match="max_active"):
        PreviewConfig(max_active=101)


def test_merge_org_defaults_beat_platform_defaults():
    out = merge_preview_config(
        org_defaults={"max_active": 10},
        app_overrides=None,
    )
    assert out.max_active == 10


def test_merge_app_overrides_beat_org_defaults():
    out = merge_preview_config(
        org_defaults={"max_active": 10},
        app_overrides={"max_active": 3},
    )
    assert out.max_active == 3


def test_merge_ignores_unknown_keys():
    """Defensive: typos in admin UI shouldn't raise; just get
    silently dropped. The dataclass validation handles real
    invalid values."""
    out = merge_preview_config(
        org_defaults={"max_active": 8, "typo_key": "ignored"},
        app_overrides={"unknown_field": True},
    )
    assert out.max_active == 8


def test_merge_partial_override():
    out = merge_preview_config(
        org_defaults={"max_active": 8},
        app_overrides={"share_main_secrets": True},
    )
    # max_active from org, share_main_secrets from app, rest default
    assert out.max_active == 8
    assert out.share_main_secrets is True
    assert out.auto_teardown_on_close is True


# ---- garbage collection --------------------------------------------


def _snap(preview_id: int, last_activity_unix: int, **kw) -> PreviewSnapshot:
    base = dict(pr_number=preview_id, is_pinned=False)
    base.update(kw)
    return PreviewSnapshot(
        preview_id=preview_id,
        last_activity_unix=last_activity_unix,
        **base,
    )


def test_no_eviction_when_under_cap():
    out = previews_to_evict(
        active=[_snap(1, 100), _snap(2, 200)],
        max_active=5,
    )
    assert out == ()


def test_evict_least_recently_active_first():
    out = previews_to_evict(
        active=[
            _snap(1, last_activity_unix=300),
            _snap(2, last_activity_unix=100),  # oldest
            _snap(3, last_activity_unix=200),
            _snap(4, last_activity_unix=400),
            _snap(5, last_activity_unix=500),
            _snap(6, last_activity_unix=600),
        ],
        max_active=4,
    )
    # Two excess → evict the two oldest (preview 2 and preview 3)
    evicted_ids = [p.preview_id for p in out]
    assert evicted_ids == [2, 3]


def test_pinned_previews_dont_count_or_evict():
    """Pinned previews bypass max_active entirely."""
    out = previews_to_evict(
        active=[
            _snap(1, last_activity_unix=100, is_pinned=True),
            _snap(2, last_activity_unix=200, is_pinned=True),
            _snap(3, last_activity_unix=300, is_pinned=True),
            _snap(4, last_activity_unix=400),  # only one unpinned
        ],
        max_active=1,
    )
    assert out == ()  # 1 unpinned <= max_active=1


def test_max_active_must_be_positive():
    with pytest.raises(ValueError):
        previews_to_evict(active=[_snap(1, 100)], max_active=0)
