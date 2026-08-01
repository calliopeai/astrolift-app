"""Tests for ``recordClusterBootstrapRun`` (#319).

The mutation is the CLI's report-back hook after
``astro cluster bootstrap`` settles. Boundaries pinned here:

* permission gate denies callers without ``cluster.manage``
* happy path writes the row + emits a ``cluster.bootstrap_run`` event
* unknown status string returns VALIDATION
* unknown cluster slug returns NOT_FOUND
* ``TenantClusterType.last_bootstrap_run`` returns the most recent run
* ``TenantClusterType.bootstrap_runs`` returns the limited history
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import (
    ClusterBootstrapRun,
    ProviderPlugin,
    TenantCluster,
)
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    RecordClusterBootstrapRunInput,
)
from astrolift_clusters.schema.types import cluster_to_type
from astrolift_identity.models import Organization
from core.events import register_event_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on every Organization/User create."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def captured_events():
    """Replace the default event writer with a list-capturing one for
    the duration of the test, then restore *whatever was previously
    installed* — typically the persistent DB writer registered by
    astrolift_operations.apps.ready. Restoring to ``_log_event``
    unconditionally (the previous shape) broke any later test in the
    suite that relied on emit() landing rows in the Event table."""
    import core.events as _events_mod

    captured: list = []
    previous = _events_mod._writer
    register_event_writer(captured.append)
    yield captured
    register_event_writer(previous)


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    # bulk_create sidesteps ProviderPlugin's version-as-CharField clash
    # with BaseCoreModel.save's ``version = (version or 0) + 1``. Matches
    # the pattern in test_management_mutations.
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug=f"k8s-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _info(user=None):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _input(cluster_slug: str, **overrides) -> RecordClusterBootstrapRunInput:
    started = dt.datetime(2026, 5, 16, 12, 0, 0, tzinfo=dt.UTC)
    ended = dt.datetime(2026, 5, 16, 12, 4, 0, tzinfo=dt.UTC)
    kwargs = {
        "cluster_slug": cluster_slug,
        "status": "succeeded",
        "chart_version": "astrolift-0.42.0",
        "installed_releases": [
            {"name": "cert-manager", "version": "1.15.0", "status": "deployed"},
            {"name": "ingress-nginx", "version": "4.10.0", "status": "deployed"},
        ],
        "cli_version": "astro 0.5.1",
        "host_info": {"os": "linux", "arch": "amd64", "helm": "v3.14.4"},
        "error_message": None,
        "started_at": started,
        "ended_at": ended,
    }
    kwargs.update(overrides)
    return RecordClusterBootstrapRunInput(**kwargs)


# ---- permission gate ----------------------------------------------


def test_record_denied_without_cluster_manage_permission(cluster, org, permission_resolver):
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    assert ClusterBootstrapRun.objects.count() == 0


# ---- happy path ---------------------------------------------------


def test_record_writes_row_and_emits_event(cluster, org, permission_resolver, captured_events):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug),
        )

    assert result.ok is True
    assert result.data is not None
    assert str(result.data.id)

    run = ClusterBootstrapRun.objects.get(tenant_cluster=cluster)
    assert run.status == "succeeded"
    assert run.chart_version == "astrolift-0.42.0"
    assert len(run.installed_releases) == 2
    assert run.cli_version == "astro 0.5.1"
    assert run.host_info == {"os": "linux", "arch": "amd64", "helm": "v3.14.4"}
    assert run.error_message == ""
    assert str(result.data.id) == str(run.guid)

    bootstrap_events = [e for e in captured_events if e.event_type == "cluster.bootstrap_run"]
    assert len(bootstrap_events) == 1
    envelope = bootstrap_events[0]
    assert envelope.resource_kind == "TenantCluster"
    assert envelope.resource_id == str(cluster.guid)
    assert envelope.organization_id == org.id
    assert envelope.payload["status"] == "succeeded"
    assert envelope.payload["chart_version"] == "astrolift-0.42.0"
    assert envelope.payload["cluster_slug"] == cluster.slug
    assert envelope.payload["installed_releases"][0]["name"] == "cert-manager"


def test_record_failed_run_carries_error_message(cluster, org, permission_resolver, captured_events):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(
                cluster.slug,
                status="failed",
                error_message="helm: chart deps unresolved",
                installed_releases=[],
            ),
        )
    assert result.ok is True
    run = ClusterBootstrapRun.objects.get(tenant_cluster=cluster)
    assert run.status == "failed"
    assert run.error_message == "helm: chart deps unresolved"
    assert run.installed_releases == []


# ---- validation ---------------------------------------------------


def test_record_rejects_unknown_status(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug, status="weird"),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "status"
    assert ClusterBootstrapRun.objects.count() == 0


def test_record_rejects_non_list_installed_releases(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug, installed_releases={"not": "a list"}),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "installedReleases"


def test_record_rejects_non_dict_host_info(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug, host_info=["not", "a", "dict"]),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "VALIDATION"


def test_record_rejects_inverted_timestamps(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    started = dt.datetime(2026, 5, 16, 12, 5, 0, tzinfo=dt.UTC)
    ended = dt.datetime(2026, 5, 16, 12, 0, 0, tzinfo=dt.UTC)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug, started_at=started, ended_at=ended),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "endedAt"


# ---- not found ----------------------------------------------------


def test_record_returns_not_found_on_unknown_slug(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input("does-not-exist"),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "clusterSlug"


def test_record_refuses_soft_deleted_cluster(cluster, org, permission_resolver):
    cluster.soft_delete()
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _input(cluster.slug),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


# ---- TenantClusterType surface -----------------------------------


def test_last_bootstrap_run_returns_most_recent(cluster, org, permission_resolver):
    started = dt.datetime(2026, 5, 16, 9, 0, 0, tzinfo=dt.UTC)
    older = ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        status="failed",
        chart_version="astrolift-0.41.0",
        installed_releases=[],
        cli_version="astro 0.5.0",
        host_info={},
        error_message="prior failure",
        started_at=started,
        ended_at=started + dt.timedelta(minutes=2),
    )
    newer = ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        status="succeeded",
        chart_version="astrolift-0.42.0",
        installed_releases=[{"name": "cert-manager", "version": "1.15.0"}],
        cli_version="astro 0.5.1",
        host_info={},
        error_message="",
        started_at=started + dt.timedelta(hours=2),
        ended_at=started + dt.timedelta(hours=2, minutes=3),
    )

    cluster_type = cluster_to_type(cluster)
    # These resolvers re-scope by caller org (#1183) and fail closed when
    # there is no tenant, so they have to be called inside one — without it
    # they answer None for every cluster and the assertions below can only
    # pass by accident.
    with _ctx(org):
        latest = cluster_type.last_bootstrap_run()
        history = cluster_type.bootstrap_runs(limit=10)

    assert latest is not None
    assert str(latest.id) == str(newer.guid)
    assert latest.status == "succeeded"
    assert latest.chart_version == "astrolift-0.42.0"
    assert [str(r.id) for r in history] == [str(newer.guid), str(older.guid)]


def test_last_bootstrap_run_is_none_when_no_history(cluster, org):
    cluster_type = cluster_to_type(cluster)
    # Inside a tenant context, so this asserts "no runs recorded" rather
    # than passing on the fail-closed path and hiding a real regression.
    with _ctx(org):
        assert cluster_type.last_bootstrap_run() is None
        assert cluster_type.bootstrap_runs() == []


def test_bootstrap_runs_respects_limit(cluster, org):
    base = dt.datetime(2026, 5, 16, 10, 0, 0, tzinfo=dt.UTC)
    for i in range(5):
        ClusterBootstrapRun.objects.create(
            tenant_cluster=cluster,
            status="succeeded",
            chart_version=f"astrolift-0.{i}.0",
            installed_releases=[],
            cli_version="astro 0.5.1",
            host_info={},
            error_message="",
            started_at=base + dt.timedelta(hours=i),
            ended_at=base + dt.timedelta(hours=i, minutes=2),
        )
    cluster_type = cluster_to_type(cluster)
    with _ctx(org):
        assert len(cluster_type.bootstrap_runs(limit=2)) == 2
        assert len(cluster_type.bootstrap_runs(limit=10)) == 5
