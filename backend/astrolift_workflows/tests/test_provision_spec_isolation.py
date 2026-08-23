"""``build_provision_spec`` carries the resolved isolation mode (#16).

Nineteen production drivers read ``ProvisionSpec.isolation``. Four size the
backing resource off it -- DocumentDB and Neptune instance counts, MemoryDB
replicas per shard, Memcached nodes -- and the rest stamp it as a cloud tag or
label. The field defaults to ``"shared"`` and the builder never set it, so a
service asked for with dedicated isolation was provisioned single-node and
tagged as shared, and an org's compliance floor could not be enforced at all.

The builder is shared with the authorized-adoption path, so these also pin
that an adopted resource carries the same mode a created one would.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import build_provision_spec

pytestmark = pytest.mark.django_db


def _scaffold(suffix: str, *, requested: str = "", policy: dict | None = None):
    org = Organization.objects.create(
        name=f"Acme {suffix}",
        slug=f"acme-iso-{suffix}",
        managed_service_isolation_policy=policy or {},
    )
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-iso-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-iso-{suffix}")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"eks-iso-{suffix}",
        name="EKS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        endpoint="https://eks.example.com",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-iso-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="db",
        variant="rds_postgres",
        isolation=requested,
        status=ManagedService.Status.ACTIVE,
    )
    return org, svc, cluster


def test_a_dedicated_request_reaches_the_spec():
    _, svc, cluster = _scaffold("a", requested="dedicated")

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.isolation == "dedicated"


def test_no_request_and_no_policy_stays_shared():
    # The mode the spec has always defaulted to. Wiring the resolver must not
    # silently move every existing service onto dedicated hardware.
    _, svc, cluster = _scaffold("b")

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.isolation == "shared"


def test_the_org_policy_supplies_a_mode_nobody_requested():
    _, svc, cluster = _scaffold("c", policy={"postgres": "dedicated"})

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.isolation == "dedicated"


def test_the_org_policy_outranks_a_weaker_request():
    # The floor is the whole point: an app must not be able to put a regulated
    # kind back onto a shared backing instance by asking nicely.
    _, svc, cluster = _scaffold("d", requested="shared", policy={"postgres": "dedicated"})

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.isolation == "dedicated"


def test_a_policy_for_another_kind_leaves_this_one_alone():
    _, svc, cluster = _scaffold("e", requested="dedicated", policy={"redis": "dedicated"})

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.isolation == "dedicated"

    other = ManagedService.objects.create(
        registered_app=svc.registered_app,
        app_environment=svc.app_environment,
        kind=ManagedService.Kind.REDIS,
        name="cache",
        variant="elasticache",
        status=ManagedService.Status.ACTIVE,
    )
    assert build_provision_spec(other, cluster=cluster).isolation == "dedicated"


def test_portable_extensions_reach_the_driver_contract_not_provider_config():
    _, svc, cluster = _scaffold("extensions")
    svc.config = {
        "size": "small",
        "desired_extensions": ["vector", "pg_trgm", "vector"],
    }
    svc.save(update_fields=["config"])

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.desired_extensions == ["vector", "pg_trgm"]
    assert "desired_extensions" not in spec.config
