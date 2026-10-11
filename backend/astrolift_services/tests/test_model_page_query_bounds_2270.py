import copy

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.model_connection_policy import effective_policy
from astrolift_services.model_connection_requests import reviewed_versions
from astrolift_services.models import ManagedService, ModelConnectionRequest
from astrolift_services.tests.test_model_connection_2270 import (
    graphql_http,
    http_token,
    placement_wire,
    settings,
)
from astrolift_services.tests.test_model_connection_2270 import world as source_world
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return source_world.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    from astrolift_services.tests.test_cluster_model_mutations_2213 import queue as source_queue

    return source_queue.__wrapped__(monkeypatch)


QUERIES = {
    "targets": (
        "modelConnectionTargetsPage",
        "query($input:ModelConnectionPlacementInput!){modelConnectionTargetsPage(input:$input,pageSize:25){totalCount items{environmentId eligible action policyVersion}}}",
    ),
    "requests": (
        "modelConnectionRequestsPage",
        "query($org:GUID!){modelConnectionRequestsPage(organizationId:$org,pageSize:25){totalCount items{id status approvalCount canCancel canFinalize}}}",
    ),
    "inbox": (
        "modelConnectionApprovalRequestsPage",
        "query($org:GUID!){modelConnectionApprovalRequestsPage(organizationId:$org,pageSize:25){totalCount items{id status approvalCount canApprove canReject}}}",
    ),
    "inventory": (
        "clusterModelDeploymentsPage",
        "query($org:GUID!){clusterModelDeploymentsPage(organizationId:$org,pageSize:25){totalCount items{id name sharingMode dedicatedAppId dedicatedAppName}}}",
    ),
}


def pending(w, env, index):
    import uuid

    return ModelConnectionRequest.objects.create(
        organization=w.org,
        model_deployment=w.model,
        registered_app=w.medops_app,
        app_environment=env,
        tenant_cluster=w.cluster,
        provider_plugin=w.cluster.provider_plugin,
        requester=w.user,
        alias=f"chat{index}",
        idempotency_key=uuid.uuid4(),
        reviewed_versions=reviewed_versions(w.model, env),
        policy_version=effective_policy(w.model).version,
        required_approvals=1,
        allow_self_approval=False,
    )


@pytest.mark.parametrize(
    "kind,votes",
    [(kind, "none") for kind in QUERIES]
    + [(kind, credential) for kind in ["requests", "inbox"] for credential in ["token", "session"]],
)
def test_actual_http_pg_page_query_count_1_vs_25(world, client, kind, votes):
    settings(world)
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_APPROVE_DEPLOY],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="query-count-app",
    )
    bind_role(
        world.user,
        permissions=[Permission.ORG_UPDATE],
        kind="ORG",
        scope_id=world.org.pk,
        slug="query-count-review",
    )
    world.model.config = {
        **world.model.config,
        "sharing_mode": "dedicated",
        "dedicated_app_id": str(world.medops_app.guid),
    }
    world.model.save()
    first = pending(world, world.env, 0)
    if votes != "none":
        add_vote(world, first, votes, 0)
    _, headers = http_token(world, scopes=["admin"])
    field, query = QUERIES[kind]
    variables = {"input": placement_wire(world)} if kind == "targets" else {"org": str(world.org.guid)}
    results = {}
    for size in [1, 25]:
        if size == 25:
            for i in range(1, 25):
                env = AppEnvironment.objects.create(
                    registered_app=world.medops_app, tenant_cluster=world.cluster, name=f"env_{i}"
                )
                row = pending(world, env, i)
                if votes != "none":
                    add_vote(world, row, votes, i)
                ManagedService.objects.create(
                    organization=world.org,
                    tenant_cluster=world.cluster,
                    kind="model_endpoint",
                    variant="vllm",
                    name=f"model_{i}",
                    config=copy.deepcopy(world.model.config),
                )
        warm = graphql_http(client, headers, query, variables)
        assert not warm.get("errors"), warm
        with CaptureQueriesContext(connection) as capture:
            result = graphql_http(client, headers, query, variables)
        assert not result.get("errors"), result
        rows = result["data"][field]["items"]
        assert len(rows) == size, (kind, result)
        if votes != "none":
            assert all(row["approvalCount"] == 1 for row in rows), result
        results[str(size)] = {"query_count": len(capture), "returned_rows": len(rows)}
    print(f"page={kind} votes={votes} actual_HTTP_PG_counts={results}")
    assert results["25"]["query_count"] <= results["1"]["query_count"] + 8, results


def add_vote(w, row, credential, index):
    from uuid import uuid4

    from django.test import Client

    from astrolift_identity.models import ApiToken, AstroliftSession, Member
    from astrolift_services.models import ModelConnectionApproval
    from core.tests.utils.scope_world import make_user

    actor = make_user(f"page-voter-{index}")
    Member.objects.create(user=actor, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        actor,
        permissions=[Permission.ORG_UPDATE, Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=w.org.pk,
        slug=f"page-vote-role-{index}",
    )
    if credential == "token":
        source = {
            "api_token": ApiToken.objects.create(
                user=actor, organization=w.org, name="page-voter", token_hash=uuid4().hex, scopes=["admin"]
            )
        }
    else:
        login = Client()
        login.force_login(actor)
        source = {
            "session": AstroliftSession.objects.create(
                user=actor, organization=w.org, session_key=login.session.session_key
            )
        }
    ModelConnectionApproval.objects.create(request=row, voter=actor, **source)
    row.status = "approved"
    row.save()
    return actor, source


def requester_page(w, client, headers):
    field, query = QUERIES["requests"]
    result = graphql_http(client, headers, query, {"org": str(w.org.guid)})
    assert not result.get("errors"), result
    return result["data"][field]["items"][0]


@pytest.mark.parametrize(
    "withdrawal",
    [
        "revoke",
        "scope",
        "expired",
        "deleted",
        "actor",
        "membership",
        "role",
        "role_expired",
        "foreign_actor",
        "foreign_org",
    ],
)
def test_current_page_requalifies_bearer_voter(world, client, withdrawal):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity.models import Member, RoleBinding

    settings(world)
    row = pending(world, world.env, 0)
    voter, source = add_vote(world, row, "token", 0)
    token = source["api_token"]
    _, headers = http_token(world, scopes=["write:apps"])
    initial = requester_page(world, client, headers)
    assert initial["approvalCount"] == 1 and initial["canFinalize"]
    if withdrawal == "revoke":
        token.is_revoked = True
    elif withdrawal == "scope":
        token.scopes = ["read:apps"]
    elif withdrawal == "expired":
        token.expires_at = timezone.now() - timedelta(seconds=1)
    elif withdrawal == "deleted":
        token.deleted_at = timezone.now()
    elif withdrawal == "foreign_actor":
        token.user = world.user
    elif withdrawal == "foreign_org":
        token.organization = world.other_org
    elif withdrawal == "actor":
        voter.is_active = False
        voter.save()
    elif withdrawal == "membership":
        Member.objects.filter(user=voter, scope_kind="ORG", scope_id=world.org.pk).update(is_active=False)
    elif withdrawal == "role":
        RoleBinding.objects.filter(user=voter).update(deleted_at=timezone.now())
    elif withdrawal == "role_expired":
        RoleBinding.objects.filter(user=voter).update(expires_at=timezone.now() - timedelta(seconds=1))
    token.save()
    current = requester_page(world, client, headers)
    assert current["approvalCount"] == 0 and not current["canFinalize"]
    row.refresh_from_db()
    assert row.status == "approved" and row.subscription_id is None


@pytest.mark.parametrize(
    "withdrawal",
    ["logout", "expired", "hash", "revoked_sidecar", "deleted_sidecar", "expired_sidecar", "foreign_sidecar"],
)
def test_current_page_requalifies_authenticated_session_voter(world, client, withdrawal):
    from datetime import timedelta

    from django.contrib.sessions.models import Session
    from django.utils import timezone

    settings(world)
    row = pending(world, world.env, 0)
    voter, source = add_vote(world, row, "session", 0)
    sidecar = source["session"]
    _, headers = http_token(world, scopes=["write:apps"])
    assert requester_page(world, client, headers)["approvalCount"] == 1
    if withdrawal == "logout":
        Session.objects.filter(session_key=sidecar.session_key).delete()
    elif withdrawal == "expired":
        Session.objects.filter(session_key=sidecar.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
    elif withdrawal == "hash":
        voter.set_password("changed-after-approval")
        voter.save()
    elif withdrawal == "revoked_sidecar":
        sidecar.revoked_at = timezone.now()
    elif withdrawal == "deleted_sidecar":
        sidecar.deleted_at = timezone.now()
    elif withdrawal == "expired_sidecar":
        sidecar.expires_at = timezone.now() - timedelta(seconds=1)
    elif withdrawal == "foreign_sidecar":
        sidecar.user = world.user
    if "sidecar" in withdrawal:
        sidecar.save()
    current = requester_page(world, client, headers)
    assert current["approvalCount"] == 0 and not current["canFinalize"]
    sidecar.refresh_from_db()
    if withdrawal == "revoked_sidecar":
        assert sidecar.revoked_at is not None
    if withdrawal == "deleted_sidecar":
        assert sidecar.deleted_at is not None


def test_page_computes_stale_without_writing_but_detail_retains_durable_transition(world, client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    policy = settings(world)
    row = pending(world, world.env, 0)
    original_version = row.version
    policy.mode = "DENY"
    policy.save()
    _, headers = http_token(world, scopes=["write:apps"])
    with CaptureQueriesContext(connection) as queries:
        item = requester_page(world, client, headers)
    assert item["status"] == "STALE" and not item["canCancel"] and not item["canFinalize"]
    row.refresh_from_db()
    assert row.status == "pending" and row.version == original_version
    assert not any(
        "astrolift_services_modelconnectionrequest" in query["sql"]
        and query["sql"].lstrip().startswith(("UPDATE", "INSERT", "DELETE"))
        for query in queries
    )
    result = graphql_http(
        client,
        headers,
        "query($input:DecideModelConnectionRequestInput!){modelConnectionRequest(input:$input){id version status}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": original_version}},
    )
    assert not result.get("errors"), result
    assert result["data"]["modelConnectionRequest"]["status"] == "STALE"
    row.refresh_from_db()
    assert row.status == "stale" and row.version > original_version


def test_page_voter_abac_evaluates_each_exact_environment(world, client):
    from astrolift_identity.models import Policy

    settings(world)
    row = pending(world, world.env, 0)
    voter, source = add_vote(world, row, "token", 0)
    other = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="denied_env"
    )
    second = pending(world, other, 1)
    from astrolift_services.models import ModelConnectionApproval

    ModelConnectionApproval.objects.create(request=second, voter=voter, **source)
    second.status = "approved"
    second.save()
    Policy.objects.create(
        organization=world.org,
        name="one-env",
        slug="one-env",
        scope_level="ORG",
        action_pattern="app.approve_deploy",
        effect="DENY",
        resource_pattern={"env": "denied_env"},
        conditions=[],
    )
    # Both requests bind the current policy; the vote denial is per environment.
    version = effective_policy(world.model).version
    ModelConnectionRequest.objects.filter(pk__in=[row.pk, second.pk]).update(policy_version=version)
    _, headers = http_token(world, scopes=["write:apps"])
    field, query = QUERIES["requests"]
    result = graphql_http(client, headers, query, {"org": str(world.org.guid)})
    assert not result.get("errors"), result
    items = {item["id"]: item for item in result["data"][field]["items"]}
    assert items[str(row.guid)]["approvalCount"] == 1
    assert items[str(second.guid)]["approvalCount"] == 0
    assert items[str(row.guid)]["canFinalize"] and not items[str(second.guid)]["canFinalize"]


@pytest.mark.parametrize("withdrawal", ["removed", "deleted", "renamed", "mapping_deleted"])
def test_page_voter_group_mapping_preserves_current_scim_authority(world, client, withdrawal):
    from django.utils import timezone

    from astrolift_identity.models import GroupRoleMapping, Member, RoleBinding, ScimGroup

    settings(world)
    row = pending(world, world.env, 0)
    voter, _ = add_vote(world, row, "token", 0)
    role = RoleBinding.objects.get(user=voter).role
    RoleBinding.objects.filter(user=voter).update(deleted_at=timezone.now())
    member = Member.objects.get(user=voter, scope_kind="ORG", scope_id=world.org.pk)
    member.idp_groups = ["managed-voters", "retired-voters"]
    member.save()
    tombstone = ScimGroup.objects.create(
        organization=world.org,
        display_name="old",
        external_id="managed-voters",
        retired_external_ids=["retired-voters"],
    )
    tombstone.soft_delete()
    group = ScimGroup.objects.create(
        organization=world.org, display_name="current", external_id="managed-voters"
    )
    group.members.add(member)
    mapping = GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="managed-voters",
        role=role,
        scope_kind="ORG",
        scope_id=world.org.pk,
    )
    _, headers = http_token(world, scopes=["write:apps"])
    assert requester_page(world, client, headers)["approvalCount"] == 1
    if withdrawal == "removed":
        group.members.remove(member)
    elif withdrawal == "deleted":
        group.soft_delete()
    elif withdrawal == "renamed":
        group.retired_external_ids = ["managed-voters"]
        group.external_id = "new-voters"
        group.save()
    else:
        mapping.soft_delete()
    assert requester_page(world, client, headers)["approvalCount"] == 0


def test_inventory_batch_does_not_disclose_unreadable_or_stale_dedicated_app(world, client):
    from django.utils import timezone

    from astrolift_identity.models import RoleBinding

    world.model.config = {
        **world.model.config,
        "sharing_mode": "dedicated",
        "dedicated_app_id": str(world.medops_app.guid),
    }
    world.model.save()
    _, headers = http_token(world, scopes=["admin"])
    field, query = QUERIES["inventory"]

    def read():
        result = graphql_http(client, headers, query, {"org": str(world.org.guid)})
        assert not result.get("errors"), result
        return result["data"][field]["items"][0]

    assert read()["dedicatedAppId"] is None
    binding = bind_role(
        world.user,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="dedicated-read",
    )
    assert read()["dedicatedAppId"] == str(world.medops_app.guid)
    world.medops_app.project.deleted_at = timezone.now()
    world.medops_app.project.save()
    assert read()["dedicatedAppId"] is None
    RoleBinding.objects.filter(pk=binding.pk).update(deleted_at=timezone.now())


def test_session_voter_freshness_uses_current_bag_and_tracked_time(world, client):
    from datetime import timedelta

    from django.contrib.sessions.backends.db import SessionStore
    from django.utils import timezone

    from astrolift_identity.models import Policy
    from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY

    settings(world)
    Policy.objects.create(
        organization=world.org,
        name="vote-age",
        slug="vote-age",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.approve_deploy",
        conditions=[{"kind": "freshness", "max_session_age_minutes": 1}],
    )
    row = pending(world, world.env, 0)
    _, source = add_vote(world, row, "session", 0)
    _, headers = http_token(world, scopes=["write:apps"])
    assert requester_page(world, client, headers)["approvalCount"] == 1
    # The fallback tracked sign-in is current. Explicit older verified SSO time wins.
    store = SessionStore(source["session"].session_key)
    store[SESSION_SSO_AUTH_TIME_KEY] = int((timezone.now() - timedelta(hours=1)).timestamp())
    store.save()
    assert requester_page(world, client, headers)["approvalCount"] == 0


@pytest.mark.parametrize(
    "level,inherits,expected", [("deployer", True, 1), ("viewer", True, 0), ("owner", False, 0)]
)
def test_voter_shared_team_grants_keep_access_and_inheritance_ceiling(
    world, client, level, inherits, expected
):
    from django.utils import timezone

    from astrolift_identity.models import RoleBinding, Team
    from astrolift_registry.models import AppTeamAccess

    settings(world)
    row = pending(world, world.env, 0)
    voter, _ = add_vote(world, row, "token", 0)
    RoleBinding.objects.filter(user=voter).update(deleted_at=timezone.now())
    bind_role(
        voter, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=world.org.pk, slug="vote-org-only"
    )
    team = Team.objects.create(organization=world.org, name="Reviewer team", slug="reviewer-team")
    binding = bind_role(
        voter, permissions=[Permission.APP_APPROVE_DEPLOY], kind="TEAM", scope_id=team.pk, slug="vote-shared"
    )
    binding.inherits = inherits
    binding.save()
    AppTeamAccess.objects.create(registered_app=world.medops_app, team=team, access_level=level)
    _, headers = http_token(world, scopes=["write:apps"])
    assert requester_page(world, client, headers)["approvalCount"] == expected


@pytest.mark.parametrize("operation", ["detail", "finalize", "page"])
def test_actual_http_same_version_env_reassignment_refuses_reviewed_request(world, queue, client, operation):
    from uuid import uuid4

    from astrolift_identity.models import ApiToken, Member
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_services.models import ManagedServiceAttachment, ModelConnectionApproval
    from core.tests.utils.scope_world import make_user

    settings(world)
    row = ModelConnectionRequest.objects.create(
        organization=world.org,
        model_deployment=world.model,
        registered_app=world.medops_app,
        app_environment=world.env,
        tenant_cluster=world.cluster,
        provider_plugin=world.cluster.provider_plugin,
        requester=world.user,
        alias="chat",
        idempotency_key=uuid4(),
        reviewed_versions=reviewed_versions(world.model, world.env),
        policy_version=effective_policy(world.model).version,
        required_approvals=1,
        allow_self_approval=False,
        status="approved",
    )
    voter = make_user("reassignment-voter")
    Member.objects.create(user=voter, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        voter,
        permissions=[Permission.ORG_UPDATE, Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=world.org.pk,
        slug="reassignment-reviewer",
    )
    token = ApiToken.objects.create(
        user=voter, organization=world.org, name="reviewer", token_hash=uuid4().hex, scopes=["admin"]
    )
    ModelConnectionApproval.objects.create(request=row, voter=voter, api_token=token)
    replacement = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops_app.team,
        project=world.medops_app.project,
        name="Replacement app",
        slug="replacement-app",
        k8s_namespace="",
    )
    RegisteredApp.objects.filter(pk=replacement.pk).update(version=world.medops_app.version)
    replacement.refresh_from_db()
    Workload.objects.create(registered_app=replacement, name="web", slug="replacement-web", kind="deployment")
    bind_role(
        world.user,
        permissions=[Permission.APP_UPDATE],
        kind="APP",
        scope_id=replacement.pk,
        slug="replacement-write",
    )
    AppEnvironment.objects.filter(pk=world.env.pk).update(registered_app=replacement)
    assert replacement.pk != row.registered_app_id and replacement.version == row.reviewed_versions["app"]
    # A fresh picker must identify the replacement app, rather than the old reviewed owner.
    _, picker_headers = http_token(world, scopes=["admin"])
    picker = graphql_http(
        client,
        picker_headers,
        "query($input:ModelConnectionPlacementInput!){modelConnectionTargetsPage(input:$input){items{environmentId appId eligible action}}}",
        {"input": placement_wire(world)},
    )
    assert not picker.get("errors"), picker
    selected = next(
        item
        for item in picker["data"]["modelConnectionTargetsPage"]["items"]
        if item["environmentId"] == str(world.env.guid)
    )
    assert selected["appId"] == str(replacement.guid) and selected["action"] == "REQUEST"
    before = (world.model.version, world.model.subscription_revision, row.version)
    _, headers = http_token(world, scopes=["admin"])
    if operation == "detail":
        result = graphql_http(
            client,
            headers,
            "query($input:DecideModelConnectionRequestInput!){modelConnectionRequest(input:$input){id appId version status canFinalize}}",
            {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
        )
        assert not result.get("errors"), result
        assert result["data"]["modelConnectionRequest"] is None, result
    elif operation == "finalize":
        result = graphql_http(
            client,
            headers,
            "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{message} data{id appId subscriptionId}}}",
            {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
        )
        assert not result.get("errors"), result
        assert not result["data"]["finalizeModelConnectionRequest"]["ok"], result
    else:
        result = graphql_http(
            client,
            headers,
            "query($org:GUID!){modelConnectionRequestsPage(organizationId:$org){items{id canFinalize canCancel canApprove}}}",
            {"org": str(world.org.guid)},
        )
        assert not result.get("errors"), result
        assert result["data"]["modelConnectionRequestsPage"]["items"] == [], result
    assert not ManagedServiceAttachment.objects.exists() and queue == []
    row.refresh_from_db()
    world.model.refresh_from_db()
    assert (world.model.version, world.model.subscription_revision, row.version) == before


@pytest.mark.parametrize("operation", ["page", "finalize"])
@pytest.mark.parametrize(
    "withdrawal", ["foreign_token_role", "foreign_session_role", "operator_token_membership"]
)
def test_current_approval_rejects_foreign_role_and_withdrawn_operator_bearer(
    world, client, queue, operation, withdrawal
):
    from astrolift_identity.models import Member, RoleBinding
    from astrolift_services.models import ManagedServiceAttachment

    settings(world)
    row = pending(world, world.env, 0)
    voter, _ = add_vote(world, row, "session" if withdrawal == "foreign_session_role" else "token", 0)
    if withdrawal == "operator_token_membership":
        voter.is_superuser = True
        voter.save()
        Member.objects.filter(user=voter, scope_kind="ORG", scope_id=world.org.pk).update(is_active=False)
    else:
        role = RoleBinding.objects.get(user=voter).role
        role.organization = world.other_org
        role.save()
    _, headers = http_token(world, scopes=["admin"])
    before = (world.model.version, world.model.subscription_revision)
    if operation == "page":
        current = requester_page(world, client, headers)
        assert current["approvalCount"] == 0 and not current["canFinalize"], current
    else:
        reply = graphql_http(
            client,
            headers,
            "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{message} data{status subscriptionId}}}",
            {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
        )
        assert not reply.get("errors"), reply
        outcome = reply["data"]["finalizeModelConnectionRequest"]
        assert outcome["ok"] is True and outcome["data"] == {"status": "STALE", "subscriptionId": None}, reply
    assert not ManagedServiceAttachment.objects.exists() and queue == []
    world.model.refresh_from_db()
    assert (world.model.version, world.model.subscription_revision) == before


@pytest.mark.parametrize("page", ["requester", "reviewer"])
def test_reassigned_environment_never_exposes_old_app_request_to_new_app_only_reader(world, client, page):
    from astrolift_identity.models import RoleBinding
    from astrolift_registry.models import RegisteredApp

    settings(world)
    row = pending(world, world.env, 0)
    replacement = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops_app.team,
        project=world.medops_app.project,
        name="Replacement app",
        slug="replacement-only",
        k8s_namespace="",
    )
    RoleBinding.objects.filter(user=world.user, scope_kind="APP").delete()
    bind_role(
        world.user,
        permissions=[Permission.APP_UPDATE, Permission.APP_APPROVE_DEPLOY],
        kind="APP",
        scope_id=replacement.pk,
        slug="replacement-only-authority",
    )
    bind_role(
        world.user,
        permissions=[Permission.ORG_UPDATE],
        kind="ORG",
        scope_id=world.org.pk,
        slug="replacement-reviewer",
    )
    AppEnvironment.objects.filter(pk=world.env.pk).update(registered_app=replacement)
    _, headers = http_token(world, scopes=["admin"])
    field = "modelConnectionRequestsPage" if page == "requester" else "modelConnectionApprovalRequestsPage"
    result = graphql_http(
        client,
        headers,
        f"query($org:GUID!){{{field}(organizationId:$org){{totalCount items{{id appId appName requesterUsername}}}}}}",
        {"org": str(world.org.guid)},
    )
    assert not result.get("errors"), result
    assert result["data"][field] == {"totalCount": 0, "items": []}, result
    row.refresh_from_db()
    assert row.registered_app_id == world.medops_app.pk and row.status == "pending"


@pytest.mark.parametrize("role_owner", ["global", "current_org", "browser_operator_without_member"])
def test_current_approval_preserves_valid_roles_and_browser_operator_rule(world, client, queue, role_owner):
    from astrolift_identity.models import Member, RoleBinding
    from astrolift_services.models import ManagedServiceAttachment

    settings(world)
    row = pending(world, world.env, 0)
    voter, _ = add_vote(
        world, row, "session" if role_owner == "browser_operator_without_member" else "token", 0
    )
    if role_owner == "current_org":
        role = RoleBinding.objects.get(user=voter).role
        role.organization = world.org
        role.save()
    elif role_owner == "browser_operator_without_member":
        voter.is_superuser = True
        voter.save()
        Member.objects.filter(user=voter, scope_kind="ORG", scope_id=world.org.pk).update(is_active=False)
    _, headers = http_token(world, scopes=["admin"])
    current = requester_page(world, client, headers)
    assert current["approvalCount"] == 1 and current["canFinalize"], current
    reply = graphql_http(
        client,
        headers,
        "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok data{status subscriptionId}}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert not reply.get("errors"), reply
    outcome = reply["data"]["finalizeModelConnectionRequest"]
    assert outcome["ok"] is True and outcome["data"]["subscriptionId"] is not None, reply
    assert ManagedServiceAttachment.objects.count() == 1 and len(queue) == 1
