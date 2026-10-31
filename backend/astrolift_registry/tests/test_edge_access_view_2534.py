"""The organization's app access, and whether the edge holds each rule (#2534)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.app_access import (
    AppAccessQuery,
    EdgeAccessFilterInput,
    EdgeAccessStatus,
)
from core import edge_access
from core.app_deploy import namespace_for_environment
from core.permissions import Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


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


def _org():
    org = Organization.objects.create(name="V", slug=f"v-{uuid.uuid4().hex[:8]}")
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"p-{uuid.uuid4().hex[:6]}")
    return org, team, project


def _cluster(org, ingress_class="envoy"):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    return TenantCluster.objects.create(
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


def _app(org, team, project, cluster, slug, *, env="prod"):
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name=slug, slug=slug, provisioning_status="ready"
    )
    environment = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name=env)
    return app, environment


def _deploy(cluster, app, environment, host):
    """What a deploy records: the environment's hosts with the app's grant."""
    namespace = namespace_for_environment(environment)
    route = {
        "kind": "HTTPRoute",
        "metadata": {"name": "r", "labels": {"astrolift.dev/namespace": namespace}},
        "spec": {"hostnames": [host]},
    }
    cluster.refresh_from_db()
    edge_access.record_environment(cluster, app, namespace, [route])
    cluster.refresh_from_db()


def _applied(cluster):
    """An edge apply that read the current rules succeeded."""
    edge_access.mark_edge_applied(cluster.pk, timezone.now())
    cluster.refresh_from_db()


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _rows(org, **kwargs):
    with tenant_context(TenantContext(organization_id=org.pk)):
        return AppAccessQuery().astrolift_edge_access(_info(), **kwargs)


# ---- the status --------------------------------------------------------------


def test_restricting_reads_applying_until_the_edge_applies_then_enforced(reapplied):
    """The acceptance: a restriction is not reported enforced before the edge
    has been applied with it."""
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")
    _deploy(cluster, app, env, "web.apps.example.net")
    _applied(cluster)

    edge_access.set_app_access(app, groups=["staff"], users=[], source="ui")
    cluster.refresh_from_db()
    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env)) == (
        edge_access.APPLYING,
        ["web.apps.example.net"],
    )

    _applied(cluster)
    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.ENFORCED
    )


def test_clearing_reads_applying_until_the_edge_drops_the_rule(reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")
    edge_access.set_app_access(app, groups=["staff"], users=[], source="ui")
    _deploy(cluster, app, env, "web.apps.example.net")
    _applied(cluster)

    edge_access.set_app_access(app, groups=[], users=[], source="ui")
    cluster.refresh_from_db()

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.APPLYING
    )
    _applied(cluster)
    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.OPEN
    )


def test_a_rule_on_an_environment_never_deployed_waits_for_its_deploy(reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")

    edge_access.set_app_access(app, groups=[], users=["a@example.com"], source="ui")
    cluster.refresh_from_db()

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env)) == (
        edge_access.PENDING_DEPLOY,
        [],
    )


def test_an_open_app_with_nothing_recorded_is_open(reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.OPEN
    )


def test_a_recorded_rule_unlike_the_grant_is_drift(reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")
    edge_access.set_app_access(app, groups=["staff"], users=[], source="ui")
    _deploy(cluster, app, env, "web.apps.example.net")
    RegisteredApp.objects.filter(pk=app.pk).update(
        edge_access={"groups": ["other"], "users": [], "source": "ui"}
    )
    app.refresh_from_db()

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.DRIFT
    )


def test_a_rule_recorded_before_applies_were_recorded_is_unverified(reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    app, env = _app(org, team, project, cluster, "web")
    edge_access.set_app_access(app, groups=["staff"], users=[], source="ui")
    _deploy(cluster, app, env, "web.apps.example.net")
    TenantCluster.objects.filter(pk=cluster.pk).update(
        edge_access_changed_at=None, edge_access_applied_at=None
    )
    cluster.refresh_from_db()

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env))[0] == (
        edge_access.UNVERIFIED
    )


def test_a_cluster_without_the_envoy_edge_holds_no_rule(reapplied):
    org, team, project = _org()
    cluster = _cluster(org, ingress_class="nginx")
    app, env = _app(org, team, project, cluster, "web")

    assert edge_access.environment_edge_state(app, cluster, namespace_for_environment(env)) == (
        edge_access.NOT_ON_EDGE,
        [],
    )


def test_the_applied_mark_never_moves_back(reapplied):
    org, _team, _project = _org()
    cluster = _cluster(org)
    later = timezone.now()
    edge_access.mark_edge_applied(cluster.pk, later)

    edge_access.mark_edge_applied(cluster.pk, later - dt.timedelta(minutes=5))

    cluster.refresh_from_db()
    assert cluster.edge_access_applied_at == later


def test_the_minimal_rbac_path_marks_the_rules_applied(monkeypatch, reapplied):
    """A cluster held to the minimal contract writes the policies itself."""
    from core import cluster_management

    org, _team, _project = _org()
    cluster = _cluster(org)
    cluster.oidc_auth_config = {"auth_proxy_host": "auth.apps.example.net", "issuer": "https://idp.example"}
    cluster.save()
    monkeypatch.setattr(
        "providers.k8s_native.edge_gateway.edge_access_manifests",
        lambda config, rules: [{"kind": "SecurityPolicy"}],
    )

    class Driver:
        def apply_manifests(self, slug, namespace, manifests):
            return SimpleNamespace(ok=True, errors=[])

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: Driver(), raising=False)

    assert edge_access._apply_access_policies(cluster) is True
    cluster.refresh_from_db()
    assert cluster.edge_access_applied_at is not None


def test_a_failed_policy_write_marks_nothing(monkeypatch, reapplied):
    from core import cluster_management

    org, _team, _project = _org()
    cluster = _cluster(org)
    monkeypatch.setattr(
        "providers.k8s_native.edge_gateway.edge_access_manifests",
        lambda config, rules: [{"kind": "SecurityPolicy"}],
    )

    class Driver:
        def apply_manifests(self, slug, namespace, manifests):
            return SimpleNamespace(ok=False, errors=["forbidden"])

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: Driver(), raising=False)

    assert edge_access._apply_access_policies(cluster) is False
    cluster.refresh_from_db()
    assert cluster.edge_access_applied_at is None


# ---- the org-wide query --------------------------------------------------------


def test_the_list_shows_each_environment_with_its_rule_and_status(permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    cluster = _cluster(org)
    web, web_env = _app(org, team, project, cluster, "web")
    docs, _docs_env = _app(org, team, project, cluster, "docs")
    _deploy(cluster, web, web_env, "web.apps.example.net")
    edge_access.set_app_access(web, groups=["staff"], users=["a@example.com"], source="ui")
    _applied(cluster)

    page = _rows(org)

    assert page.total_count == 2
    by_app = {r.app_slug: r for r in page.items}
    assert [r.app_slug for r in page.items] == ["docs", "web"]
    row = by_app["web"]
    assert row.status == EdgeAccessStatus.ENFORCED
    assert row.hosts == ["web.apps.example.net"]
    assert (row.groups, row.users, row.restricted, row.source) == (["staff"], ["a@example.com"], True, "ui")
    assert row.cluster_slug == cluster.slug and row.environment == "prod"
    assert row.team_slug == team.slug and row.project_slug == project.slug
    assert by_app["docs"].status == EdgeAccessStatus.OPEN
    assert by_app["docs"].restricted is False and by_app["docs"].source == ""
    assert docs.slug == "docs"


def test_who_set_the_rule_comes_from_the_audit_log(permission_resolver, reapplied, django_user_model):
    from core.schema.audit import MutationAuditLog

    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    cluster = _cluster(org)
    app, _env = _app(org, team, project, cluster, "web")
    edge_access.set_app_access(app, groups=["staff"], users=[], source="ui")
    user = django_user_model.objects.create(username="leo", email="leo@example.com")
    other_org, _t, _p = _org()
    for org_id, actor in ((org.pk, user), (other_org.pk, None)):
        MutationAuditLog.objects.create(
            user=actor,
            organization_id=org_id,
            operation="app.access.set",
            variables={"input": {"appSlug": "web", "groups": ["staff"], "users": []}},
            success=True,
        )

    row = _rows(org).items[0]

    assert row.set_by == "leo@example.com"
    assert row.set_at is not None


def test_another_orgs_apps_never_appear(permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    cluster = _cluster(org)
    _app(org, team, project, cluster, "web")
    other, other_team, other_project = _org()
    # Same slug, another org, on the same shared cluster.
    _app(other, other_team, other_project, cluster, "web")

    page = _rows(org)

    assert [r.app_slug for r in page.items] == ["web"]
    assert page.total_count == 1
    assert _rows(other).total_count == 1


def test_a_team_scoped_reader_sees_only_its_teams_apps(permission_resolver, reapplied):
    org, team, project = _org()
    cluster = _cluster(org)
    _app(org, team, project, cluster, "mine")
    other_team = Team.objects.create(organization=org, name="o", slug=f"o-{uuid.uuid4().hex[:6]}")
    other_project = Project.objects.create(
        organization=org, team=other_team, name="op", slug=f"op-{uuid.uuid4().hex[:6]}"
    )
    _app(org, other_team, other_project, cluster, "theirs")
    permission_resolver.grant(Permission.APP_READ, scope=PermissionScope(kind=ScopeKind.TEAM, id=team.pk))

    page = _rows(org)

    assert [r.app_slug for r in page.items] == ["mine"]


def test_no_read_grant_is_refused(permission_resolver, reapplied):
    from core.permissions import PermissionDenied

    org, team, project = _org()
    _app(org, team, project, _cluster(org), "web")

    with pytest.raises(PermissionDenied):
        _rows(org)


def test_filters_and_search(permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    envoy = _cluster(org)
    nginx = _cluster(org, ingress_class="nginx")
    web, web_env = _app(org, team, project, envoy, "web")
    _app(org, team, project, nginx, "legacy")
    other_team = Team.objects.create(organization=org, name="o", slug=f"o-{uuid.uuid4().hex[:6]}")
    other_project = Project.objects.create(
        organization=org, team=other_team, name="op", slug=f"op-{uuid.uuid4().hex[:6]}"
    )
    _app(org, other_team, other_project, envoy, "billing")
    _deploy(envoy, web, web_env, "web.apps.example.net")
    edge_access.set_app_access(web, groups=["finance"], users=[], source="ui")

    def slugs(**kwargs):
        return [r.app_slug for r in _rows(org, **kwargs).items]

    assert slugs(filter=EdgeAccessFilterInput(cluster=nginx.slug)) == ["legacy"]
    assert slugs(filter=EdgeAccessFilterInput(team=other_team.slug)) == ["billing"]
    assert slugs(filter=EdgeAccessFilterInput(project=project.slug)) == ["legacy", "web"]
    assert slugs(filter=EdgeAccessFilterInput(status=[EdgeAccessStatus.NOT_ON_EDGE])) == ["legacy"]
    assert slugs(filter=EdgeAccessFilterInput(status=[EdgeAccessStatus.APPLYING])) == ["web"]
    # Search reaches the rule's groups and the hosts, not only the names.
    assert slugs(search="finance") == ["web"]
    assert slugs(search="web.apps") == ["web"]


def test_the_cursor_walks_every_row_once(permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    cluster = _cluster(org)
    for i in range(5):
        _app(org, team, project, cluster, f"app-{i}")

    seen, after = [], None
    while True:
        page = _rows(org, first=2, after=after)
        assert page.total_count == 5
        seen += [r.app_slug for r in page.items]
        after = page.next_cursor
        if after is None:
            break

    assert seen == [f"app-{i}" for i in range(5)]


# ---- per person ----------------------------------------------------------------


class _Pool:
    def __init__(self, *people):
        self.people = people
        self.searches = []

    def list_users(self, *, search="", limit=60):
        self.searches.append(search)
        return [p for p in self.people if search in p.email]


def _person(email, groups=(), enabled=True):
    from _sdk.identity_users import IdentityUser

    return IdentityUser(
        username=email, email=email, enabled=enabled, status="CONFIRMED", groups=tuple(groups)
    )


def _person_page(org, email, user=None, **kwargs):
    with tenant_context(TenantContext(organization_id=org.pk)):
        return AppAccessQuery().astrolift_edge_access_for_person(_info(user), email=email, **kwargs)


def _three_apps(org, team, project, cluster):
    """open, staff-only and one listing a@example.com, all deployed and applied."""
    out = {}
    for slug, groups, users in (
        ("open", [], []),
        ("staff", ["staff"], []),
        ("listed", [], ["a@example.com"]),
    ):
        app, env = _app(org, team, project, cluster, slug)
        _deploy(cluster, app, env, f"{slug}.apps.example.net")
        edge_access.set_app_access(app, groups=groups, users=users, source="ui")
        out[slug] = app
    _applied(cluster)
    return out


def test_a_person_enters_through_a_group_a_listing_or_an_open_app(
    permission_resolver, reapplied, monkeypatch
):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    pool = _Pool(_person("a@example.com", groups=["staff"]), _person("b@example.com"))
    monkeypatch.setattr("astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (pool, ""))

    page = _person_page(org, "A@Example.com")

    via = {r.rule.app_slug: (r.enters, r.via, r.via_groups) for r in page.items}
    assert via == {
        "listed": (True, "user", []),
        "open": (True, "open", []),
        "staff": (True, "group", ["staff"]),
    }
    assert page.refused_count == 0
    assert [(login.resolved, login.has_login, login.groups) for login in page.logins] == [
        (True, True, ["staff"])
    ]
    # One read of the provider per cluster, not per app.
    assert pool.searches == ["a@example.com"]

    other = _person_page(org, "b@example.com")
    assert [r.rule.app_slug for r in other.items] == ["open"]
    assert other.refused_count == 2


def test_no_login_in_the_pool_enters_nothing(permission_resolver, reapplied, monkeypatch):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    monkeypatch.setattr("astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (_Pool(), ""))

    page = _person_page(org, "a@example.com")

    assert page.items == []
    assert page.refused_count == 3
    assert page.logins[0].has_login is False


def test_an_unreadable_provider_says_so_and_group_rules_stay_unknown(
    permission_resolver, reapplied, monkeypatch
):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    monkeypatch.setattr(
        "astrolift_clusters.schema.auth_users.identity_users_driver",
        lambda c: (None, "this cluster's central auth is an external IdP"),
    )

    page = _person_page(org, "a@example.com")

    via = {r.rule.app_slug: (r.enters, r.via) for r in page.items}
    assert via == {"listed": (None, "user"), "open": (None, "open"), "staff": (None, "unknown")}
    assert page.logins[0].resolved is False
    assert "external IdP" in page.logins[0].reason


def test_without_cluster_users_the_provider_is_not_read(permission_resolver, reapplied, monkeypatch):
    permission_resolver.grant(Permission.APP_READ)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    monkeypatch.setattr(
        "astrolift_clusters.schema.auth_users.identity_users_driver",
        lambda c: pytest.fail("read the provider without cluster.users"),
    )

    page = _person_page(org, "a@example.com")

    assert page.logins[0].resolved is False
    assert "cluster.users" in page.logins[0].reason
    assert {r.rule.app_slug: r.via for r in page.items}["staff"] == "unknown"


def test_a_disabled_login_enters_nothing(permission_resolver, reapplied, monkeypatch):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    pool = _Pool(_person("a@example.com", groups=["staff"], enabled=False))
    monkeypatch.setattr("astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (pool, ""))

    page = _person_page(org, "a@example.com")

    assert page.items == [] and page.refused_count == 3


def test_a_person_query_never_reaches_another_orgs_apps(permission_resolver, reapplied, monkeypatch):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    other, other_team, other_project = _org()
    app, env = _app(other, other_team, other_project, cluster, "theirs")
    _deploy(cluster, app, env, "theirs.apps.example.net")
    edge_access.set_app_access(app, groups=[], users=["a@example.com"], source="ui")
    pool = _Pool(_person("a@example.com"))
    monkeypatch.setattr("astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (pool, ""))

    page = _person_page(org, "a@example.com")

    assert page.items == [] and page.total_count == 0 and page.refused_count == 0
    assert page.logins == []
    assert pool.searches == []


def test_me_is_the_callers_own_email(permission_resolver, reapplied, monkeypatch):
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.CLUSTER_USERS)
    org, team, project = _org()
    cluster = _cluster(org)
    _three_apps(org, team, project, cluster)
    pool = _Pool(_person("a@example.com"))
    monkeypatch.setattr("astrolift_clusters.schema.auth_users.identity_users_driver", lambda c: (pool, ""))
    caller = SimpleNamespace(is_authenticated=True, is_superuser=False, email="A@example.com")

    page = _person_page(org, "me", user=caller)

    assert page.email == "a@example.com"
    assert sorted(r.rule.app_slug for r in page.items) == ["listed", "open"]
