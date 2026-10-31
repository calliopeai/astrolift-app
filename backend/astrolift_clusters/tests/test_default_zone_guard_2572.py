"""A zone new apps resolve through is not removed by accident (#2572).

On a production install the platform-level zone (organization NULL, default
for both) was soft-deleted from the Domains screen with no guard. Existing
apps kept serving, so nothing looked wrong; every app registered afterwards
came up with no hostname, no Ingress, and still read Ready. Recovery took a
manual ``deleted_at = NULL`` and ``manage.py backfill_managed_domain`` from a
shell on the webservice task.

``softDeleteManagedDomain`` now keeps an effective default zone unless the
caller passes ``force``, and names what resolves through it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, effective_default_ids
from astrolift_clusters.schema import mutations as mut
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.tests.test_heartbeat import (  # noqa: F401 (fixture)
    _make_cluster,
    _no_opensearch_profile_index,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.helpers import _bootstrap_app_environments
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_temporal(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture
def started(monkeypatch):
    """The workflows the delete starts, recorded instead of submitted."""
    names: list[str] = []
    monkeypatch.setattr(mut, "start_workflow", lambda name, **_: names.append(name))
    return names


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-2572-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug=f"globex-2572-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def cluster():
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug=f"k8s-2572-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    # Shared: every org places apps on it, and the delete deprovisions through it.
    return _make_cluster(None, plugin)


@pytest.fixture
def grants(permission_resolver):
    for permission in (
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        Permission.PROVIDER_PLUGIN_READ,
        Permission.APP_READ,
    ):
        permission_resolver.grant(permission)
    return permission_resolver


def _operator():
    return User.objects.create(username=f"root-{uuid.uuid4().hex[:6]}", is_superuser=True)


def _org_admin():
    return User.objects.create(username=f"admin-{uuid.uuid4().hex[:6]}")


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _zone(organization, zone, default_for, **fields):
    return ManagedDomain.objects.create(
        organization=organization,
        zone=zone,
        dns_driver=fields.pop("dns_driver", "route53"),
        default_for=default_for,
        provision_state="mark_active",
        **fields,
    )


def _app(organization, name):
    suffix = uuid.uuid4().hex[:6]
    team = Team.objects.create(organization=organization, name="Eng", slug=f"eng-{suffix}")
    return RegisteredApp.objects.create(
        organization=organization,
        team=team,
        name=name,
        slug=f"{name}-{suffix}",
        provisioning_status="ready",
    )


def _register(organization, cluster, name):
    """Place an app's first environment the way registration does."""
    app = _app(organization, name)
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    _bootstrap_app_environments(app, ["production"])
    return AppEnvironment.objects.select_related("registered_app").get(registered_app=app, name="production")


def _delete(organization, user, domain, *, force=False):
    with tenant_context(TenantContext(organization_id=organization.id, actor_user_id=user.id)):
        return mut.ClustersMutation().soft_delete_managed_domain(
            _info(user), mut.SoftDeleteManagedDomainInput(id=GUID(str(domain.guid)), force=force)
        )


def _deleted(domain) -> bool:
    return ManagedDomain.all_objects.get(pk=domain.pk).deleted_at is not None


def _path(env) -> str:
    app = env.registered_app
    return f"{app.organization.slug}/{app.slug}/{env.name}"


# ---- the guard -----------------------------------------------------


def test_the_platform_default_is_kept_and_its_dependents_named_without_force(org, cluster, grants, started):
    platform = _zone(None, "apps-2572.example", "both")
    env = _register(org, cluster, "storefront")
    assert env.managed_domain_id == platform.pk

    result = _delete(org, _operator(), platform)

    assert result.ok is False
    error = result.errors[0]
    assert (error.code, error.field) == ("PRECONDITION", "force")
    assert error.message.startswith("apps-2572.example is the effective default zone for new app hostnames.")
    assert f"1 environment resolves through it: {_path(env)}." in error.message
    assert error.dependents.total == 1
    assert [(d.organization_slug, d.app_slug, d.environment_name, d.url) for d in error.dependents.items] == [
        (org.slug, env.registered_app.slug, "production", env.url)
    ]
    assert not _deleted(platform)
    assert started == []


def test_the_refusal_names_ten_environments_and_counts_the_rest(org, cluster, grants, started):
    platform = _zone(None, "apps-2572.example", "both")
    for index in range(12):
        AppEnvironment.objects.create(
            registered_app=_app(org, f"svc{index:02d}"),
            name="production",
            tenant_cluster=cluster,
            managed_domain=platform,
            url=f"https://svc{index:02d}.apps-2572.example",
        )

    error = _delete(org, _operator(), platform).errors[0]

    assert error.dependents.total == 12
    assert [d.app_slug[:5] for d in error.dependents.items] == [f"svc{index:02d}" for index in range(10)]
    assert "12 environments resolve through it: " in error.message
    assert error.message.count(f"{org.slug}/svc") == 10
    assert ", and 2 more." in error.message
    assert not _deleted(platform)


@pytest.mark.parametrize("how", ["only_resolving_zone", "explicit_default"])
def test_an_organizations_default_zone_is_kept_without_force(org, cluster, grants, started, how):
    own = _zone(org, "own-2572.example", "tenant_apps" if how == "only_resolving_zone" else "none")
    if how == "explicit_default":
        org.default_managed_domain = own
        org.save(update_fields=["default_managed_domain"])

    result = _delete(org, _org_admin(), own)

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].dependents.total == 0
    assert "No environment resolves through it yet." in result.errors[0].message
    assert not _deleted(own)
    assert started == []


def test_force_removes_an_effective_default_and_deprovisions_it(org, cluster, grants, started):
    platform = _zone(None, "apps-2572.example", "both")
    _register(org, cluster, "storefront")

    result = _delete(org, _operator(), platform, force=True)

    assert result.ok, result.errors
    assert _deleted(platform)
    assert started == ["DeprovisionManagedDomainWorkflow"]


def test_an_ordinary_zone_is_removed_without_force(org, cluster, grants, started):
    _zone(None, "apps-2572.example", "both")
    ordinary = _zone(org, "spare-2572.example", "none")

    result = _delete(org, _org_admin(), ordinary)

    assert result.ok, result.errors
    assert _deleted(ordinary)
    assert started == ["DeprovisionManagedDomainWorkflow"]


def test_force_does_not_open_a_shared_zone_to_an_org_admin(org, cluster, grants, started):
    platform = _zone(None, "apps-2572.example", "both")

    result = _delete(org, _org_admin(), platform, force=True)

    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not _deleted(platform)
    assert started == []


def test_only_a_zone_resolution_picks_is_an_effective_default(org):
    first = _zone(None, "first-2572.example", "both")
    shadowed = _zone(None, "second-2572.example", "both")
    previews = _zone(None, "previews-2572.example", "preview_envs")
    pending = _zone(None, "pending-2572.example", "tenant_apps", verification_state="pending")
    read_only = _zone(None, "ro-2572.example", "tenant_apps", dns_driver="cloudflare_read_only")
    own = _zone(org, "own-2572.example", "tenant_apps")
    spare = _zone(org, "spare-2572.example", "none")
    zones = [first, shadowed, previews, pending, read_only, own, spare]

    assert effective_default_ids(zones) == {first.pk, own.pk}

    # An explicit org default outranks the org's own default_for rows; the
    # shared zone stays the pick for every org without a zone of its own.
    org.default_managed_domain = spare
    org.save(update_fields=["default_managed_domain"])
    assert effective_default_ids(zones) == {first.pk, spare.pk}


# ---- what the Domains screen reads ---------------------------------


def _listed(organization, user, zone):
    with tenant_context(TenantContext(organization_id=organization.id, actor_user_id=user.id)):
        info = _info(user)
        row = next(d for d in ClustersQuery().astrolift_managed_domains(info) if d.zone == zone.zone)
        return (
            row.is_effective_default,
            row.dependent_app_count(info),
            [d.app_slug for d in row.dependent_apps(info)],
        )


def test_a_shared_zones_dependents_span_organizations_for_the_operator_only(org, other_org, cluster, grants):
    platform = _zone(None, "apps-2572.example", "both")
    mine = _register(org, cluster, "storefront").registered_app.slug
    theirs = _register(other_org, cluster, "billing").registered_app.slug

    # Dependents sort by organization slug first: acme-* before globex-*.
    assert _listed(org, _operator(), platform) == (True, 2, [mine, theirs])
    assert _listed(org, _org_admin(), platform) == (True, 1, [mine])
    assert _listed(other_org, _org_admin(), platform) == (True, 1, [theirs])


def test_the_list_marks_only_the_effective_default(org, cluster, grants):
    platform = _zone(None, "apps-2572.example", "both")
    spare = _zone(org, "spare-2572.example", "none")

    assert _listed(org, _org_admin(), platform)[0] is True
    assert _listed(org, _org_admin(), spare) == (False, 0, [])


# ---- the wire --------------------------------------------------------


def _execute(organization, user, query, variables):
    from config.schema import schema

    with tenant_context(TenantContext(organization_id=organization.id, actor_user_id=user.id)):
        result = schema.execute_sync(
            query, variable_values=variables, context_value=SimpleNamespace(user=user, request=None)
        )
    assert result.errors is None, result.errors
    return result.data


DELETE = """
mutation($id: GUID!, $force: Boolean!) {
  softDeleteManagedDomain(input: {id: $id, force: $force}) {
    ok
    errors { code field dependents { total items { organizationSlug appSlug environmentName url } } }
    data { id deleted }
  }
}
"""


def test_the_refusal_and_force_over_the_schema(org, cluster, grants, started):
    operator = _operator()
    platform = _zone(None, "apps-2572.example", "both")
    env = _register(org, cluster, "storefront")
    variables = {"id": str(platform.guid), "force": False}

    refused = _execute(org, operator, DELETE, variables)["softDeleteManagedDomain"]

    assert refused["ok"] is False
    assert refused["errors"][0]["code"] == "PRECONDITION"
    assert refused["errors"][0]["field"] == "force"
    assert refused["errors"][0]["dependents"] == {
        "total": 1,
        "items": [
            {
                "organizationSlug": org.slug,
                "appSlug": env.registered_app.slug,
                "environmentName": "production",
                "url": env.url,
            }
        ],
    }
    forced = _execute(org, operator, DELETE, {**variables, "force": True})["softDeleteManagedDomain"]
    assert forced["ok"] is True
    assert forced["data"] == {"id": str(platform.guid), "deleted": True}


def test_the_domain_list_reports_the_default_and_its_dependents_over_the_schema(org, cluster, grants):
    platform = _zone(None, "apps-2572.example", "both")
    _zone(org, "spare-2572.example", "none")
    env = _register(org, cluster, "storefront")
    query = (
        "{ astroliftManagedDomains { zone isEffectiveDefault dependentAppCount dependentApps { appSlug } } }"
    )

    rows = _execute(org, _org_admin(), query, {})["astroliftManagedDomains"]

    assert sorted(rows, key=lambda row: row["zone"]) == [
        {
            "zone": platform.zone,
            "isEffectiveDefault": True,
            "dependentAppCount": 1,
            "dependentApps": [{"appSlug": env.registered_app.slug}],
        },
        {
            "zone": "spare-2572.example",
            "isEffectiveDefault": False,
            "dependentAppCount": 0,
            "dependentApps": [],
        },
    ]
