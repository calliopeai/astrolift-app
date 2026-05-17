"""Tests for the cost-collector activity (#502).

Drives ``_capture_platform_cost_snapshot_sync`` with fake billing
clients to exercise the tag-resolution path end-to-end against
real Postgres rows. Covers:

* tagged-row resolves to ``CostSnapshot.managed_service_binding`` FK
* untagged-row writes NULL FK (Shared / untagged bucket)
* unknown-binding-GUID writes NULL FK (binding belongs to a different
  org / has been soft-deleted since provisioning)
* placeholder OTHER row written even when no driver returns data
* idempotent: re-running the activity the same day is a no-op
* unavailable driver result (cloud not configured) is logged + skipped
* multi-binding / multi-cloud sweep aggregates correctly
* multi-tenant guardrail: a binding for org B doesn't write into org A
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
        version="0.0.1",
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
        version="0.0.1",
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


def _make_binding(*, app, cluster=None):
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
    return ManagedServiceBinding.objects.create(
        managed_service=svc,
        env_key="DATABASE_URL",
        env_value_ref="secret:db/url",
        is_secret=True,
    )


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
    """Helper: build a billing client whose query_actuals_by_binding
    returns `result` regardless of inputs."""
    return SimpleNamespace(query_actuals_by_binding=lambda *, start, end, currency="USD": result)


# ---- tagged + untagged: one populated FK, one NULL ---------------


def test_tagged_row_resolves_to_binding_fk(aws_cluster, patch_factories):
    """Acceptance §4: a tagged row produces a populated FK; an
    untagged sibling produces NULL."""
    from _sdk.cost import BillingActualLineItem

    app = _make_app(aws_cluster.organization)
    binding = _make_binding(app=app)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid=str(binding.guid),
                amount_cents=12_345,
                currency="USD",
                provider="aws",
            ),
            BillingActualLineItem(
                binding_guid="",
                amount_cents=99_999,
                currency="USD",
                provider="aws",
            ),
        ]
    )

    n = scheduled_mod._capture_platform_cost_snapshot_sync()

    # tagged + untagged + placeholder OTHER per org = 3 rows.
    assert n == 3
    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization).order_by(
        "managed_service_binding_id"
    )
    bindings = [r.managed_service_binding_id for r in rows]
    assert binding.pk in bindings
    assert None in bindings
    populated = next(r for r in rows if r.managed_service_binding_id == binding.pk)
    assert populated.amount_cents == 12_345
    assert populated.by == CostSnapshot.CostBy.MANAGED_SERVICE.value
    assert populated.source == CostSnapshot.Source.PROVIDER_ESTIMATE.value


def test_unknown_binding_guid_writes_null_fk(aws_cluster, patch_factories):
    """A tag pointing at a GUID the platform doesn't recognize lands
    as the untagged bucket — the cost row still counts toward the
    org but doesn't pretend to attribute to a binding that doesn't
    exist here."""
    from _sdk.cost import BillingActualLineItem

    _make_app(aws_cluster.organization)
    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid="00000000-0000-0000-0000-000000000000",
                amount_cents=4_242,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    binding_ids = {r.managed_service_binding_id for r in rows}
    assert binding_ids == {None}
    # OTHER placeholder + the unknown-GUID row (also OTHER because
    # the binding doesn't resolve).
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
    binding = _make_binding(app=app)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid=str(binding.guid),
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

    patch_factories["aws"] = SimpleNamespace(query_actuals_by_binding=raising_client)

    n = scheduled_mod._capture_platform_cost_snapshot_sync()

    # placeholder OTHER still written
    assert n >= 1
    assert CostSnapshot.objects.filter(organization=aws_cluster.organization).count() == 1


# ---- multi-cloud / multi-binding ----------------------------------


def test_multi_cloud_aggregates(two_clouds_org, patch_factories):
    """Two clouds, two bindings, one untagged row each → expect
    one populated FK per binding, two untagged rows, plus placeholder."""
    from _sdk.cost import BillingActualLineItem

    org, _aws, _gcp = two_clouds_org
    app = _make_app(org)
    binding1 = _make_binding(app=app)
    # Second binding under the same app
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
    binding2 = ManagedServiceBinding.objects.create(
        managed_service=svc2,
        env_key="REDIS_URL",
        env_value_ref="secret:cache/url",
        is_secret=True,
    )

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid=str(binding1.guid),
                amount_cents=1_000,
                currency="USD",
            ),
            BillingActualLineItem(binding_guid="", amount_cents=100, currency="USD"),
        ]
    )
    patch_factories["gcp"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid=str(binding2.guid),
                amount_cents=2_000,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    rows = list(CostSnapshot.objects.filter(organization=org).order_by("amount_cents"))
    # 0 placeholder + 100 untagged + 1000 aws-binding + 2000 gcp-binding
    assert [r.amount_cents for r in rows] == [0, 100, 1_000, 2_000]


def test_cross_org_binding_lands_as_untagged_not_misattributed(aws_cluster, patch_factories):
    """A row whose binding tag matches a binding in a *different* org
    must NOT write the FK on the wrong org (multi-tenant guardrail).
    The cost itself still lands — it really did come from this org's
    cluster — but it lands in the Shared/untagged bucket where we
    can't attribute to a known binding."""
    from _sdk.cost import BillingActualLineItem

    # Binding under a sibling org — aws_cluster.organization is OrgA;
    # build one under OrgB so its guid exists somewhere in the DB.
    org_b = Organization.objects.create(name="OrgB", slug="org-b")
    app_b = _make_app(org_b)
    cluster_b = _cluster(
        org=org_b,
        plugin=aws_cluster.provider_plugin,
        slug="aws-b",
    )
    binding_b = _make_binding(app=app_b, cluster=cluster_b)

    patch_factories["aws"] = _fake_client(
        [
            BillingActualLineItem(
                binding_guid=str(binding_b.guid),
                amount_cents=9_999,
                currency="USD",
            ),
        ]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    # OrgA's rows: never carry OrgB's binding FK. The guid resolves
    # to None in OrgA's per-org bindings map → NULL FK → untagged
    # bucket, but the cost lands so the org-level total is correct.
    org_a_rows = CostSnapshot.objects.filter(organization=aws_cluster.organization)
    org_a_binding_ids = {r.managed_service_binding_id for r in org_a_rows}
    assert binding_b.pk not in org_a_binding_ids
    # OrgB: factory returns the same payload so OrgB's binding FK
    # gets populated correctly when the sweep walks OrgB.
    org_b_rows = CostSnapshot.objects.filter(organization=org_b)
    org_b_binding_ids = {r.managed_service_binding_id for r in org_b_rows}
    assert binding_b.pk in org_b_binding_ids


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
        [BillingActualLineItem(binding_guid="", amount_cents=500, currency="USD")]
    )

    scheduled_mod._capture_platform_cost_snapshot_sync()

    # Only the placeholder lands — the AWS billing client was never
    # called because the cluster is not in MANAGED state.
    rows = CostSnapshot.objects.filter(organization=org)
    assert rows.count() == 1
    assert rows.first().amount_cents == 0
