# ruff: noqa: F811
"""Archived run-log reads retain actual owner policies and current credentials."""

import json
from types import SimpleNamespace

import pytest
from django.test import Client

from astrolift_identity import abac
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.historical_scopes import HistoricalAppScope
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.run_history import record
from astrolift_lifecycle.run_log_api import historical_log_scope
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.scopes import deployment_app_scope
from astrolift_lifecycle.tests.test_deployment_run_history_2176 import download, grant, info, page
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from astrolift_registry.models import AppTeamAccess
from core.permissions import Permission, PermissionDenied, check_permission
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db
ARCHIVES = ("app", "environment", "inactive-cluster", "deleted-cluster")


def archive(w, condition):
    row = w.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "durable output")
    row.transition_to(row.Status.PENDING)
    env = w.rows["medops"]["environment"]
    if condition == "app":
        w.medops_app.soft_delete()
    elif condition == "environment":
        env.soft_delete()
    elif condition == "inactive-cluster":
        env.tenant_cluster.is_active = False
        env.tenant_cluster.save()
    else:
        env.tenant_cluster.soft_delete()
    return row


def read_all(w, row):
    assert "durable output" in [e.message for e in page(w, row).items]
    assert "durable output" in download(w, row).content
    assert [e.status for e in LifecycleQuery().astrolift_deployment_log(info(w), str(row.guid))] == [
        "pending"
    ]


@pytest.mark.parametrize("condition", ARCHIVES)
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_archived_history_keeps_actual_grant_chain_and_live_scope_is_unchanged(world, condition, kind):
    owner = {
        "APP": world.medops_app,
        "PROJECT": world.medops_project,
        "TEAM": world.medops,
        "ORG": world.org,
    }[kind]
    grant(world, kind, owner)
    row = archive(world, condition)
    with subject(world), abac.request_attributes(abac.RequestAttributes(actor_user_id=world.user.pk)):
        historical = historical_log_scope({"deployment_id": str(row.guid)})
        ordinary = deployment_app_scope("deployment_id", permission=Permission.APP_READ_LOGS)(
            {"deployment_id": str(row.guid)}
        )
        assert isinstance(historical, HistoricalAppScope)
        assert historical != ordinary
        read_all(world, row)
        if kind != "ORG":
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ_LOGS, scope=ordinary)
        with pytest.raises(PermissionDenied):
            check_permission(Permission.APP_DEPLOY, scope=historical)


@pytest.mark.parametrize("condition", ARCHIVES)
@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT"])
def test_archived_history_applies_actual_owner_deny_and_environment_facts(world, condition, kind):
    grant(world, "ORG", world.org)
    row = archive(world, condition)
    owner = {"APP": world.medops_app, "PROJECT": world.medops_project, "TEAM": world.medops}[kind]
    Policy.objects.create(
        organization=world.org,
        name="Archived deny",
        slug="archived-deny",
        effect="DENY",
        scope_level=kind,
        scope_id=owner.pk,
        action_pattern="app.read_logs",
        resource_pattern={"env": ["production"]},
    )
    with subject(world):
        for read in (
            page,
            download,
            lambda w, r: LifecycleQuery().astrolift_deployment_log(info(w), str(r.guid)),
        ):
            with pytest.raises(PermissionDenied):
                read(world, row)


@pytest.mark.parametrize("condition", ARCHIVES)
@pytest.mark.parametrize("scope", [["read:apps"], []])
def test_archived_history_keeps_bearer_permission_and_home_team_ceilings(world, condition, scope):
    grant(world, "ORG", world.org)
    row = archive(world, condition)
    token = SimpleNamespace(
        organization_id=world.org.pk, user_id=world.user.pk, team_id=world.medops.pk, scopes=scope
    )
    with subject(world, token):
        if scope:
            read_all(world, row)
        else:
            with pytest.raises(PermissionDenied):
                page(world, row)
    token.scopes = ["admin"]
    token.team_id = world.platform.pk
    with subject(world, token):
        with pytest.raises(PermissionDenied):
            page(world, row)
        with pytest.raises(PermissionDenied):
            download(world, row)


@pytest.mark.parametrize("level", ["viewer", "deployer", "owner"])
def test_current_shares_keep_their_permission_ceiling_on_archived_apps(world, level):
    grant(world, "TEAM", world.platform)
    row = archive(world, "app")
    share = AppTeamAccess.objects.create(
        registered_app=world.medops_app, team=world.platform, access_level=level
    )
    token = SimpleNamespace(
        organization_id=world.org.pk, user_id=world.user.pk, team_id=world.platform.pk, scopes=["admin"]
    )
    with subject(world, token):
        if level == "viewer":
            with pytest.raises(PermissionDenied):
                page(world, row)
        else:
            read_all(world, row)
            share.soft_delete()
            with pytest.raises(PermissionDenied):
                download(world, row)


@pytest.mark.parametrize("condition", ARCHIVES)
def test_http_archived_history_keeps_policies_bearers_and_revoked_continuations(world, condition):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    binding = grant(world, "ORG", world.org)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="History HTTP",
        token_hash=minted.token_hash,
        scopes=["read:apps"],
    )
    row = archive(world, condition)
    query = """query($id:String!,$cursor:String){astroliftDeploymentRunLogPage(deploymentId:$id,cursor:$cursor,limit:1){items{message}nextCursor}astroliftDeploymentRunLogDownload(deploymentId:$id){content}astroliftDeploymentLog(deploymentId:$id){status}}"""

    def send(cursor=None):
        response = Client().post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"id": str(row.guid), "cursor": cursor}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
        )
        assert response.status_code == 200, response.content
        return response.json()

    result = send()
    assert not result.get("errors"), result
    assert "durable output" in result["data"]["astroliftDeploymentRunLogDownload"]["content"]
    cursor = result["data"]["astroliftDeploymentRunLogPage"]["nextCursor"]
    assert cursor
    for kind, owner in [("APP", world.medops_app), ("TEAM", world.medops), ("PROJECT", world.medops_project)]:
        policy = Policy.objects.create(
            organization=world.org,
            name="HTTP archived deny",
            slug="http-archived-deny",
            effect="DENY",
            scope_level=kind,
            scope_id=owner.pk,
            action_pattern="app.read_logs",
            resource_pattern={"env": ["production"]},
        )
        assert send(cursor).get("errors")
        policy.soft_delete()
    token.team = world.platform
    token.save()
    assert send(cursor).get("errors")
    token.team = world.medops
    token.scopes = []
    token.save()
    assert send(cursor).get("errors")
    token.scopes = ["read:apps"]
    token.save()
    binding.soft_delete()
    assert send(cursor).get("errors")


@pytest.mark.parametrize("invalid", ["foreign-project", "incoherent-team", "foreign-cluster"])
def test_archived_owner_chain_never_accepts_foreign_or_incoherent_ancestry(world, invalid):
    grant(world, "ORG", world.org)
    row = archive(world, "app")
    foreign = ScopeWorld("archived-foreign")
    if invalid == "foreign-project":
        world.medops_app.project = foreign.medops_project
        world.medops_app.save()
    elif invalid == "incoherent-team":
        world.medops_app.team = world.platform
        world.medops_app.save()
    else:
        cluster = row.app_environment.tenant_cluster
        cluster.organization = foreign.org
        cluster.save()
    with subject(world):
        assert page(world, row).items == []
        assert download(world, row) is None


def test_registry_requires_actual_team_even_when_a_project_has_a_team(world):
    from django.db import IntegrityError, transaction

    from astrolift_registry.models import RegisteredApp

    with pytest.raises(IntegrityError), transaction.atomic():
        RegisteredApp.objects.filter(pk=world.medops_app.pk).update(team_id=None)
    world.medops_app.refresh_from_db()
    assert world.medops_app.team_id == world.medops.pk


def test_archived_team_bearer_cannot_read_after_its_home_team_is_deleted(world):
    grant(world, "ORG", world.org)
    row = archive(world, "app")
    token = SimpleNamespace(
        organization_id=world.org.pk, user_id=world.user.pk, team_id=world.medops.pk, scopes=["admin"]
    )
    with subject(world, token):
        read_all(world, row)
        world.medops.soft_delete()
        with pytest.raises(PermissionDenied):
            page(world, row)
        with pytest.raises(PermissionDenied):
            download(world, row)


@pytest.mark.parametrize("invalid", ["account", "membership", "organization"])
def test_http_archived_history_requires_current_active_account_and_membership(world, invalid):
    member = Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    grant(world)
    row = archive(world, "app")
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="Archived active auth",
        token_hash=minted.token_hash,
        scopes=["read:apps"],
    )
    query = """query($id:String!){astroliftDeploymentRunLogPage(deploymentId:$id){items{message}}astroliftDeploymentRunLogDownload(deploymentId:$id){content}}"""

    def send():
        return Client().post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"id": str(row.guid)}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
        )

    response = send()
    assert response.status_code == 200
    assert not response.json().get("errors"), response.content
    if invalid == "account":
        world.user.is_active = False
        world.user.save()
    elif invalid == "membership":
        member.is_active = False
        member.save()
    else:
        world.org.soft_delete()
    assert send().status_code == 401


def test_migrated_history_accepts_pre_upgrade_worker_insert_and_new_observations(world):
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    from astrolift_lifecycle.models import DeploymentLog

    state = MigrationLoader(connection).project_state(
        [("astrolift_lifecycle", "0044_deployment_approval_identities")]
    )
    OldLog = state.apps.get_model("astrolift_lifecycle", "DeploymentLog")
    assert "phase" not in {field.name for field in OldLog._meta.fields}
    assert "event" not in {field.name for field in OldLog._meta.fields}
    row = world.rows["medops"]["deployment"]
    old_entry = OldLog.objects.create(deployment_id=row.pk, status="deploying", message="old worker output")
    upgraded = DeploymentLog.objects.get(pk=old_entry.pk)
    assert upgraded.phase == upgraded.event == ""
    assert upgraded.status == "deploying"
    new_entry = record(row.pk, "apply", "started", "new worker observation")
    assert new_entry.phase == "apply" and new_entry.event == "started"
    assert upgraded.pk < new_entry.pk
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.attname, a.attnotnull, pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid='astrolift_lifecycle_deploymentlog'::regclass AND a.attname IN ('phase','event') ORDER BY a.attname"
        )
        defaults = cursor.fetchall()
    assert len(defaults) == 2
    assert all(notnull and default.startswith("''::") for _, notnull, default in defaults)
