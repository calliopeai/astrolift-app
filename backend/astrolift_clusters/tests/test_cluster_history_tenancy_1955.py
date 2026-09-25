"""Cluster history reads stay inside the caller's org (#1955).

Two cluster surfaces read rows that the cluster alone cannot attribute:

* ``astroliftClusterLifecycleAudit`` reads ``MutationAuditLog``, which had
  no organization column. It kept a row when ``str(variables)`` contained
  the cluster's guid or slug, so another org's mutations on a shared
  cluster came back, and a short slug matched unrelated rows.
* ``AstroliftTenantCluster.bootstrapRuns`` / ``lastBootstrapRun`` read
  ``ClusterBootstrapRun``, which had no organization column either. Any org
  with ``cluster.manage`` can record a run on a shared cluster, and every
  org read it back: username, host info, error text.

A shared cluster (organization NULL) resolves for every org, which is what
makes the cluster lookup insufficient, so most tests here use one. Every
"other org" assertion fails with the fix reverted.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ClusterBootstrapRun, ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    InstallClusterPrereqsInputType,
    RecordClusterBootstrapRunInput,
)
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.schema.types import cluster_to_type
from astrolift_graphql import GUID
from astrolift_identity.models import Member, Organization
from astrolift_workflows.activities.install_prereqs import _record_bootstrap_run_sync
from core.events import register_event_writer
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

REGISTER = (
    "mutation Register($input: RegisterTenantClusterInput!) { "
    "registerTenantCluster(input: $input) { ok errors { code message } } }"
)
GQL = f"/{settings.BASE_URL}gql/config/"
UPDATE = (
    "mutation Update($input: UpdateTenantClusterInput!) { "
    "updateTenantCluster(input: $input) { ok errors { code message } } }"
)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Creating a User indexes its Profile in OpenSearch; not under test."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture(autouse=True)
def _cluster_permissions(permission_resolver):
    """Every caller here can read and manage clusters, so each result is
    decided by the org scoping under test, not by RBAC. ``cluster.update``
    is left out: the forged-header test needs that mutation refused."""
    for permission in (Permission.CLUSTER_REGISTER, Permission.CLUSTER_MANAGE):
        permission_resolver.grant(permission)


@pytest.fixture
def captured_events():
    """recordClusterBootstrapRun emits an Event; keep its fan-out out of
    these tests and restore whatever writer was installed before."""
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


@pytest.fixture
def user_a():
    return get_user_model().objects.create(username=f"alice-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def user_b():
    return get_user_model().objects.create(username=f"bob-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    # bulk_create sidesteps ProviderPlugin's version-as-CharField clash with
    # BaseCoreModel.save, as the sibling suites do.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug=f"k8s-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return row


def _cluster(organization, plugin, *, slug: str | None = None) -> TenantCluster:
    return TenantCluster.objects.create(
        organization=organization,
        slug=slug or f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def shared(plugin):
    return _cluster(None, plugin)


def _member(user, org) -> Member:
    return Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.id)


def _ctx(org, user=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id if user else None))


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _execute(query: str, variables: dict, user):
    from config.schema import schema

    result = schema.execute_sync(
        query,
        variable_values=variables,
        context_value=SimpleNamespace(user=user, request=None),
    )
    assert result.errors is None, result.errors
    return result


def _log(organization, operation: str, variables: dict) -> MutationAuditLog:
    return MutationAuditLog.objects.create(
        organization=organization, operation=operation, variables=variables
    )


def _timeline(org, cluster) -> list:
    with _ctx(org):
        return ClustersQuery().astrolift_cluster_lifecycle_audit(_info(), cluster_id=GUID(str(cluster.guid)))


# ---- astroliftClusterLifecycleAudit -------------------------------


def test_each_org_sees_only_its_own_mutations_on_a_shared_cluster(
    org_a, org_b, user_a, user_b, plugin, permission_resolver
):
    """The issue's scenario, end to end through the schema and the audit
    extension: org A registers a shared cluster, org B updates it. Without
    the fix org A gets B's update (the guid is in its variables) and org B
    gets A's registration (the slug is in its variables)."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    _member(user_a, org_a)
    _member(user_b, org_b)
    # Registering and changing a shared cluster is the platform operator's
    # (#1918), so the operator runs it, once in each org's context.
    for user in (user_a, user_b):
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    slug = f"shared-{uuid.uuid4().hex[:6]}"

    with _ctx(org_a, user_a):
        _execute(
            REGISTER,
            {
                "input": {
                    "slug": slug,
                    "name": "Shared",
                    "providerPluginSlug": plugin.slug,
                    "authMethod": "kubeconfig",
                    "organizationScoped": False,
                }
            },
            user_a,
        )
    cluster = TenantCluster.objects.get(slug=slug)
    assert cluster.organization_id is None
    with _ctx(org_b, user_b):
        _execute(UPDATE, {"input": {"id": str(cluster.guid), "region": "eu-west-1"}}, user_b)

    # The extension stamped each row with the org whose session ran it.
    assert MutationAuditLog.objects.get(operation="cluster.register").organization_id == org_a.id
    assert MutationAuditLog.objects.get(operation="cluster.update").organization_id == org_b.id

    as_a = _timeline(org_a, cluster)
    as_b = _timeline(org_b, cluster)
    assert [(e.operation, e.actor) for e in as_a] == [("cluster.register", user_a.username)]
    assert [(e.operation, e.actor) for e in as_b] == [("cluster.update", user_b.username)]


def test_an_outsider_naming_the_org_by_header_stays_out_of_its_timeline(org_a, org_b, plugin):
    """Review PoC: a session names org B in ``X-Astrolift-Organization``,
    which the middleware accepts without a membership check, and tries to
    update B's cluster. RBAC refuses it, but the audit extension still
    wrote the row, and filed it under B."""
    theirs = _cluster(org_b, plugin)
    outsider = get_user_model().objects.create(username=f"mallory-{uuid.uuid4().hex[:6]}")
    _member(outsider, org_a)
    client = Client()
    client.force_login(outsider)

    response = client.post(
        GQL,
        data=json.dumps({"query": UPDATE, "variables": {"input": {"id": str(theirs.guid), "region": "x"}}}),
        content_type="application/json",
        HTTP_X_PLATFORM="web",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(org_b.guid),
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"]["updateTenantCluster"]["ok"] is False
    assert MutationAuditLog.objects.get(operation="cluster.update").organization_id is None
    assert _timeline(org_b, theirs) == []


def test_other_org_rows_stay_out_by_guid_slug_substring_and_prefix(org_a, org_b, shared):
    guid = str(shared.guid)
    slug = shared.slug
    by_guid = _log(org_a, "cluster.install_prereqs", {"input": {"clusterId": guid}})
    by_slug = _log(org_a, "RegisterTenantCluster", {"input": {"slug": slug, "name": "Shared"}})
    # Rows that only contain the identifiers inside a longer value.
    _log(org_a, "CreateManagedDomain", {"input": {"zone": f"{slug}.example.com"}})
    _log(org_a, "cluster.register", {"input": {"slug": f"{slug}-staging"}})
    _log(org_a, "cluster.install_prereqs", {"input": {"clusterId": f"{guid}-restore"}})
    _log(org_a, "cluster.update", {"input": {"id": str(uuid.uuid4()), "name": f"clone of {guid}"}})

    assert _timeline(org_b, shared) == []

    # The owner's own view matches whole values only, so the substring and
    # prefix rows stay out of it too. jsonb reorders keys on the way back,
    # hence the canonical JSON.
    assert {(e.operation, json.dumps(e.variables, sort_keys=True)) for e in _timeline(org_a, shared)} == {
        (by_guid.operation, json.dumps(by_guid.variables, sort_keys=True)),
        (by_slug.operation, json.dumps(by_slug.variables, sort_keys=True)),
    }


def test_rows_written_before_the_org_column_reach_no_tenant(org_a, org_b, shared):
    """A legacy row cannot say whose it is, so no org gets it."""
    _log(None, "cluster.install_prereqs", {"input": {"clusterId": str(shared.guid)}})
    _log(None, "cluster.register", {"input": {"slug": shared.slug}})

    assert _timeline(org_a, shared) == []
    assert _timeline(org_b, shared) == []


def test_other_org_cannot_read_an_org_owned_clusters_timeline(org_a, org_b, plugin):
    theirs = _cluster(org_a, plugin)
    _log(org_a, "cluster.install_prereqs", {"input": {"clusterId": str(theirs.guid)}})
    _log(org_a, "cluster.register", {"input": {"slug": theirs.slug}})

    assert _timeline(org_b, theirs) == []
    assert len(_timeline(org_a, theirs)) == 2


def test_slug_only_names_the_cluster_for_slug_addressed_mutations(org_a, shared):
    """A guid-addressed mutation whose variables merely carry a value equal
    to this cluster's slug (here another cluster's new name) is about that
    other cluster."""
    _log(org_a, "cluster.update", {"input": {"id": str(uuid.uuid4()), "name": shared.slug}})
    bootstrap = _log(org_a, "cluster.record_bootstrap_run", {"input": {"clusterSlug": shared.slug}})

    assert [e.variables for e in _timeline(org_a, shared)] == [bootstrap.variables]


def test_guid_matches_whatever_spelling_the_caller_sent(org_a, shared):
    _log(org_a, "cluster.update", {"input": {"id": str(shared.guid).upper()}})

    assert [e.operation for e in _timeline(org_a, shared)] == ["cluster.update"]


def test_limit_is_clamped_to_200(org_a, shared):
    MutationAuditLog.objects.bulk_create(
        MutationAuditLog(
            organization=org_a, operation="cluster.update", variables={"input": {"id": str(shared.guid)}}
        )
        for _ in range(201)
    )
    with _ctx(org_a):
        rows = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(), cluster_id=GUID(str(shared.guid)), limit=10_000
        )

    assert len(rows) == 200


# ---- bootstrapRuns / lastBootstrapRun -----------------------------


def _bootstrap_input(slug: str) -> RecordClusterBootstrapRunInput:
    started = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.UTC)
    return RecordClusterBootstrapRunInput(
        cluster_slug=slug,
        status="failed",
        chart_version="astrolift-0.42.0",
        installed_releases=[],
        cli_version="astro 0.5.1",
        host_info={"os": "darwin", "hostname": "bobs-laptop"},
        error_message="helm: context deadline exceeded",
        started_at=started,
        ended_at=started + dt.timedelta(minutes=3),
    )


def _runs(org, cluster):
    parent = cluster_to_type(cluster)
    with _ctx(org):
        return parent.bootstrap_runs(), parent.last_bootstrap_run()


def test_other_orgs_bootstrap_run_on_a_shared_cluster_stays_out(
    org_a, org_b, user_b, shared, captured_events
):
    with _ctx(org_b, user_b):
        result = ClustersMutation().record_cluster_bootstrap_run(_info(user_b), _bootstrap_input(shared.slug))
    assert result.ok is True, result.errors
    run = ClusterBootstrapRun.objects.get(tenant_cluster=shared)
    assert run.organization_id == org_b.id

    assert _runs(org_a, shared) == ([], None)

    history, latest = _runs(org_b, shared)
    assert [str(r.id) for r in history] == [str(run.guid)]
    assert latest is not None and latest.triggered_by_username == user_b.username


def _record_ui_run(cluster, user, **kwargs) -> ClusterBootstrapRun:
    _record_bootstrap_run_sync(
        cluster.pk,
        user.pk,
        "succeeded",
        [],
        "",
        "2026-09-01T09:00:00+00:00",
        "2026-09-01T09:02:00+00:00",
        **kwargs,
    )
    return ClusterBootstrapRun.objects.filter(tenant_cluster=cluster).latest("created_at")


def test_ui_install_run_belongs_to_the_org_that_asked_for_it(org_a, org_b, user_b, shared):
    run = _record_ui_run(shared, user_b, organization_id=org_b.id)
    assert run.organization_id == org_b.id

    assert _runs(org_a, shared) == ([], None)
    assert [str(r.id) for r in _runs(org_b, shared)[0]] == [str(run.guid)]


def test_ui_install_run_without_an_org_falls_back_to_the_clusters_own(org_a, user_a, plugin, shared):
    """A workflow started before the input carried the org. Only the owning
    org can install on an org-owned cluster, so that org owns the run; a
    shared cluster has no owner to fall back to."""
    owned = _cluster(org_a, plugin)

    assert _record_ui_run(owned, user_a).organization_id == org_a.id
    assert _record_ui_run(shared, user_a).organization_id is None


def test_no_tenant_reads_no_bootstrap_runs(org_b, shared):
    """These are type fields, not @tenant_scoped resolvers. Without a tenant
    the shared-cluster union and the NULL-org rows would both match."""
    ClusterBootstrapRun.objects.create(
        tenant_cluster=shared,
        organization=org_b,
        status="succeeded",
        started_at=dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.UTC),
        ended_at=dt.datetime(2026, 9, 1, 9, 2, 0, tzinfo=dt.UTC),
    )
    ClusterBootstrapRun.objects.create(
        tenant_cluster=shared,
        status="succeeded",
        started_at=dt.datetime(2026, 9, 1, 10, 0, 0, tzinfo=dt.UTC),
        ended_at=dt.datetime(2026, 9, 1, 10, 2, 0, tzinfo=dt.UTC),
    )
    parent = cluster_to_type(shared)

    assert parent.bootstrap_runs() == []
    assert parent.last_bootstrap_run() is None


def test_install_prereqs_hands_the_requesting_org_to_the_workflow(org_b, user_b, shared, monkeypatch):
    # Installing into a shared cluster is the platform operator's (#1918).
    user_b.is_superuser = True
    user_b.save(update_fields=["is_superuser"])
    starts = []
    monkeypatch.setattr(
        "astrolift_clusters.schema.mutations.start_workflow",
        lambda name, args, **kwargs: starts.append((name, args)),
    )
    with _ctx(org_b, user_b):
        result = ClustersMutation().install_cluster_prereqs(
            _info(user_b),
            InstallClusterPrereqsInputType(
                cluster_id=GUID(str(shared.guid)), selected_components=["cert-manager"]
            ),
        )
    assert result.ok is True, result.errors

    [(name, [workflow_input])] = starts
    assert name == "InstallClusterPrereqsWorkflow"
    assert workflow_input.organization_id == org_b.id


def test_backfill_gives_legacy_runs_their_clusters_org(org_a, plugin, shared):
    migration = importlib.import_module("astrolift_clusters.migrations.0017_clusterbootstraprun_organization")
    owned = _cluster(org_a, plugin)
    started = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.UTC)
    legacy_owned = ClusterBootstrapRun.objects.create(
        tenant_cluster=owned, status="succeeded", started_at=started, ended_at=started
    )
    legacy_shared = ClusterBootstrapRun.objects.create(
        tenant_cluster=shared, status="succeeded", started_at=started, ended_at=started
    )

    migration.backfill_organization(django_apps, None)

    legacy_owned.refresh_from_db()
    legacy_shared.refresh_from_db()
    assert legacy_owned.organization_id == org_a.id
    assert legacy_shared.organization_id is None
