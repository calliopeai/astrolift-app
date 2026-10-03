# ruff: noqa: F811
"""Public timeline identity is the admitted parent, with unchanged owner controls."""

import json
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment, Deployment, DeploymentLog
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import deployment_log_to_type
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster

pytestmark = pytest.mark.django_db


def http(world, *, permissions=(Permission.APP_READ, Permission.APP_READ_LOGS), org_grant=False, team=None):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        world.user,
        permissions=permissions,
        kind="ORG" if org_grant else "APP",
        scope_id=world.org.pk if org_grant else world.medops_app.pk,
        slug="timeline-guid-2265",
    )
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Timeline reader",
        token_hash=minted.token_hash,
        scopes=["admin"],
        team=team,
    )
    client = Client()

    def send(query, guid, **variables):
        response = client.post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"id": str(guid), **variables}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
            HTTP_X_ASTROLIFT_PROJECT=str(world.platform_project.pk),
        )
        assert response.status_code == 200
        return response.json()

    return send


LEGACY = "query($id:String!){astroliftDeploymentLog(deploymentId:$id){id deploymentId status message}}"
PAGE = "query($id:String!,$cursor:String){astroliftDeploymentRunLogPage(deploymentId:$id,cursor:$cursor,limit:2){items{id deploymentId status message} nextCursor hasMore}}"
DETAIL = "query($id:String!){astroliftDeployment(id:$id){id}}"


def test_real_http_legacy_and_every_page_reference_exact_detail_guid(world):
    row = world.rows["medops"]["deployment"]
    assert str(row.pk) != str(row.guid)
    own = [
        DeploymentLog.objects.create(deployment=row, status="deploying", message=f"own-{i}") for i in range(5)
    ]
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="staging", tenant_cluster=row.app_environment.tenant_cluster
    )
    sibling = Deployment.objects.create(registered_app=world.medops_app, app_environment=env)
    DeploymentLog.objects.create(deployment=sibling, status="failed", message="sibling-environment-marker")
    DeploymentLog.objects.create(
        deployment=world.rows["platform"]["deployment"], status="failed", message="sibling-app-marker"
    )
    foreign = ScopeWorld("timeline-foreign2265")
    foreign_env = AppEnvironment.objects.create(
        registered_app=foreign.medops_app,
        name="production",
        tenant_cluster=make_cluster(foreign, "timeline-foreign2265"),
    )
    foreign_parent = Deployment.objects.create(registered_app=foreign.medops_app, app_environment=foreign_env)
    DeploymentLog.objects.create(
        deployment=foreign_parent, status="failed", message="foreign-organization-marker"
    )
    send = http(world)
    detail = send(DETAIL, row.guid)
    assert not detail.get("errors") and detail["data"]["astroliftDeployment"]["id"] == str(row.guid)
    legacy = send(LEGACY, row.guid)
    assert not legacy.get("errors")
    assert [entry["id"] for entry in legacy["data"]["astroliftDeploymentLog"]] == [
        str(entry.guid) for entry in own
    ]
    assert all(
        entry["deploymentId"] == detail["data"]["astroliftDeployment"]["id"]
        for entry in legacy["data"]["astroliftDeploymentLog"]
    )
    pages, cursor = [], None
    while True:
        result = send(PAGE, row.guid, cursor=cursor)
        assert not result.get("errors")
        page = result["data"]["astroliftDeploymentRunLogPage"]
        assert all(entry["deploymentId"] == str(row.guid) for entry in page["items"])
        pages = page["items"] + pages
        cursor = page["nextCursor"]
        if not cursor:
            assert not page["hasMore"]
            break
    assert [entry["id"] for entry in pages] == [str(entry.guid) for entry in own]
    assert all(
        marker not in json.dumps(legacy) + json.dumps(pages)
        for marker in ("sibling-environment-marker", "sibling-app-marker", "foreign-organization-marker")
    )
    for denied in (world.rows["platform"]["deployment"], foreign_parent):
        assert send(LEGACY, denied.guid).get("errors")
        assert send(PAGE, denied.guid).get("errors")


@pytest.mark.parametrize("query", [LEGACY, PAGE])
@pytest.mark.parametrize("refusal", ["missing-logs-role", "team-ceiling", "environment-policy"])
def test_http_timeline_preserves_logs_permission_team_and_environment_admission(world, query, refusal):
    row = world.rows["medops"]["deployment"]
    target = world.rows["platform"]["deployment"] if refusal == "team-ceiling" else row
    DeploymentLog.objects.create(deployment=target, status="failed", message="denied-timeline-marker")
    send = http(
        world,
        permissions=(Permission.APP_READ,)
        if refusal == "missing-logs-role"
        else (Permission.APP_READ, Permission.APP_READ_LOGS),
        org_grant=refusal == "team-ceiling",
        team=world.medops if refusal == "team-ceiling" else None,
    )
    if refusal == "environment-policy":
        Policy.objects.create(
            organization=world.org,
            name="No production timeline",
            slug="2265-env",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.read_logs",
            resource_pattern={"env": ["production"]},
        )
    assert not send(DETAIL, row.guid).get("errors")
    denied = send(query, target.guid)
    assert denied.get("errors") and "denied-timeline-marker" not in json.dumps(denied)


def test_converter_refuses_foreign_parent_and_never_fetches_parent_per_row(world):
    row = world.rows["medops"]["deployment"]
    entries = [DeploymentLog.objects.create(deployment=row, status="pending") for _ in range(10)]
    entries = list(DeploymentLog.objects.filter(pk__in=[entry.pk for entry in entries]))
    with CaptureQueriesContext(connection) as queries:
        rendered = [deployment_log_to_type(entry, deployment=row) for entry in entries]
    assert len(queries) == 0
    assert {entry.deployment_id for entry in rendered} == {str(row.guid)}
    with pytest.raises(ValueError, match="resolved deployment"):
        deployment_log_to_type(entries[0], deployment=world.rows["platform"]["deployment"])


def test_legacy_oldest_cap_and_paged_newest_window_keep_guid_identity(world):
    row = world.rows["medops"]["deployment"]
    bind_role(
        world.user,
        permissions=[Permission.APP_READ_LOGS],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="2265-cap",
    )
    DeploymentLog.objects.bulk_create(
        [DeploymentLog(deployment=row, status="deploying", message=f"line-{i}") for i in range(1003)]
    )
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=world.user, auth=None)))
    with subject(world):
        legacy = LifecycleQuery().astrolift_deployment_log(info, str(row.guid))
        newest = LifecycleQuery().astrolift_deployment_run_log_page(info, str(row.guid), limit=2)
    assert len(legacy) == 1000 and [legacy[0].message, legacy[-1].message] == ["line-0", "line-999"]
    assert [entry.message for entry in newest.items] == ["line-1001", "line-1002"]
    assert all(entry.deployment_id == str(row.guid) for entry in [*legacy, *newest.items])
