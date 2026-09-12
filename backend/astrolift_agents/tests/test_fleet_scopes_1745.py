"""Fleet and task operations must agree on real sub-org role grants."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_registry.models import AppTeamAccess, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def fleet():
    world = ScopeWorld("1745")
    user = make_user("1745")
    agents = [
        Workload.objects.create(registered_app=app, name=side, slug=side, kind=Workload.Kind.AGENT)
        for app, side in [(world.medops_app, "medops"), (world.platform_app, "platform")]
    ]
    tasks = [AgentTask.objects.create(organization=world.org, agent_definition=agent) for agent in agents]
    orphan = AgentTask.objects.create(organization=world.org)
    return SimpleNamespace(
        world=world, user=user, info=make_info(user), agents=agents, tasks=tasks, orphan=orphan
    )


def bind(fleet, kind="TEAM", permissions=None):
    world = fleet.world
    target = {
        "TEAM": world.medops,
        "PROJECT": world.medops_project,
        "APP": world.medops_app,
        "ORG": world.org,
    }[kind]
    return bind_role(
        fleet.user,
        permissions=permissions or [Permission.AGENT_READ, Permission.APP_READ],
        kind=kind,
        scope_id=target.pk,
        slug=f"fleet-{kind}-{'-'.join(p.value for p in permissions or [])}"[:190],
    )


def tenant(fleet, selected=False):
    return tenant_context(
        TenantContext(
            organization_id=fleet.world.org.pk,
            actor_user_id=fleet.user.pk,
            team_id=fleet.world.medops.pk if selected else None,
            project_id=fleet.world.medops_project.pk if selected else None,
        )
    )


@contextmanager
def token(fleet, *, team=None, org=None, scopes=("admin",)):
    credential = ApiToken.objects.create(
        user=fleet.user,
        organization=org or fleet.world.org,
        team=team,
        name="fleet-token",
        token_hash=uuid4().hex * 2,
        token_last_4="test",
        scopes=list(scopes),
    )
    marker = set_current_api_token(credential)
    try:
        yield credential
    finally:
        reset_current_api_token(marker)


def running(task):
    task.vnc_enabled = True
    task.pod_name = f"task-{task.guid}"
    task.snapshot_key = f"snapshots/{task.guid}/latest.jpg"
    task.save(update_fields=["vnc_enabled", "pod_name", "snapshot_key"])
    for status in [AgentTask.Status.QUEUED, AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING]:
        task.transition_to(status)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM"])
@pytest.mark.parametrize(
    "method",
    ["agent_workloads", "agent_fleet", "agent_tasks", "agent_task_transitions_since", "agent_live_status"],
)
def test_fleet_lists_only_the_granted_scope(fleet, selected, kind, method):
    bind(fleet, kind)
    with tenant(fleet, selected):
        rows = getattr(AgentsQuery(), method)(fleet.info, org_id=str(fleet.world.org.guid))
    expected = (
        fleet.tasks[0] if method in {"agent_tasks", "agent_task_transitions_since"} else fleet.agents[0]
    )
    ids = {str(row.workload_id if method == "agent_live_status" else row.id) for row in rows}
    assert ids == {str(expected.guid)}


@pytest.mark.parametrize("method", ["agent_task", "agent_task_logs"])
def test_selected_team_cannot_inspect_a_sibling_task(fleet, method):
    bind(fleet)
    with tenant(fleet, True), pytest.raises(PermissionDenied):
        getattr(AgentsQuery(), method)(fleet.info, id=str(fleet.tasks[1].guid))


def test_app_grant_can_inspect_the_task_it_dispatched(fleet):
    bind(fleet, "APP")
    with tenant(fleet):
        row = AgentsQuery().agent_task(fleet.info, id=str(fleet.tasks[0].guid))
    assert str(row.id) == str(fleet.tasks[0].guid)


@pytest.mark.parametrize("method", ["cancel_task", "send_agent_task_input"])
def test_selected_team_cannot_control_a_sibling_or_org_only_task(fleet, method):
    bind(fleet, permissions=[Permission.AGENT_DISPATCH, Permission.AGENT_TASK_SEND_INPUT])
    for task in [fleet.tasks[1], fleet.orphan]:
        args = (
            {"id": str(task.guid)}
            if method == "cancel_task"
            else {"task_id": str(task.guid), "message": "continue"}
        )
        with tenant(fleet, True):
            result = getattr(AgentsMutation(), method)(fleet.info, **args)
        assert not result.ok
        assert any(error.code == "PERMISSION_DENIED" for error in result.errors)
        task.refresh_from_db()
        assert task.status == AgentTask.Status.DRAFT


def test_read_permission_does_not_mint_a_framebuffer_url(fleet, monkeypatch):
    bind(fleet, "ORG")
    task = fleet.tasks[0]
    task.vnc_enabled = True
    task.snapshot_key = f"snapshots/{task.guid}/latest.jpg"
    task.save(update_fields=["vnc_enabled", "snapshot_key"])
    for status in [AgentTask.Status.QUEUED, AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING]:
        task.transition_to(status)
    minted = []
    monkeypatch.setattr(
        "astrolift_agents.snapshot_store.presigned_snapshot_download_url",
        lambda **kwargs: minted.append(kwargs) or "https://frames.example/frame",
    )
    with tenant(fleet):
        row = AgentsQuery().agent_task(fleet.info, id=str(task.guid))
    assert row.snapshot_url is None
    assert not minted


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP", "ORG"])
def test_own_scope_can_cancel_and_send_input(fleet, kind):
    bind(fleet, kind, [Permission.AGENT_DISPATCH, Permission.AGENT_TASK_SEND_INPUT])
    with tenant(fleet):
        result = AgentsMutation().cancel_task(fleet.info, id=str(fleet.tasks[0].guid))
    assert result.ok
    fleet.tasks[0].refresh_from_db()
    assert fleet.tasks[0].status == AgentTask.Status.CANCELLED
    task = AgentTask.objects.create(organization=fleet.world.org, agent_definition=fleet.agents[0])
    running(task)
    with tenant(fleet):
        result = AgentsMutation().send_agent_task_input(fleet.info, task_id=str(task.guid), message="next")
    assert result.ok
    assert task.input_messages.get().body == "next"


@pytest.mark.parametrize("owner", ["project", "team"])
def test_recorded_task_owner_takes_precedence_over_the_definition(fleet, owner):
    bind(fleet)
    own = getattr(fleet.world, "medops_project" if owner == "project" else "medops")
    sibling = getattr(fleet.world, "platform_project" if owner == "project" else "platform")
    AgentTask.objects.filter(pk=fleet.tasks[0].pk).update(**{owner: sibling})
    AgentTask.objects.filter(pk=fleet.tasks[1].pk).update(**{owner: own})
    with tenant(fleet, True):
        rows = AgentsQuery().agent_tasks(fleet.info, org_id=str(fleet.world.org.guid))
        assert {str(row.id) for row in rows} == {str(fleet.tasks[1].guid)}
        assert AgentsQuery().agent_task(fleet.info, id=str(fleet.tasks[1].guid)) is not None
        with pytest.raises(PermissionDenied):
            AgentsQuery().agent_task(fleet.info, id=str(fleet.tasks[0].guid))


@pytest.mark.parametrize("owner", ["project", "team", "definition"])
@pytest.mark.parametrize("invalid", ["foreign", "deleted"])
def test_invalid_task_owner_is_org_only(fleet, owner, invalid):
    bind(fleet)
    task = fleet.tasks[0]
    world = ScopeWorld("1745-foreign") if invalid == "foreign" else fleet.world
    if owner == "definition":
        target = Workload.objects.create(
            registered_app=world.medops_app, name="legacy", slug="legacy", kind="agent"
        )
        field = "agent_definition"
    else:
        target = world.medops_project if owner == "project" else world.medops
        field = owner
    AgentTask.objects.filter(pk=task.pk).update(**{field: target})
    if invalid == "deleted":
        type(target).objects.filter(pk=target.pk).update(deleted_at=timezone.now())
    with tenant(fleet, True), pytest.raises(PermissionDenied):
        AgentsQuery().agent_task(fleet.info, id=str(task.guid))
    bind(fleet, "ORG")
    with tenant(fleet):
        assert AgentsQuery().agent_task(fleet.info, id=str(task.guid)) is not None


def test_team_token_remains_a_ceiling_over_an_org_role_and_shares(fleet):
    bind(fleet, "ORG", [Permission.AGENT_READ, Permission.APP_READ, Permission.AGENT_DISPATCH])
    with token(fleet, team=fleet.world.medops), tenant(fleet):
        query = AgentsQuery()
        args = {"org_id": str(fleet.world.org.guid)}
        assert len(query.agent_tasks(fleet.info, **args)) == 1
        assert len(query.agent_fleet(fleet.info, **args)) == 1
        assert query.agent_task(fleet.info, id=str(fleet.tasks[1].guid)) is None
        assert query.agent_task(fleet.info, id=str(fleet.orphan.guid)) is None
        share = AppTeamAccess.objects.create(
            registered_app=fleet.world.platform_app, team=fleet.world.medops, access_level="viewer"
        )
        assert len(query.agent_tasks(fleet.info, **args)) == 2
        assert len(query.agent_fleet(fleet.info, **args)) == 2
        assert not AgentsMutation().cancel_task(fleet.info, id=str(fleet.tasks[1].guid)).ok
        share.access_level = "deployer"
        share.save(update_fields=["access_level"])
        assert AgentsMutation().cancel_task(fleet.info, id=str(fleet.tasks[1].guid)).ok
        share.soft_delete()
        assert len(query.agent_tasks(fleet.info, **args)) == 1


@pytest.mark.parametrize("level", ["viewer", "deployer", "owner"])
def test_shared_team_role_is_limited_by_the_share_level(fleet, level):
    bind(fleet, permissions=[Permission.AGENT_READ, Permission.APP_READ, Permission.AGENT_DISPATCH])
    AppTeamAccess.objects.create(
        registered_app=fleet.world.platform_app, team=fleet.world.medops, access_level=level
    )
    with tenant(fleet):
        assert len(AgentsQuery().agent_fleet(fleet.info, org_id=str(fleet.world.org.guid))) == 2
        assert AgentsQuery().agent_task(fleet.info, id=str(fleet.tasks[1].guid)) is not None
        result = AgentsMutation().cancel_task(fleet.info, id=str(fleet.tasks[1].guid))
    assert result.ok is (level != "viewer")


def test_bearer_permission_ceiling_still_applies(fleet):
    bind(fleet, "ORG", [Permission.AGENT_DISPATCH])
    with token(fleet, scopes=("read:apps",)), tenant(fleet):
        result = AgentsMutation().cancel_task(fleet.info, id=str(fleet.tasks[0].guid))
    assert not result.ok
    fleet.tasks[0].refresh_from_db()
    assert fleet.tasks[0].status == AgentTask.Status.DRAFT


def test_foreign_token_cannot_use_current_org_role(fleet):
    bind(fleet, "ORG")
    foreign = ScopeWorld("1745-token")
    with token(fleet, org=foreign.org), tenant(fleet):
        assert AgentsQuery().agent_tasks(fleet.info, org_id=str(fleet.world.org.guid)) == []
        assert AgentsQuery().agent_fleet(fleet.info, org_id=str(fleet.world.org.guid)) == []


@pytest.mark.parametrize("method", ["agent", "agent_triggers", "agent_triggers_page"])
def test_agent_detail_and_triggers_deny_sibling_scope(fleet, method):
    bind(fleet)
    kwargs = {"slug" if method == "agent" else "agent_slug": fleet.agents[1].slug}
    with tenant(fleet, True), pytest.raises(PermissionDenied):
        getattr(AgentsQuery(), method)(fleet.info, org_id=str(fleet.world.org.guid), **kwargs)


def test_ambiguous_agent_slug_does_not_choose_an_arbitrary_app(fleet):
    bind(fleet, "ORG")
    Workload.objects.filter(pk=fleet.agents[1].pk).update(slug=fleet.agents[0].slug)
    with tenant(fleet):
        assert (
            AgentsQuery().agent(fleet.info, org_id=str(fleet.world.org.guid), slug=fleet.agents[0].slug)
            is None
        )
        page = AgentsQuery().agent_triggers_page(
            fleet.info, org_id=str(fleet.world.org.guid), agent_slug=fleet.agents[0].slug
        )
    assert not page.items and page.total_count == 0


def test_task_interactions_follow_task_ownership(fleet):
    from astrolift_agents.models.agent_interaction import record_interaction

    bind(fleet)
    for task in fleet.tasks:
        record_interaction(task, kind="signal", name="continue")
    with tenant(fleet, True):
        assert (
            len(
                AgentsQuery().agent_task_interactions(
                    fleet.info, org_id=str(fleet.world.org.guid), task_id=str(fleet.tasks[0].guid)
                )
            )
            == 1
        )
        with pytest.raises(PermissionDenied):
            AgentsQuery().agent_task_interactions(
                fleet.info, org_id=str(fleet.world.org.guid), task_id=str(fleet.tasks[1].guid)
            )


def test_gallery_and_vnc_apply_the_same_watch_scope(fleet, monkeypatch):
    from core.schema.vnc_ws import _check_vnc_permission

    bind(fleet, "APP", [Permission.AGENT_TASK_WATCH])
    for task in fleet.tasks:
        running(task)
    monkeypatch.setattr(
        "astrolift_agents.snapshot_store.presigned_snapshot_download_url",
        lambda **kwargs: "https://frames.example/frame",
    )
    with tenant(fleet):
        rows = AgentsQuery().agent_gallery(fleet.info, org_id=str(fleet.world.org.guid))
    assert [str(row.id) for row in rows] == [str(fleet.tasks[0].guid)]
    assert rows[0].snapshot_url and rows[0].vnc_url
    for task, expected in [(fleet.tasks[0], True), (fleet.tasks[1], False), (fleet.orphan, False)]:
        assert (
            _check_vnc_permission.func(
                tenant_org_id=fleet.world.org.pk, actor_user_id=fleet.user.pk, task_guid=str(task.guid)
            )
            is expected
        )


def test_vnc_org_role_cannot_override_the_token_row_boundary(fleet):
    from core.schema.vnc_ws import _check_vnc_permission

    bind(fleet, "ORG", [Permission.AGENT_TASK_WATCH])
    with token(fleet, team=fleet.world.medops):
        for task, expected in [(fleet.tasks[0], True), (fleet.tasks[1], False), (fleet.orphan, False)]:
            assert (
                _check_vnc_permission.func(
                    tenant_org_id=fleet.world.org.pk, actor_user_id=fleet.user.pk, task_guid=str(task.guid)
                )
                is expected
            )


def test_list_and_snapshot_authorization_do_not_add_per_task_queries(fleet, monkeypatch):
    bind(fleet, "ORG", [Permission.AGENT_READ, Permission.AGENT_TASK_WATCH])
    for task in fleet.tasks:
        running(task)
    monkeypatch.setattr(
        "astrolift_agents.snapshot_store.presigned_snapshot_download_url",
        lambda **kwargs: "https://frames.example/frame",
    )
    args = {"org_id": str(fleet.world.org.guid)}
    with tenant(fleet):
        with CaptureQueriesContext(connection) as one:
            AgentsQuery().agent_tasks(fleet.info, workload_id=str(fleet.agents[0].guid), **args)
        with CaptureQueriesContext(connection) as two:
            rows = AgentsQuery().agent_tasks(fleet.info, **args)
    # Filtering by workload adds one lookup; growing the result set adds none.
    assert len(two) <= len(one)
    assert sum(bool(row.snapshot_url) for row in rows) == 2


def test_row_filtering_precedes_caps_and_compiled_graphql_projection(fleet, monkeypatch):
    from config.schema import schema

    bind(fleet)
    monkeypatch.setattr("astrolift_agents.schema.queries._AGENT_LIST_CAP", 1)
    with tenant(fleet, True):
        result = schema.execute_sync(
            "query($org: ID!) { agentFleet(orgId: $org) { id } agentTasks(orgId: $org) { id } }",
            variable_values={"org": str(fleet.world.org.guid)},
            context_value=fleet.info.context,
        )
    assert result.errors is None
    assert result.data == {
        "agentFleet": [{"id": str(fleet.agents[0].guid)}],
        "agentTasks": [{"id": str(fleet.tasks[0].guid)}],
    }


def test_expired_grant_does_not_open_the_fleet(fleet):
    role = bind(fleet)
    role.expires_at = timezone.now()
    role.save(update_fields=["expires_at"])
    with tenant(fleet, True), pytest.raises(PermissionDenied):
        AgentsQuery().agent_fleet(fleet.info, org_id=str(fleet.world.org.guid))


def test_dispatch_keeps_the_authorized_workload_when_another_app_reuses_its_slug(fleet, monkeypatch):
    from astrolift_agents.schema.mutations import RunAstroliftAgentInput

    bind(fleet, "APP", [Permission.AGENT_DISPATCH])
    Workload.objects.filter(pk=fleet.agents[1].pk).update(slug=fleet.agents[0].slug)
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow", lambda *args, **kwargs: starts.append(kwargs)
    )
    with tenant(fleet):
        result = AgentsMutation().run_astrolift_agent(
            fleet.info, input=RunAstroliftAgentInput(agent_slug=fleet.agents[0].slug)
        )
    assert result.ok and len(starts) == 1
    task = AgentTask.objects.get(guid=str(result.data.id))
    assert task.agent_definition_id == fleet.agents[0].pk
    assert task.status == AgentTask.Status.QUEUED


@pytest.mark.parametrize(
    "kind,team_token,allowed", [("TEAM", False, False), ("ORG", True, False), ("ORG", False, True)]
)
def test_legacy_org_task_launch_requires_org_authority(fleet, kind, team_token, allowed):
    from astrolift_agents.models import Brief

    bind(fleet, kind, [Permission.APP_DEPLOY])
    brief = Brief.objects.create(organization=fleet.world.org, content_hash="legacy", storage_key="")
    before = AgentTask.objects.count()
    with token(fleet, team=fleet.world.medops if team_token else None), tenant(fleet, True):
        result = AgentsMutation().launch_task(
            fleet.info, brief_id=str(brief.guid), org_id=str(fleet.world.org.guid)
        )
    assert result.ok is allowed
    assert AgentTask.objects.count() == before + int(allowed)


def test_trigger_control_uses_the_bound_agents_scope(fleet):
    bind(fleet, "APP", [Permission.APP_UPDATE])
    with tenant(fleet, True):
        own = AgentsMutation().create_agent_trigger(fleet.info, agent_slug=fleet.agents[0].slug)
        assert own.ok and own.signing_secret
        assert AgentsMutation().unbind_agent_trigger(fleet.info, slug=own.slug).ok
        with pytest.raises(PermissionDenied):
            AgentsMutation().create_agent_trigger(fleet.info, agent_slug=fleet.agents[1].slug)


@pytest.mark.parametrize("target,accepted", [(0, True), (1, False)])
def test_websocket_entry_checks_the_task_before_opening_a_pod(fleet, monkeypatch, target, accepted):
    from asgiref.sync import async_to_sync

    from core.schema import vnc_ws, ws_auth

    bind(fleet, "APP", [Permission.AGENT_TASK_WATCH])
    task = fleet.tasks[target]
    running(task)
    opened, sent = [], []

    async def user(_session):
        return fleet.user, {}

    async def context(_user, _session):
        return TenantContext(organization_id=fleet.world.org.pk, actor_user_id=fleet.user.pk)

    class Backend(vnc_ws._StubVncBackend):
        async def open(self, *, task_guid):
            opened.append(task_guid)
            return await super().open(task_guid=task_guid)

    async def receive():
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(ws_auth, "_resolve_user_from_sessionid", user)
    monkeypatch.setattr(ws_auth, "_resolve_tenant_for_user", context)
    monkeypatch.setattr(vnc_ws, "get_vnc_backend", lambda: Backend())
    async_to_sync(vnc_ws.vnc_ws_application)(
        {"type": "websocket", "path": f"/app/vnc/{task.guid}", "headers": []}, receive, send
    )
    assert any(message["type"] == "websocket.accept" for message in sent) is accepted
    assert opened == ([str(task.guid)] if accepted else [])


def test_snapshot_projection_rejects_a_task_outside_the_active_org(fleet, monkeypatch):
    from astrolift_agents.schema.types import agent_task_to_type

    bind(fleet, "ORG", [Permission.AGENT_TASK_WATCH])
    foreign = ScopeWorld("1745-frame")
    task = AgentTask.objects.create(organization=foreign.org)
    running(task)
    minted = []
    monkeypatch.setattr(
        "astrolift_agents.snapshot_store.presigned_snapshot_download_url",
        lambda **kwargs: minted.append(kwargs) or "https://frames.example/frame",
    )
    with tenant(fleet):
        assert agent_task_to_type(task).snapshot_url is None
    assert not minted


@pytest.mark.parametrize("placement", ["foreign-dispatcher", "foreign-cluster", "platform-cluster"])
def test_task_projection_confines_placement_metadata(fleet, placement):
    from astrolift_agents.models import DispatcherInstance
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    bind(fleet, "APP")
    foreign = ScopeWorld("1745-placement")
    plugin = ProviderPlugin.objects.create(slug="1745-placement", name="Placement")
    cluster = TenantCluster.objects.create(
        organization=None if placement == "platform-cluster" else foreign.org,
        provider_plugin=plugin,
        name="Placement",
        slug="1745-placement",
    )
    dispatcher = DispatcherInstance.objects.create(
        organization=foreign.org if placement == "foreign-dispatcher" else fleet.world.org,
        name="Placement",
        slug="1745-placement",
        tenant_cluster=cluster,
    )
    AgentTask.objects.filter(pk=fleet.tasks[0].pk).update(dispatcher=dispatcher)
    with tenant(fleet):
        row = AgentsQuery().agent_task(fleet.info, id=str(fleet.tasks[0].guid))
    if placement == "foreign-dispatcher":
        assert row.dispatcher is None
    else:
        assert str(row.dispatcher.id) == str(dispatcher.guid)
        assert row.dispatcher.cluster_name == (cluster.name if placement == "platform-cluster" else "")
        assert row.dispatcher.cluster_id == (str(cluster.guid) if placement == "platform-cluster" else None)
