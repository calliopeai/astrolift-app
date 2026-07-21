"""
astrolift_deployment_metrics per-day series (#1069).

The metrics query grew per-day arrays (``daily_succeeded`` / ``daily_failed`` /
``daily_mean_duration_seconds``) that back the dashboard trend sparklines.
These tests pin the bucketing contract: length == ``window_days``, oldest →
newest, gaps zero-filled (counts) / null (duration mean), and every counted row
lands in exactly one bucket so the daily arrays sum back to the window
aggregates. Buckets are rolling 24h slices anchored at the same UTC cutoff the
aggregates use.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _deploy(app, env, *, status, created_at, duration_seconds=None):
    """Create a Deployment in ``status`` with a controlled ``created_at``.

    ``created_at`` is ``auto_now_add``, so it's set after insert via
    ``update()`` — the metrics bucketing keys off it."""
    dep = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        image_tag="v1",
        duration_seconds=duration_seconds,
    )
    Deployment.objects.filter(pk=dep.pk).update(created_at=created_at)
    return dep


def _metrics(app, window_days):
    with tenant_context(TenantContext(organization_id=app.organization_id)):
        return LifecycleQuery().astrolift_deployment_metrics(_info(), window_days=window_days)


def test_arrays_have_window_length_and_zero_fill_gaps(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    # Two deploys today, none on any other day of a 30-day window.
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=1))
    _deploy(app, env, status=Deployment.Status.FAILED.value, created_at=now - timedelta(hours=2))

    m = _metrics(app, 30)

    assert len(m.daily_succeeded) == 30
    assert len(m.daily_failed) == 30
    assert len(m.daily_mean_duration_seconds) == 30
    # Only the newest bucket is populated; every earlier day is zero / null.
    assert m.daily_succeeded[-1] == 1
    assert m.daily_failed[-1] == 1
    assert m.daily_succeeded[:-1] == [0] * 29
    assert m.daily_failed[:-1] == [0] * 29
    assert m.daily_mean_duration_seconds == [None] * 30


def test_empty_window_all_zeros(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    m = _metrics(app, 14)
    assert m.total == 0
    assert m.success_rate == -1.0
    assert m.daily_succeeded == [0] * 14
    assert m.daily_failed == [0] * 14
    assert m.daily_mean_duration_seconds == [None] * 14


def test_bucket_boundary_at_24h(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    # A 2-day window puts the bucket boundary exactly 24h ago: 23h ago is
    # "today" (newest bucket, idx 1) and 25h ago is "yesterday" (idx 0). The
    # 1h margin keeps the assertion clear of sub-second now() drift between the
    # test and the resolver.
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=23))
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=25))

    m = _metrics(app, 2)
    assert m.daily_succeeded == [1, 1]  # oldest → newest


def test_deploy_lands_in_expected_day_bucket(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    window = 7
    # A deploy "k days + 12h ago" lands in bucket (window - 1 - k); the 12h
    # offset keeps each sample clear of a bucket edge.
    for k in range(window):
        _deploy(
            app,
            env,
            status=Deployment.Status.RUNNING.value,
            created_at=now - timedelta(days=k, hours=12),
        )

    m = _metrics(app, window)
    # One succeeded rollout in every bucket, oldest → newest.
    assert m.daily_succeeded == [1] * window


def test_succeeded_vs_failed_split(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    window = 5
    # Newest bucket: 2 succeeded + 1 failed. Bucket two days back: 1 failed.
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=1))
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=2))
    _deploy(app, env, status=Deployment.Status.FAILED.value, created_at=now - timedelta(hours=3))
    _deploy(app, env, status=Deployment.Status.FAILED.value, created_at=now - timedelta(days=2, hours=12))
    # rolled_back + in-flight rows must not leak into the succeeded/failed split.
    _deploy(app, env, status=Deployment.Status.ROLLED_BACK.value, created_at=now - timedelta(hours=4))
    _deploy(app, env, status=Deployment.Status.DEPLOYING.value, created_at=now - timedelta(hours=5))

    m = _metrics(app, window)
    assert m.daily_succeeded[-1] == 2
    assert m.daily_failed[-1] == 1
    assert m.daily_failed[2] == 1  # [now-3d, now-2d) — two days back
    # Daily arrays sum back to the window aggregates.
    assert sum(m.daily_succeeded) == m.succeeded == 2
    assert sum(m.daily_failed) == m.failed == 2


def test_duration_mean_per_day(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    window = 4
    # Two rollouts today, durations 40s + 80s → today's mean is 60. Duration is
    # sampled across all terminal states (matching mean_duration_seconds), so
    # the failed row's 80s counts.
    _deploy(
        app,
        env,
        status=Deployment.Status.RUNNING.value,
        created_at=now - timedelta(hours=1),
        duration_seconds=40,
    )
    _deploy(
        app,
        env,
        status=Deployment.Status.FAILED.value,
        created_at=now - timedelta(hours=2),
        duration_seconds=80,
    )
    # One rollout a day back, duration 30.
    _deploy(
        app,
        env,
        status=Deployment.Status.RUNNING.value,
        created_at=now - timedelta(days=1, hours=1),
        duration_seconds=30,
    )

    m = _metrics(app, window)
    assert m.daily_mean_duration_seconds[-1] == 60.0  # (40 + 80) / 2
    assert m.daily_mean_duration_seconds[-2] == 30.0  # single rollout a day back
    # Days with no rollouts are null, not 0 (0.0 would read as an instant deploy).
    assert m.daily_mean_duration_seconds[0] is None
    assert m.daily_mean_duration_seconds[1] is None


def test_deploy_outside_window_excluded(app, env, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    window = 3
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=1))
    # Older than the window → excluded from both the aggregate and the arrays.
    _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(days=10))

    m = _metrics(app, window)
    assert m.total == 1
    assert len(m.daily_succeeded) == window
    assert sum(m.daily_succeeded) == 1
    assert m.daily_succeeded[-1] == 1
