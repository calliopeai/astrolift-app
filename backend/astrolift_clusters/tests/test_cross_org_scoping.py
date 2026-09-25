"""Cross-tenant scoping tests for the cluster fleet schema (#1183).

``@tenant_scoped()`` only asserts a tenant context exists and
``@require_permission`` checks the caller's role in *their own* org —
neither constrains *which row* a by-id / by-slug resolver reads or
mutates. Before #1183, any resolver that fetched a ``TenantCluster`` /
``ManagedDomain`` by guid/slug without an explicit org constraint would
read or mutate another org's rows.

Both models carry a NULLABLE ``organization`` FK: org-owned rows plus
platform-shared (``organization=None``) rows that every tenant may
operate. The fix scopes every by-id/slug fetch with
``Q(organization_id=<caller org>) | Q(organization_id__isnull=True)``.

Each test pins BOTH halves of that contract:

* a row owned by *another* org is invisible — NOT_FOUND / None / absent
  from lists — and no mutation / workflow / cloud call fires against it;
* a platform-shared (``organization=None``) row stays reachable (the
  ``Q(organization_id__isnull=True)`` branch — the regression risk).

Coverage spans a representative slice of both files: a list read
(``astroliftClusters``), a by-id read (``astroliftClusterHealth``), a
write (``updateTenantCluster``), the destructive path
(``decommissionCluster``), the slug-keyed path
(``recordClusterBootstrapRun``), a ManagedDomain path
(``softDeleteManagedDomain``), and the non-``@tenant_scoped``
``TenantClusterType.bootstrap_runs`` field re-fetch.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import (
    ClusterBootstrapRun,
    ManagedDomain,
    ProviderPlugin,
    TenantCluster,
)
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    DecommissionClusterInputType,
    RecordClusterBootstrapRunInput,
    SoftDeleteManagedDomainInput,
    UpdateTenantClusterInput,
)
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.schema.types import cluster_to_type
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.events import register_event_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- fixtures ------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_temporal(settings):
    """The destructive mutations call start_workflow. Disable the
    Temporal client so the flip-and-enqueue helper is a pure DB write —
    the workflow body is covered elsewhere. If the org scope ever
    regressed, we'd want NOT_FOUND to short-circuit *before* the enqueue,
    which this lets us assert via the unchanged lifecycle."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on every Organization create."""
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
    """Swap the event writer for a list-capturing one, restoring whatever
    was installed before (mirrors test_record_bootstrap_run)."""
    import core.events as _events_mod

    captured: list = []
    previous = _events_mod._writer
    register_event_writer(captured.append)
    yield captured
    register_event_writer(previous)


@pytest.fixture
def org_a():
    return Organization.objects.create(name="A", slug=f"a-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="B", slug=f"b-{uuid.uuid4().hex[:6]}")


def _plugin() -> ProviderPlugin:
    # bulk_create sidesteps ProviderPlugin's version-as-CharField clash
    # with BaseCoreModel.save (same trick as the sibling test suites).
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


def _cluster(organization, **overrides) -> TenantCluster:
    """A cluster owned by ``organization`` (pass ``None`` for a
    platform-shared row)."""
    kwargs = {
        "organization": organization,
        "slug": f"c-{uuid.uuid4().hex[:6]}",
        "name": "dev",
        "provider_plugin": _plugin(),
        "provider_config": {},
        "endpoint": "https://invalid",
        "auth_method": TenantCluster.AuthMethod.KUBECONFIG,
        "auth_config": {"kubeconfig": "fake"},
        "is_active": True,
        "lifecycle": TenantCluster.Lifecycle.MANAGED.value,
    }
    kwargs.update(overrides)
    return TenantCluster.objects.create(**kwargs)


def _domain(organization, **overrides) -> ManagedDomain:
    kwargs = {
        "organization": organization,
        "zone": f"{uuid.uuid4().hex[:8]}.example.com",
        "dns_driver": "route53",
    }
    kwargs.update(overrides)
    return ManagedDomain.objects.create(**kwargs)


def _info():
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _bootstrap_input(cluster_slug: str, **overrides) -> RecordClusterBootstrapRunInput:
    started = dt.datetime(2026, 7, 21, 12, 0, 0, tzinfo=dt.UTC)
    kwargs = {
        "cluster_slug": cluster_slug,
        "status": "succeeded",
        "chart_version": "astrolift-0.42.0",
        "installed_releases": [],
        "cli_version": "astro 0.5.1",
        "host_info": {},
        "error_message": None,
        "started_at": started,
        "ended_at": started + dt.timedelta(minutes=4),
    }
    kwargs.update(overrides)
    return RecordClusterBootstrapRunInput(**kwargs)


# ---- list read: astroliftClusters ---------------------------------


def test_list_excludes_other_org_includes_own_and_shared(org_a, org_b, permission_resolver):
    mine = _cluster(org_a)
    theirs = _cluster(org_b)
    shared = _cluster(None)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(org_a):
        rows = ClustersQuery().astrolift_clusters(_info())
    slugs = {r.slug for r in rows}
    assert mine.slug in slugs
    assert shared.slug in slugs  # Q(organization_id__isnull=True) branch
    assert theirs.slug not in slugs  # cross-tenant leak would include it


# ---- by-id read: astroliftClusterHealth ---------------------------


def test_cluster_health_other_org_returns_none(org_a, org_b, permission_resolver, monkeypatch):
    # If the fetch weren't scoped, this dispatch WOULD run against another
    # org's cluster; scoping returns None before we ever reach it.
    monkeypatch.setattr(
        "core.cluster_management.cluster_health_dispatch",
        lambda cluster, event_limit: {"pods": [], "events": []},
    )
    theirs = _cluster(org_b)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(org_a):
        result = ClustersQuery().astrolift_cluster_health(_info(), cluster_id=GUID(str(theirs.guid)))
    assert result is None


def test_cluster_health_shared_cluster_is_reachable(org_a, permission_resolver, monkeypatch):
    monkeypatch.setattr(
        "core.cluster_management.cluster_health_dispatch",
        lambda cluster, event_limit: {"pods": [], "events": []},
    )
    shared = _cluster(None)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(org_a):
        result = ClustersQuery().astrolift_cluster_health(_info(), cluster_id=GUID(str(shared.guid)))
    assert result is not None
    assert str(result.cluster_id) == str(shared.guid)


# ---- write: updateTenantCluster -----------------------------------


def test_update_other_org_cluster_is_not_found_and_unchanged(org_a, org_b, permission_resolver):
    theirs = _cluster(org_b, region="us-east-1")
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with _ctx(org_a):
        result = ClustersMutation().update_tenant_cluster(
            _info(),
            UpdateTenantClusterInput(id=GUID(str(theirs.guid)), region="hacked-region"),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    theirs.refresh_from_db()
    assert theirs.region == "us-east-1"  # mutation must not have landed


def test_update_shared_cluster_is_the_operators(org_a, permission_resolver):
    """A tenant cannot change a shared cluster every org trusts (#1918)."""
    shared = _cluster(None, region="us-east-1")
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with _ctx(org_a):
        result = ClustersMutation().update_tenant_cluster(
            _info(),
            UpdateTenantClusterInput(id=GUID(str(shared.guid)), region="us-west-2"),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    shared.refresh_from_db()
    assert shared.region == "us-east-1"


# ---- destructive: decommissionCluster -----------------------------


def test_decommission_other_org_cluster_is_not_found_and_unchanged(org_a, org_b, permission_resolver):
    theirs = _cluster(org_b, lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    permission_resolver.grant(Permission.CLUSTER_UNREGISTER)
    with _ctx(org_a):
        result = ClustersMutation().decommission_cluster(
            _info(),
            DecommissionClusterInputType(cluster_id=GUID(str(theirs.guid)), delete_cloud_infra=True),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    theirs.refresh_from_db()
    # No lifecycle flip => the teardown workflow was never enqueued against
    # another org's real cloud infra.
    assert theirs.lifecycle == TenantCluster.Lifecycle.MANAGED.value


def test_decommission_shared_cluster_is_the_operators(org_a, permission_resolver):
    """A tenant cannot tear down a shared cluster (#1918)."""
    shared = _cluster(None, lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    permission_resolver.grant(Permission.CLUSTER_UNREGISTER)
    with _ctx(org_a):
        result = ClustersMutation().decommission_cluster(
            _info(),
            DecommissionClusterInputType(cluster_id=GUID(str(shared.guid)), delete_cloud_infra=False),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    shared.refresh_from_db()
    assert shared.lifecycle == TenantCluster.Lifecycle.MANAGED.value


# ---- slug-keyed: recordClusterBootstrapRun ------------------------


def test_record_bootstrap_other_org_slug_is_not_found_and_writes_nothing(
    org_a, org_b, permission_resolver, captured_events
):
    theirs = _cluster(org_b)
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org_a):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _bootstrap_input(theirs.slug),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "clusterSlug"
    assert ClusterBootstrapRun.objects.filter(tenant_cluster=theirs).count() == 0


def test_record_bootstrap_shared_cluster_slug_writes_row(org_a, permission_resolver, captured_events):
    shared = _cluster(None)
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org_a):
        result = ClustersMutation().record_cluster_bootstrap_run(
            _info(),
            _bootstrap_input(shared.slug),
        )
    assert result.ok is True
    assert ClusterBootstrapRun.objects.filter(tenant_cluster=shared).count() == 1


# ---- ManagedDomain: softDeleteManagedDomain -----------------------


def test_soft_delete_other_org_domain_is_not_found_and_unchanged(org_a, org_b, permission_resolver):
    theirs = _domain(org_b)
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    with _ctx(org_a):
        result = ClustersMutation().soft_delete_managed_domain(
            _info(),
            SoftDeleteManagedDomainInput(id=GUID(str(theirs.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    theirs.refresh_from_db()
    assert theirs.deleted_at is None  # delete must not have landed


def test_soft_delete_shared_domain_is_the_operators(org_a, permission_resolver):
    """A tenant cannot delete a shared zone (#1929)."""
    shared = _domain(None)
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    with _ctx(org_a):
        result = ClustersMutation().soft_delete_managed_domain(
            _info(),
            SoftDeleteManagedDomainInput(id=GUID(str(shared.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    shared.refresh_from_db()
    assert shared.deleted_at is None


# ---- non-@tenant_scoped field re-fetch: TenantClusterType.bootstrap_runs


def test_bootstrap_runs_field_does_not_leak_other_org_history(org_a, org_b):
    theirs = _cluster(org_b)
    ClusterBootstrapRun.objects.create(
        tenant_cluster=theirs,
        status="succeeded",
        chart_version="astrolift-0.42.0",
        installed_releases=[],
        cli_version="astro 0.5.1",
        host_info={},
        error_message="",
        started_at=dt.datetime(2026, 7, 21, 9, 0, 0, tzinfo=dt.UTC),
        ended_at=dt.datetime(2026, 7, 21, 9, 2, 0, tzinfo=dt.UTC),
    )
    # A leaked parent type carrying another org's guid must not widen into
    # that org's bootstrap history when the nested field re-fetches.
    leaked_parent = cluster_to_type(theirs)
    with _ctx(org_a):
        assert leaked_parent.bootstrap_runs() == []
        assert leaked_parent.last_bootstrap_run() is None


def test_bootstrap_runs_field_reaches_shared_cluster_history(org_a):
    shared = _cluster(None)
    # Recorded by org_a, as recordClusterBootstrapRun stamps it. Another
    # org's run on the same shared cluster stays out (#1955, see
    # test_cluster_history_tenancy_1955).
    run = ClusterBootstrapRun.objects.create(
        tenant_cluster=shared,
        organization=org_a,
        status="succeeded",
        chart_version="astrolift-0.42.0",
        installed_releases=[],
        cli_version="astro 0.5.1",
        host_info={},
        error_message="",
        started_at=dt.datetime(2026, 7, 21, 9, 0, 0, tzinfo=dt.UTC),
        ended_at=dt.datetime(2026, 7, 21, 9, 2, 0, tzinfo=dt.UTC),
    )
    parent = cluster_to_type(shared)
    with _ctx(org_a):
        history = parent.bootstrap_runs()
        latest = parent.last_bootstrap_run()
    assert [str(r.id) for r in history] == [str(run.guid)]
    assert latest is not None and str(latest.id) == str(run.guid)
