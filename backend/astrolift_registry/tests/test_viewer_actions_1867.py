"""Real grants, current environments, bearer ceilings and mutation denials agree."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Policy, RoleBinding
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_identity.tests.test_stock_roles_1864 import _stock_roles
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RestartWorkloadInput, ScaleWorkloadInput
from astrolift_registry.models import AppTeamAccess, Workload
from astrolift_registry.viewer_actions import workload_viewer_permissions
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))


@pytest.fixture
def world():
    world = ScopeWorld("viewer-1867")
    world.user = make_user("viewer-1867")
    world.cluster = make_cluster(world, "viewer-1867")
    world.rows = {}
    for app in (world.medops_app, world.platform_app):
        environment = AppEnvironment.objects.create(
            registered_app=app, tenant_cluster=world.cluster, name="staging"
        )
        workload = Workload.objects.create(registered_app=app, slug="web", name="Web", kind="deployment")
        world.rows[app.pk] = SimpleNamespace(environment=environment, workload=workload)
    return world


def own(world):
    return world.rows[world.medops_app.pk].workload


def sibling(world):
    return world.rows[world.platform_app.pk].workload


def grant(world, kind="APP", permissions=(Permission.APP_READ, Permission.APP_DEPLOY)):
    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    return bind_role(
        world.user, permissions=permissions, kind=kind, scope_id=scope_id, slug=f"viewer-grant-{kind}"
    )


@contextmanager
def subject(world, token=None):
    state = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
            yield
    finally:
        reset_current_api_token(state)


@pytest.fixture
def effects(monkeypatch):
    calls = []

    class Driver:
        def patch_workload(self, *args):
            calls.append(args)
            return {"spec": {"replicas": 2}, "status": {"observedGeneration": 3, "readyReplicas": 2}}

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: Driver())
    return calls


def invoke(world, workload, action):
    mutation = LifecycleMutation()
    info = make_info(world.user)
    if action == "restart":
        return mutation.restart_astrolift_workload(info, RestartWorkloadInput(workload_id=str(workload.guid)))
    return mutation.scale_astrolift_workload(
        info, ScaleWorkloadInput(workload_id=str(workload.guid), replicas=2)
    )


@pytest.mark.parametrize("kind", ["ORG", "TEAM", "PROJECT", "APP"])
def test_scoped_decisions_and_actual_mutations_agree_without_sibling_authority(world, effects, kind):
    grant(world, kind)
    with subject(world):
        decisions = workload_viewer_permissions([own(world), sibling(world)])
        assert decisions[own(world).pk].allowed
        assert decisions[sibling(world).pk].allowed == (kind == "ORG")
        for action in ("restart", "scale"):
            result = invoke(world, sibling(world), action)
            assert result.ok == decisions[sibling(world).pk].allowed
            if not result.ok:
                assert result.errors[0].code == decisions[sibling(world).pk].code
                assert result.errors[0].message == decisions[sibling(world).pk].reason
    assert len(effects) == (2 if kind == "ORG" else 0)


@pytest.mark.parametrize("role_slug", [slug for slug, *_ in SYSTEM_ROLES])
def test_every_stock_role_decision_matches_restart_and_scale_gate(world, effects, role_slug):
    role = _stock_roles()[role_slug]
    kind = role.scope_level
    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    RoleBinding.objects.create(user=world.user, role=role, scope_kind=kind, scope_id=scope_id)
    with subject(world):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        for action in ("restart", "scale"):
            result = invoke(world, own(world), action)
            assert result.ok == decision.allowed, (role_slug, decision, result.errors)
            if not result.ok:
                assert result.errors[0].message == decision.reason
    assert len(effects) == (2 if decision.allowed else 0)


@pytest.mark.parametrize("stale", ["expired", "revoked", "deleted_role", "non_inheriting"])
def test_current_stale_grants_deny_both_preview_and_mutation(world, effects, stale):
    binding = grant(world, "TEAM")
    if stale == "expired":
        binding.expires_at = timezone.now() - timedelta(seconds=1)
    elif stale == "revoked":
        binding.deleted_at = timezone.now()
    elif stale == "deleted_role":
        binding.role.deleted_at = timezone.now()
        binding.role.save()
    else:
        binding.inherits = False
    binding.save()
    with subject(world):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert not decision.allowed and not result.ok
    assert result.errors[0].message == decision.reason
    assert effects == []


@pytest.mark.parametrize(
    "scopes,team,expected",
    [("read:apps", None, False), ("write:apps", "own", True), ("write:apps", "sibling", False)],
)
def test_bearer_scope_and_team_ceilings_match_the_actual_gate(world, effects, scopes, team, expected):
    grant(world, "ORG")
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        token_hash="viewer-token",
        scopes=[scopes],
        name="Viewer",
        team={"own": world.medops, "sibling": world.platform}.get(team),
    )
    with subject(world, token):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert decision.allowed == expected and result.ok == expected
    if not expected:
        assert result.errors[0].message == decision.reason
        assert effects == []


@pytest.mark.parametrize("level,expected", [("viewer", False), ("deployer", True), ("owner", True)])
def test_team_share_and_bearer_share_ceiling_are_identical(world, effects, level, expected):
    grant(world, "TEAM")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        token_hash="share-token",
        scopes=["write:apps"],
        name="Share",
        team=world.medops,
    )
    with subject(world, token):
        decision = workload_viewer_permissions([sibling(world)])[sibling(world).pk]
        result = invoke(world, sibling(world), "restart")
    assert decision.allowed == expected and result.ok == expected
    if not expected:
        assert result.errors[0].message == decision.reason
        assert effects == []


def test_current_primary_environment_policy_ignores_permissive_sibling_environment(world, effects):
    grant(world, "ORG")
    primary = world.rows[world.medops_app.pk].environment
    primary.name = "production"
    primary.save()
    AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="staging"
    )
    Policy.objects.create(
        organization=world.org,
        slug="deny-prod",
        name="Production deny",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"env": ["production"]},
    )
    with subject(world):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert not decision.allowed and not result.ok
    assert result.errors[0].message == decision.reason
    assert effects == []


def test_unknown_or_incoherent_first_environment_never_advertises_authority(world):
    grant(world, "ORG")
    primary = world.rows[world.medops_app.pk].environment
    primary.tenant_cluster.organization_id = None
    primary.tenant_cluster.is_active = False
    primary.tenant_cluster.save()
    with subject(world):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
    assert not decision.allowed and decision.code == "PRECONDITION"


@pytest.mark.parametrize(
    "invalid", ["missing_environment", "foreign_cluster", "deleted_app", "foreign_project", "mismatched_home"]
)
def test_missing_or_incoherent_current_target_is_not_advertised(world, effects, invalid):
    grant(world, "ORG")
    primary = world.rows[world.medops_app.pk].environment
    if invalid == "missing_environment":
        primary.deleted_at = timezone.now()
        primary.save()
    elif invalid == "foreign_cluster":
        from astrolift_identity.models import Organization

        primary.tenant_cluster.organization = Organization.objects.create(name="Other", slug="viewer-other")
        primary.tenant_cluster.save()
    elif invalid == "deleted_app":
        world.medops_app.deleted_at = timezone.now()
        world.medops_app.save()
    elif invalid == "foreign_project":
        from astrolift_identity.models import Organization

        foreign = Organization.objects.create(name="Other", slug="viewer-other")
        type(world.medops_project).objects.filter(pk=world.medops_project.pk).update(
            organization_id=foreign.pk
        )
    else:
        world.medops_app.team = world.platform
        world.medops_app.save()
    with subject(world):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert not decision.allowed and not result.ok
    assert effects == []


def test_region_policy_uses_current_primary_cluster_instead_of_selected_request_region(world, effects):
    from astrolift_identity.abac import RequestAttributes, request_attributes

    grant(world, "ORG")
    world.cluster.region = "eu-west-1"
    world.cluster.save()
    Policy.objects.create(
        organization=world.org,
        name="Region deny",
        slug="deny-region",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"region": ["eu-west-1"]},
    )
    with subject(world), request_attributes(RequestAttributes(region="us-west-2")):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert not decision.allowed and not result.ok
    assert result.errors[0].message == decision.reason
    assert effects == []


@pytest.mark.parametrize("resource", ["app_slug", "project_slug"])
def test_resource_policy_denies_only_the_actual_app_or_project(world, effects, resource):
    grant(world, "ORG")
    slug = world.medops_app.slug if resource == "app_slug" else world.medops_project.slug
    Policy.objects.create(
        organization=world.org,
        name="Resource deny",
        slug="deny-resource",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={resource: [slug]},
    )
    with subject(world):
        decisions = workload_viewer_permissions([own(world), sibling(world)])
        result = invoke(world, own(world), "restart")
    assert not decisions[own(world).pk].allowed and not result.ok
    assert decisions[sibling(world).pk].allowed
    assert result.errors[0].message == decisions[own(world).pk].reason
    assert effects == []


@pytest.mark.parametrize("scopes,expected", [(["read:apps"], False), (["read:apps", "write:apps"], True)])
def test_real_http_workload_page_exposes_current_bearer_decisions(world, client, settings, scopes, expected):
    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import Member

    grant(world, "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    issued = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="HTTP viewer",
        token_hash=issued.token_hash,
        scopes=scopes,
    )
    query = "{ astroliftWorkloadsPage { items { slug viewerCan { restart { allowed code reason } scale { allowed code reason } } } } }"
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_AUTHORIZATION": f"Bearer {issued.plaintext}",
    }
    response = client.post(
        f"/{settings.BASE_URL}gql/config/", data={"query": query}, content_type="application/json", **headers
    )
    assert response.status_code == 200
    body = response.json()
    assert not body.get("errors"), body
    rows = body["data"]["astroliftWorkloadsPage"]["items"]
    assert len(rows) == 2
    for row in rows:
        assert row["viewerCan"]["restart"]["allowed"] == expected
        assert row["viewerCan"]["scale"] == row["viewerCan"]["restart"]
        assert row["viewerCan"]["restart"]["code"] == ("" if expected else "PERMISSION_DENIED")
    token.is_revoked = True
    token.save()
    response = client.post(
        f"/{settings.BASE_URL}gql/config/", data={"query": query}, content_type="application/json", **headers
    )
    assert response.status_code in {401, 403} or response.json().get("errors")


@pytest.mark.parametrize("team_token", [False, True])
def test_one_and_many_apps_have_equal_bounded_decision_query_counts(world, team_token):
    from astrolift_identity.models import Project
    from astrolift_registry.models import RegisteredApp

    grant(world, "ORG")
    token = (
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops,
            token_hash="batch-token",
            name="Batch",
            scopes=["write:apps"],
        )
        if team_token
        else None
    )
    Policy.objects.create(
        organization=world.org,
        name="Unmatched project policy",
        slug="viewer-project-policy",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"project_slug": ["another-project"]},
    )
    many = [own(world)]
    for number in range(20):
        project = Project.objects.create(
            organization=world.org,
            team=world.medops,
            name=f"Project {number}",
            slug=f"viewer-project-{number}",
        )
        app = RegisteredApp.objects.create(
            organization=world.org,
            team=world.medops,
            project=project,
            name=f"App {number}",
            slug=f"viewer-{number}",
        )
        AppEnvironment.objects.create(registered_app=app, tenant_cluster=world.cluster, name="staging")
        many.append(Workload.objects.create(registered_app=app, name="Web", slug="web"))
    counts = []
    for rows in ([own(world)], many):
        with subject(world, token), CaptureQueriesContext(connection) as capture:
            decisions = workload_viewer_permissions(rows)
        assert all(decision.allowed for decision in decisions.values())
        counts.append(len(capture))
    assert counts[0] == counts[1]
    assert counts[1] <= 12


def test_allowed_read_does_not_authorize_after_grant_revocation(world, effects):
    binding = grant(world)
    with subject(world):
        assert workload_viewer_permissions([own(world)])[own(world).pk].allowed
    binding.deleted_at = timezone.now()
    binding.save()
    with subject(world):
        current = workload_viewer_permissions([own(world)])[own(world).pk]
        result = invoke(world, own(world), "restart")
    assert not current.allowed and not result.ok
    assert result.errors[0].message == current.reason
    assert effects == []


def test_no_actor_returns_denials_without_reading_target_metadata(world):
    with (
        tenant_context(TenantContext(organization_id=world.org.pk)),
        CaptureQueriesContext(connection) as capture,
    ):
        decision = workload_viewer_permissions([own(world)])[own(world).pk]
    assert not decision.allowed and decision.reason == "no actor"
    assert len(capture) == 0
