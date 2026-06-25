"""Orphan detection scan (#995, scan phase — read-only).

Pins the core contract: an owned cloud resource with no live owner row is
flagged; a live app's resource is not; and a scan that can't fully enumerate
(unsupported driver or a list error) reports INCOMPLETE rather than implying
a clean result — the guard against a false "all clear".
"""

from __future__ import annotations

import pytest

from astrolift_operations.services import orphan_reaper as reaper

pytestmark = pytest.mark.django_db


class _FakeIdentityDriver:
    def __init__(self, roles):
        self._roles = roles

    def list_owned_roles(self):
        return list(self._roles)


def _managed(cluster):
    from astrolift_clusters.models import TenantCluster

    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    cluster.save(update_fields=["lifecycle"])
    return cluster


def test_orphan_role_flagged_live_role_not(app, cluster, monkeypatch):
    from core.app_deploy import workload_identity_role_name

    _managed(cluster)
    live_name = workload_identity_role_name(app)  # app is live → must NOT flag
    orphan_name = "astrolift-ghost-org-ghost-app"  # no DB row → orphan

    driver = _FakeIdentityDriver([live_name, orphan_name])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    report = reaper.scan_orphans()

    ids = [o.identifier for o in report.orphans]
    assert orphan_name in ids
    assert live_name not in ids
    assert report.complete
    assert report.scanned_kinds == [reaper.KIND_IAM_ROLE]
    assert all(o.classification == "dangling" for o in report.orphans)


def test_soft_deleted_app_role_is_orphan(app, cluster, monkeypatch):
    from django.utils import timezone

    from core.app_deploy import workload_identity_role_name

    _managed(cluster)
    name = workload_identity_role_name(app)
    # App torn down at the DB layer but its IAM role lingered.
    app.deleted_at = timezone.now()
    app.save(update_fields=["deleted_at"])

    driver = _FakeIdentityDriver([name])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    report = reaper.scan_orphans()
    assert [o.identifier for o in report.orphans] == [name]


def test_unsupported_driver_marks_incomplete_not_clean(app, cluster, monkeypatch):
    from _sdk import UnsupportedOperationError

    _managed(cluster)

    class _Unsupported:
        def list_owned_roles(self):
            raise UnsupportedOperationError("not on this cloud")

    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda *a, **k: _Unsupported()
    )
    report = reaper.scan_orphans()
    assert reaper.KIND_IAM_ROLE in report.incomplete_kinds
    assert not report.complete
    assert report.orphans == []


def test_list_error_marks_incomplete(app, cluster, monkeypatch):
    _managed(cluster)

    class _Broken:
        def list_owned_roles(self):
            raise RuntimeError("aws unreachable")

    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda *a, **k: _Broken()
    )
    report = reaper.scan_orphans()
    assert reaper.KIND_IAM_ROLE in report.incomplete_kinds
    assert report.orphans == []
