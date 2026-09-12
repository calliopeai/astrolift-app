"""A failed cronjob run has to be visible in the product (#1710).

A `kind = "cronjob"` workload whose run failed left no trace anywhere in
Astrolift. The app read healthy throughout -- health was the `web`
Deployment, and the cronjob is a different workload of the same app -- so
the only way to learn about it was kubectl. By the time anyone looked the
pod had been reaped and the Job's events had aged out, so the reason was
gone as well.

Surfaced through the app doctor, which is the app-scoped panel an
operator already reads. Split into a scheduled probe and a cached check
for the same reason the DNS check is (#1550): the doctor renders on every
app-detail load, and a kubernetes round-trip per load is a latency nobody
asked for.
"""

from __future__ import annotations

import datetime as dt

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.services.app_doctor import (
    CHECK_CRONJOB_RUNS,
    probe_app_cronjob_runs,
    run_app_doctor,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _job(name: str, *, failed: bool, owned: bool = True) -> dict:
    meta: dict = {"name": name}
    if owned:
        meta["ownerReferences"] = [{"kind": "CronJob", "name": "refresh"}]
    conditions = (
        [{"type": "Failed", "status": "True"}] if failed else [{"type": "Complete", "status": "True"}]
    )
    return {"metadata": meta, "status": {"conditions": conditions}}


@pytest.fixture
def app(db):
    org = Organization.objects.create(name="Conflict", slug="conflict-1710")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1710")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="aws", slug="aws-1710", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-1710",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
    )
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="quake-dash",
        slug="quake-dash-1710",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
        manifest_normalized={
            "workloads": [
                {"name": "web", "kind": "deployment"},
                {"name": "refresh", "kind": "cronjob"},
            ]
        },
    )


def _check(app):
    return next(c for c in run_app_doctor(app).checks if c.key == CHECK_CRONJOB_RUNS)


# ---- the probe --------------------------------------------------------


def test_the_probe_records_failed_runs(app):
    record = probe_app_cronjob_runs(
        app, list_jobs=lambda *_a: [_job("refresh-29804400", failed=True), _job("refresh-1", failed=False)]
    )

    assert record["failed"] == ["refresh-29804400"]
    assert record["total"] == 2
    app.refresh_from_db()
    assert app.cronjob_probe["failed"] == ["refresh-29804400"]


def test_the_probe_ignores_jobs_no_cronjob_owns(app):
    """An agent task Job in the same namespace is not a cronjob run."""

    probe_app_cronjob_runs(app, list_jobs=lambda *_a: [_job("one-off", failed=True, owned=False)])

    app.refresh_from_db()
    assert app.cronjob_probe["failed"] == []
    assert app.cronjob_probe["total"] == 0


def test_an_unreadable_cluster_records_an_error_not_a_pass(app):
    def _boom(*_a):
        raise RuntimeError("connection refused")

    record = probe_app_cronjob_runs(app, list_jobs=_boom)

    assert "connection refused" in record["error"]
    assert _check(app).status == "unknown"


# ---- the check --------------------------------------------------------


def test_a_failed_run_fails_the_doctor(app):
    """The bug: nothing anywhere said this had happened."""

    probe_app_cronjob_runs(app, list_jobs=lambda *_a: [_job("refresh-29804400", failed=True)])

    check = _check(app)
    assert check.status == "fail"
    assert "refresh-29804400" in check.detail
    # The evidence expires, so the message says to look now.
    assert "TTL" in check.detail


def test_clean_runs_pass(app):
    probe_app_cronjob_runs(app, list_jobs=lambda *_a: [_job("refresh-1", failed=False)])

    assert _check(app).status == "pass"


def test_a_declared_cronjob_that_never_ran_warns(app):
    probe_app_cronjob_runs(app, list_jobs=lambda *_a: [])

    check = _check(app)
    assert check.status == "warn"
    assert "no run has happened yet" in check.detail


def test_never_probed_is_unknown_not_pass(app):
    """ "Nobody has checked" and "it is fine" are different answers."""

    assert _check(app).status == "unknown"


def test_a_stale_probe_is_unknown_not_pass(app):
    probe_app_cronjob_runs(app, list_jobs=lambda *_a: [_job("refresh-1", failed=False)])
    stale = dict(app.cronjob_probe)
    stale["probed_at"] = (dt.datetime.now(dt.UTC) - dt.timedelta(days=2)).isoformat()
    app.cronjob_probe = stale
    app.save(update_fields=["cronjob_probe"])

    check = _check(app)
    assert check.status == "unknown"
    assert "too old" in check.detail


def test_a_malformed_probe_is_unknown_not_pass(app):
    app.cronjob_probe = {"probed_at": "not-a-date", "failed": []}
    app.save(update_fields=["cronjob_probe"])

    assert _check(app).status == "unknown"


def test_an_app_with_no_cronjob_is_skipped(app):
    app.manifest_normalized = {"workloads": [{"name": "web", "kind": "deployment"}]}
    app.save(update_fields=["manifest_normalized"])

    assert _check(app).status == "skip"


def test_the_doctor_never_makes_a_live_call(app, monkeypatch):
    """#1550's contract: the doctor renders on every app-detail load."""

    def _boom(*_a, **_k):
        raise AssertionError("run_app_doctor must not touch the cluster")

    monkeypatch.setattr("astrolift_registry.services.app_doctor._default_list_jobs", _boom)
    assert _check(app).status == "unknown"
