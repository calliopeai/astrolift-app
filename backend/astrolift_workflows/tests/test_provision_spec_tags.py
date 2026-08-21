"""``build_provision_spec`` carries the org's operator tags (#1505).

`ProvisionSpec.tags` is read by twenty-one production drivers -- AWS
namespaces it under ``astrolift.io/extra/``, Azure passes it as
``custom_tags``, GCP merges it into labels -- and `build_provision_spec`
never set it. So every one of them read an empty dict on every provision,
forever, and `cost_center` had no path to a cloud resource at all.

The builder is shared with the authorized-adoption path deliberately, so
an adopted resource carries the same envelope a created one would; these
assert the tags travel with it rather than being a provision-only extra.
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


def _scaffold(suffix: str, *, tags: dict | None = None):
    org = Organization.objects.create(
        name=f"Acme {suffix}",
        slug=f"acme-tags-{suffix}",
        **({"default_resource_tags": tags} if tags is not None else {}),
    )
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-tags-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-tags-{suffix}")
    # bulk_create, not create: ProviderPlugin.version is a semver
    # CharField that shadows BaseCoreModel.version, the optimistic-lock
    # integer, so save() evaluates `"0.0.1" + 1` and raises. bulk_create
    # skips save(). The rest of the suite routes around it the same way
    # (#1517).
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"eks-tags-{suffix}",
        name="EKS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        endpoint="https://eks.example.com",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-tags-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="db",
        variant="rds_postgres",
        status=ManagedService.Status.ACTIVE,
    )
    return org, svc, cluster


def test_the_orgs_tags_reach_the_spec():
    _, svc, cluster = _scaffold("a", tags={"cost_center": "platform-rnd", "owner": "infra"})

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.tags == {"cost_center": "platform-rnd", "owner": "infra"}


def test_an_org_with_no_tags_still_provisions():
    # The default is `{}` and every driver already handles an empty map;
    # the point is that it stays a dict rather than becoming None.
    _, svc, cluster = _scaffold("b")

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.tags == {}


def test_the_spec_holds_a_copy_the_org_cannot_mutate():
    # The spec crosses into a Temporal activity and a driver; handing out
    # the model's own dict would let a driver's normalisation write back
    # into the organization row.
    org, svc, cluster = _scaffold("c", tags={"cost_center": "platform-rnd"})

    spec = build_provision_spec(svc, cluster=cluster)
    spec.tags["cost_center"] = "mutated"

    org.refresh_from_db()
    assert org.default_resource_tags == {"cost_center": "platform-rnd"}


def test_the_platform_envelope_is_unchanged_by_the_operator_tags():
    # Operator tags are a separate channel. If they could displace org /
    # app / env, a mis-set tag would break cost attribution rather than
    # extending it.
    _, svc, cluster = _scaffold("d", tags={"cost_center": "platform-rnd"})

    spec = build_provision_spec(svc, cluster=cluster)

    assert spec.organization_slug == "acme-tags-d"
    assert spec.app_slug == "shop-tags-d"
    assert spec.environment_name == "production"
    assert "cost_center" not in {
        spec.organization_slug,
        spec.app_slug,
        spec.environment_name,
    }
