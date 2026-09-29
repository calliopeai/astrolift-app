"""Who may enter an app behind central auth (#2132)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.parser import ManifestError
from astrolift_manifest.parser import parse_raw as parse_manifest_text
from astrolift_registry.models import RegisteredApp
from core import edge_access
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

ROUTE = {
    "kind": "HTTPRoute",
    "metadata": {"name": "r", "labels": {"astrolift.dev/namespace": "veruus-prod"}},
    "spec": {"hostnames": ["veruus-demo.apps.example.net"]},
}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def reapplied(monkeypatch):
    calls = []
    monkeypatch.setattr(edge_access, "reapply_edge", lambda cluster: calls.append(cluster.slug) or True)
    return calls


def _world(slug="veruus-demo", ingress_class="envoy"):
    org = Organization.objects.create(name="V", slug=f"v-{uuid.uuid4().hex[:8]}")
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"p-{uuid.uuid4().hex[:6]}")
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name=slug, slug=slug, provisioning_status="ready"
    )
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:8]}",
        name="c",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class=ingress_class,
    )
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod", url="https://x")
    return org, app, cluster


# ---- the manifest ------------------------------------------------------------

MANIFEST = """
name = "veruus-demo"

[[workloads]]
name = "web"
kind = "deployment"
  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/acme/web:main"

[ingress.access]
groups = ["veruus"]
users = ["Guest@Example.com"]
"""


def test_the_manifest_declares_access():
    raw = parse_manifest_text(MANIFEST)

    assert raw.ingress_access == {"groups": ["veruus"], "users": ["guest@example.com"]}


@pytest.mark.parametrize(
    "block",
    [
        '[ingress.access]\nteams = ["x"]',
        '[ingress.access]\nusers = ["not-an-email"]',
        '[ingress.access]\ngroups = "veruus"',
    ],
)
def test_a_malformed_access_table_is_refused(block):
    with pytest.raises(ManifestError):
        parse_manifest_text(
            'name = "a"\n[[workloads]]\nname = "web"\nkind = "deployment"\n  [[workloads.containers]]\n  name = "app"\n  is_primary = true\n  image_ref = "docker.io/acme/web:main"\n'
            + block
        )


def test_a_manifest_without_it_parses_to_none():
    assert (
        parse_manifest_text(
            'name = "a"\n[[workloads]]\nname = "web"\nkind = "deployment"\n  [[workloads.containers]]\n  name = "app"\n  is_primary = true\n  image_ref = "docker.io/acme/web:main"\n'
        ).ingress_access
        is None
    )


# ---- recording and applying ----------------------------------------------------


def test_a_deploy_records_the_hosts_of_a_restricted_app(reapplied):
    _org, app, cluster = _world()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "ui"}
    app.save()

    assert edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE]) is True

    cluster.refresh_from_db()
    entry = cluster.edge_access_rules[f"{app.guid}/veruus-prod"]
    assert entry["hosts"] == ["veruus-demo.apps.example.net"]
    assert entry["groups"] == ["veruus"]
    assert reapplied == [cluster.slug]


def test_redeploying_an_unchanged_app_does_not_reapply(reapplied):
    _org, app, cluster = _world()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "ui"}
    app.save()
    edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE])
    reapplied.clear()

    assert edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE]) is False
    assert reapplied == []


def test_an_open_app_leaves_no_rule(reapplied):
    _org, app, cluster = _world()

    edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE])

    cluster.refresh_from_db()
    assert cluster.edge_access_rules == {}
    assert reapplied == []


def test_a_teardown_removes_the_rule(reapplied):
    _org, app, cluster = _world()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "ui"}
    app.save()
    edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE])

    edge_access.record_environment(cluster, app, "veruus-prod", [])

    cluster.refresh_from_db()
    assert cluster.edge_access_rules == {}


def test_changing_access_updates_only_this_apps_rules(reapplied):
    _org, app, cluster = _world()
    _other_org, other, _other_cluster = _world()
    # Same slug, another org, sharing the cluster's rule table.
    other_key = f"{other.guid}/veruus-prod"
    cluster.edge_access_rules = {
        other_key: {"name": "x", "hosts": ["o.example.net"], "groups": ["o"], "users": []}
    }
    cluster.save()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "ui"}
    app.save()
    edge_access.record_environment(cluster, app, "veruus-prod", [ROUTE])

    edge_access.set_app_access(app, groups=["veruus", "staff"], users=[], source="ui")

    cluster.refresh_from_db()
    assert cluster.edge_access_rules[f"{app.guid}/veruus-prod"]["groups"] == ["staff", "veruus"]
    assert cluster.edge_access_rules[other_key]["groups"] == ["o"]


# ---- manifest wins, removal clears, UI untouched ------------------------------


def _persist(app, text):
    from astrolift_manifest.normalize import normalize
    from astrolift_manifest.persist import persist_manifest

    persist_manifest(app, normalize(parse_manifest_text(text)))
    app.refresh_from_db()


def test_the_manifest_sets_access_and_removing_it_clears_it(reapplied):
    _org, app, _cluster = _world()

    _persist(app, MANIFEST)
    assert app.edge_access == {"groups": ["veruus"], "users": ["guest@example.com"], "source": "manifest"}

    _persist(
        app,
        'name = "veruus-demo"\n[[workloads]]\nname = "web"\nkind = "deployment"\n  [[workloads.containers]]\n  name = "app"\n  is_primary = true\n  image_ref = "docker.io/acme/web:main"\n',
    )
    assert app.edge_access == {}


def test_a_manifest_without_the_table_leaves_a_ui_rule_alone(reapplied):
    _org, app, _cluster = _world()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "ui"}
    app.save()

    _persist(
        app,
        'name = "veruus-demo"\n[[workloads]]\nname = "web"\nkind = "deployment"\n  [[workloads.containers]]\n  name = "app"\n  is_primary = true\n  image_ref = "docker.io/acme/web:main"\n',
    )

    assert app.edge_access["groups"] == ["veruus"]


# ---- GraphQL -----------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))


def test_the_mutation_sets_access(permission_resolver, reapplied):
    from astrolift_registry.schema.app_access import AppAccessMutation, SetAppAccessInput

    permission_resolver.grant(Permission.APP_ACCESS)
    org, app, cluster = _world()

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = AppAccessMutation().set_app_access(
            _info(), SetAppAccessInput(app_slug=app.slug, groups=["veruus"], users=[])
        )

    assert result.ok is True, result.errors
    assert result.data.restricted is True
    assert result.data.enforced_on == [cluster.slug]


def test_the_mutation_refuses_an_app_the_manifest_manages(permission_resolver, reapplied):
    from astrolift_registry.schema.app_access import AppAccessMutation, SetAppAccessInput

    permission_resolver.grant(Permission.APP_ACCESS)
    org, app, _cluster = _world()
    app.edge_access = {"groups": ["veruus"], "users": [], "source": "manifest"}
    app.save()

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = AppAccessMutation().set_app_access(_info(), SetAppAccessInput(app_slug=app.slug, groups=[]))

    assert result.ok is False
    assert "astrolift.toml" in result.errors[0].message


def test_another_orgs_app_is_not_found(permission_resolver, reapplied):
    from astrolift_registry.schema.app_access import AppAccessMutation, SetAppAccessInput

    permission_resolver.grant(Permission.APP_ACCESS)
    _org, app, _cluster = _world()
    other = Organization.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")

    with tenant_context(TenantContext(organization_id=other.pk)):
        result = AppAccessMutation().set_app_access(
            _info(), SetAppAccessInput(app_slug=app.slug, groups=["x"])
        )

    assert result.ok is False
    app.refresh_from_db()
    assert app.edge_access == {}


def test_the_preview_names_who_would_lose_access(monkeypatch):
    from _sdk.identity_users import IdentityUser

    _org, app, cluster = _world()

    class Driver:
        def list_users(self, *, search="", limit=60):
            return [
                IdentityUser(
                    username="a", email="a@example.com", enabled=True, status="", groups=("veruus",)
                ),
                IdentityUser(username="b", email="b@example.com", enabled=True, status="", groups=()),
            ]

    monkeypatch.setattr(
        "astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (Driver(), "")
    )

    preview = edge_access.access_preview(app, cluster, groups=["veruus"], users=[])

    assert preview == {"allowed": 1, "total": 2, "losing": ["b@example.com"]}
