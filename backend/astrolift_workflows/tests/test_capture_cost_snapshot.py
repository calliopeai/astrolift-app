"""Tests for the cost-collector activity (#502, regrained by #1418).

Drives ``_capture_platform_cost_snapshot_sync`` with fake billing
clients to exercise the tag-resolution path end-to-end against
real Postgres rows. Covers:

* tagged-row resolves to ``CostSnapshot.managed_service`` FK
* untagged-row writes NULL FK (Shared / untagged bucket)
* unknown service GUID writes NULL FK (service belongs to a different
  org / has been soft-deleted since provisioning)
* placeholder OTHER row written even when no driver returns data
* idempotent: re-running the activity the same day is a no-op
* unavailable driver result (cloud not configured) is logged + skipped
* multi-service / multi-cloud sweep aggregates correctly
* multi-tenant guardrail: a service for org B doesn't write into org A
* project-owned services attribute too, not just app-owned ones

Attribution keyed on ``ManagedServiceBinding`` until #1418 and never
resolved a single row in production, because nothing ever stamped the
binding tag. These tests pass with the *service* GUID stamped, which
is the identifier drivers actually write.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_billing.models import CostSnapshot
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities import scheduled as scheduled_mod

pytestmark = pytest.mark.django_db


# ---- fixtures ------------------------------------------------------


@pytest.fixture
def aws_plugin():
    plugin = ProviderPlugin(
        name="AWS",
        slug="aws",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug="aws")


@pytest.fixture
def gcp_plugin():
    plugin = ProviderPlugin(
        name="GCP",
        slug="gcp",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug="gcp")


@pytest.fixture
def aws_cluster(aws_plugin):
    org = Organization.objects.create(name="OrgA", slug="org-a")
    return _cluster(org=org, plugin=aws_plugin, slug="aws-prod")


@pytest.fixture
def two_clouds_org(aws_plugin, gcp_plugin):
    org = Organization.objects.create(name="OrgM", slug="org-m")
    aws = _cluster(org=org, plugin=aws_plugin, slug="aws-m")
    gcp = _cluster(org=org, plugin=gcp_plugin, slug="gcp-m")
    return org, aws, gcp


def _cluster(*, org, plugin, slug):
    return TenantCluster.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint=f"https://{slug}.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _make_app(org):
    team = Team.objects.create(organization=org, name="t", slug=f"team-{org.slug}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"proj-{org.slug}")
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="App",
        slug=f"app-{org.slug}",
        provisioning_status="ready",
    )


def _make_service(*, app, cluster=None):
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster or app.organization.tenant_clusters.first(),
        name="prod",
        url="https://x.invalid",
        required_approvals=0,
    )
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES.value,
        name="db",
        variant="rds",
        status=ManagedService.Status.ACTIVE.value,
    )
    # A real service carries binding rows; create one so the tests
    # exercise a service that looks like production rather than a
    # bare row, and so a regression back to binding-grain attribution
    # would have something to (wrongly) resolve against.
    ManagedServiceBinding.objects.create(
        managed_service=svc,
        env_key="DATABASE_URL",
        env_value_ref="secret:db/url",
        is_secret=True,
    )
    return svc


@pytest.fixture
def patch_factories(monkeypatch):
    """Yield a dict the test fills with provider_slug -> client. The
    factory registry returns those clients (or None) on lookup."""

    holder: dict[str, Any] = {}

    def factory_for(slug):
        client = holder.get(slug)
        if client is None:
            return None
        return lambda cluster: client

    # Replace the resolver entirely so the lazy-default isn't tripped.
    monkeypatch.setattr(
        scheduled_mod,
        "_resolve_actuals_factory",
        factory_for,
    )
    return holder


def _fake_client(result):
    """Helper: build a billing client whose query_actuals_by_service
    returns `result` regardless of inputs."""
    return SimpleNamespace(query_actuals_by_service=lambda *, start, end, currency="USD": result)


# ---- tagged + untagged: one populated FK, one NULL ---------------


def test_tagged_row_resolves_to_service_fk(aws_cluster, patch_factories):
    """Acceptance §4: a tagged row produces a populated FK; an
    untagged sibling produces NULL."""
    from _sdk.cost import BillingActualLineItem

    app = _make_app(aws_cluster.organization)
    service = _make_service(app=app)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(service.guid),
                amount_cents=12_345,
                currency="USD",
                provider="aws",
            ),
            BillingActualLineItem(
                managed_service_guid="",
                amount_cents=99_999,
                currency="USD",
                provider="aws",
            ),
        ]
    )

    n = scheduled_mod._capture_platform_cost_snapshot_sync()

    # tagged + untagged + placeholder OTHER per org = 3 rows.
    assert n == 3
    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization).order_by("managed_service_id")
    services = [r.managed_service_id for r in rows]
    assert service.pk in services
    assert None in services
    populated = next(r for r in rows if r.managed_service_id == service.pk)
    assert populated.amount_cents == 12_345
    assert populated.by == CostSnapshot.CostBy.MANAGED_SERVICE.value
    assert populated.source == CostSnapshot.Source.PROVIDER_ESTIMATE.value


def test_unknown_managed_service_guid_writes_null_fk(aws_cluster, patch_factories):
    """A tag pointing at a GUID the platform doesn't recognize lands
    as the untagged bucket — the cost row still counts toward the
    org but doesn't pretend to attribute to a service that doesn't
    exist here."""
    from _sdk.cost import BillingActualLineItem

    _make_app(aws_cluster.organization)
    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid="00000000-0000-0000-0000-000000000000",
                amount_cents=4_242,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    service_ids = {r.managed_service_id for r in rows}
    assert service_ids == {None}
    # OTHER placeholder + the unknown-GUID row (also OTHER because
    # the service doesn't resolve).
    assert sum(r.amount_cents for r in rows) == 4_242


# ---- placeholder + empty cases -----------------------------------


def test_placeholder_other_row_always_written(aws_cluster, patch_factories):
    """No driver registered → placeholder OTHER row still lands so
    the trend chart x-axis stays continuous."""
    # No factories registered = no actuals client.
    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    assert rows.count() == 1
    placeholder = rows.first()
    assert placeholder.by == CostSnapshot.CostBy.OTHER.value
    assert placeholder.amount_cents == 0
    assert placeholder.source == CostSnapshot.Source.PLATFORM_METER.value


def test_idempotent_on_same_day_rerun(aws_cluster, patch_factories):
    """Re-running the activity within the same UTC day adds zero
    new rows — the model unique constraint guards against double-
    writes."""
    from _sdk.cost import BillingActualLineItem

    app = _make_app(aws_cluster.organization)
    service = _make_service(app=app)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(service.guid),
                amount_cents=5_000,
                currency="USD",
            ),
        ]
    )

    first = scheduled_mod._capture_platform_cost_snapshot_sync()
    second = scheduled_mod._capture_platform_cost_snapshot_sync()
    assert first > 0
    assert second == 0


# ---- unavailable / failure modes ----------------------------------


def test_unavailable_driver_logs_and_skips(aws_cluster, patch_factories, caplog):
    """When a cloud's billing API is unreachable / not enabled, the
    activity logs + still emits the placeholder OTHER row."""
    from _sdk.cost import BillingActualsUnavailable

    patch_factories["aws"] = _fake_client(
        BillingActualsUnavailable(reason="not_enabled", message="enable cost allocation tag")
    )

    n = scheduled_mod._capture_platform_cost_snapshot_sync()

    assert n == 1  # just the placeholder
    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    assert {r.amount_cents for r in rows} == {0}


def test_query_raise_does_not_break_sweep(aws_cluster, patch_factories):
    """A raise inside the billing client doesn't poison the sweep —
    other orgs / clouds still get their snapshots."""

    def raising_client(*, start, end, currency="USD"):
        raise RuntimeError("billing API timeout")

    patch_factories["aws"] = SimpleNamespace(query_actuals_by_service=raising_client)

    n = scheduled_mod._capture_platform_cost_snapshot_sync()

    # placeholder OTHER still written
    assert n >= 1
    assert CostSnapshot.objects.filter(organization=aws_cluster.organization).count() == 1


# ---- multi-cloud / multi-service ----------------------------------


def test_multi_cloud_aggregates(two_clouds_org, patch_factories):
    """Two clouds, two services, one untagged row each → expect
    one populated FK per service, two untagged rows, plus placeholder."""
    from _sdk.cost import BillingActualLineItem

    org, _aws, _gcp = two_clouds_org
    app = _make_app(org)
    service1 = _make_service(app=app)
    # Second service under the same app
    env = app.environments.first() or AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=None,
        name="staging",
        url="https://stg.invalid",
        required_approvals=0,
    )
    svc2 = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.REDIS.value,
        name="cache",
        variant="elasticache",
        status=ManagedService.Status.ACTIVE.value,
    )
    ManagedServiceBinding.objects.create(
        managed_service=svc2,
        env_key="REDIS_URL",
        env_value_ref="secret:cache/url",
        is_secret=True,
    )
    service2 = svc2

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(service1.guid),
                amount_cents=1_000,
                currency="USD",
            ),
            BillingActualLineItem(managed_service_guid="", amount_cents=100, currency="USD"),
        ]
    )
    patch_factories["gcp"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(service2.guid),
                amount_cents=2_000,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = list(CostSnapshot.objects.filter(organization=org).order_by("amount_cents"))
    # 0 placeholder + 100 untagged + 1000 aws-service + 2000 gcp-service
    assert [r.amount_cents for r in rows] == [0, 100, 1_000, 2_000]


def test_cross_org_service_lands_as_untagged_not_misattributed(aws_cluster, patch_factories):
    """A row whose service tag matches a service in a *different* org
    must NOT write the FK on the wrong org (multi-tenant guardrail).
    The cost itself still lands — it really did come from this org's
    cluster — but it lands in the Shared/untagged bucket where we
    can't attribute to a known service."""
    from _sdk.cost import BillingActualLineItem

    # Service under a sibling org — aws_cluster.organization is OrgA;
    # build one under OrgB so its guid exists somewhere in the DB.
    org_b = Organization.objects.create(name="OrgB", slug="org-b")
    app_b = _make_app(org_b)
    cluster_b = _cluster(
        org=org_b,
        plugin=aws_cluster.provider_plugin,
        slug="aws-b",
    )
    service_b = _make_service(app=app_b, cluster=cluster_b)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(service_b.guid),
                amount_cents=9_999,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    # OrgA's rows: never carry OrgB's service FK. The guid resolves
    # to None in OrgA's per-org services map → NULL FK → untagged
    # bucket, but the cost lands so the org-level total is correct.
    org_a_rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    org_a_service_ids = {r.managed_service_id for r in org_a_rows}
    assert service_b.pk not in org_a_service_ids
    # OrgB: factory returns the same payload so OrgB's service FK
    # gets populated correctly when the sweep walks OrgB.
    org_b_rows = CostSnapshot.objects.filter(organization=org_b)
    org_b_service_ids = {r.managed_service_id for r in org_b_rows}
    assert service_b.pk in org_b_service_ids


# ---- only managed clusters considered ------------------------------


def test_unmanaged_clusters_skipped(aws_plugin, patch_factories):
    """A `registered` cluster (not yet brought into management) is
    skipped — only `managed` clusters contribute to actuals."""
    from _sdk.cost import BillingActualLineItem

    org = Organization.objects.create(name="OrgU", slug="org-u")
    TenantCluster.objects.create(
        organization=org,
        name="aws-pending",
        slug="aws-pending",
        provider_plugin=aws_plugin,
        provider_config={},
        endpoint="https://pending.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.REGISTERED.value,
    )

    patch_factories["aws"] = _fake_client(
        [BillingActualLineItem(managed_service_guid="", amount_cents=500, currency="USD")]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    # Only the placeholder lands — the AWS billing client was never
    # called because the cluster is not in MANAGED state.
    rows = CostSnapshot.objects.filter(organization=org)
    assert rows.count() == 1
    assert rows.first().amount_cents == 0


# ---- the regression this whole change exists to prevent -------------


def test_binding_guid_alone_attributes_nothing(aws_cluster, patch_factories):
    """The failure #1418 describes, pinned so it cannot come back.

    A driver that reports the *binding* GUID rather than the service
    GUID must not resolve. This is what production did for the entire
    life of the feature: the tag the collector grouped on was one no
    driver ever wrote, so every row silently fell into the untagged
    bucket and the cost panel showed a plausible, empty answer.

    Attributing this row would be worse than dropping it — it would
    mean the collector guesses at a grain the billing data does not
    have.
    """
    from _sdk.cost import BillingActualLineItem

    app = _make_app(aws_cluster.organization)
    service = _make_service(app=app)
    binding = service.bindings.get()

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(binding.guid),
                amount_cents=7_777,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    assert {r.managed_service_id for r in rows} == {None}
    # The spend is still recorded against the org — it was really
    # incurred — it just isn't attributed to a service.
    assert sum(r.amount_cents for r in rows) == 7_777


# ---- project-owned services -----------------------------------------


def test_project_owned_service_attributes(aws_cluster, patch_factories):
    """A managed service is owned by exactly one of registered_app /
    project (model check constraint). Scoping the per-org cache to
    ``registered_app__organization`` alone drops every project-owned
    shared resource into the untagged bucket, which is precisely the
    silent-undercount this change is meant to end.
    """
    from _sdk.cost import BillingActualLineItem

    org = aws_cluster.organization
    team = Team.objects.create(organization=org, name="shared", slug="team-shared")
    project = Project.objects.create(organization=org, team=team, name="Shared", slug="proj-shared")
    svc = ManagedService.objects.create(
        project=project,
        tenant_cluster=aws_cluster,
        kind=ManagedService.Kind.POSTGRES.value,
        name="shared-db",
        variant="rds",
        status=ManagedService.Status.ACTIVE.value,
    )

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                managed_service_guid=str(svc.guid),
                amount_cents=3_100,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    attributed = CostSnapshot.objects.get(organization=org, managed_service=svc)
    assert attributed.amount_cents == 3_100
    assert attributed.project_id == project.pk
    assert attributed.registered_app_id is None
    assert attributed.by == CostSnapshot.CostBy.MANAGED_SERVICE.value
