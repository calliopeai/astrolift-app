"""A member of one team cannot reach another team's agent surfaces (#1866).

One org, two teams (MedOps and Platform), each with a project, an app and a
persistent agent, plus one environment spec per team and one org-shared spec.
The caller holds real RoleBindings, so the gate and the rows it narrows to
read the same grants the production resolver does. Every surface is checked
the same four ways the scope sweeps are (#1731): the caller's own scope
reaches its row, a sibling team's row stays out of reach, an org-level grant
reaches everything, and a selected team (the header or a team token) opens
nothing it does not own.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import Client

from astrolift_agents.models import AgentBox, AgentEnvironmentSpec, AgentTask
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    EnsureAgentBoxInput,
    RunAstroliftAgentInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_registry.models import Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, clear_current_tenant, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


def _spec(world, slug, *, team=None, project=None, **fields):
    return AgentEnvironmentSpec.objects.create(
        organization=world.org,
        team=team,
        project=project,
        name=slug,
        slug=slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        image_tag="example/agent:1",
        **fields,
    )


@pytest.fixture
def world():
    world = ScopeWorld("1866")
    world.user = make_user("1866")
    world.info = make_info(world.user)
    world.medops_agent, world.platform_agent = (
        Workload.objects.create(
            registered_app=app,
            name=name,
            slug=name,
            kind=Workload.Kind.AGENT,
            run_mode=Workload.RunMode.PERSISTENT,
        )
        for app, name in [(world.medops_app, "medops-bot"), (world.platform_app, "platform-bot")]
    )
    world.medops_spec = _spec(world, "medops-env", team=world.medops, project=world.medops_project)
    world.platform_spec = _spec(world, "platform-env", team=world.platform, project=world.platform_project)
    world.shared_spec = _spec(world, "shared-env")
    world.medops_box, world.platform_box = (
        AgentBox.objects.create(
            organization=world.org,
            name=agent.name,
            slug=f"box-{agent.slug}",
            agent_definition=agent,
            status=AgentBox.Status.RUNNING,
        )
        for agent in [world.medops_agent, world.platform_agent]
    )
    world.org_box = AgentBox.objects.create(
        organization=world.org, name="Org box", slug="box-org", status=AgentBox.Status.RUNNING
    )
    return world


def grant(world, *permissions, kind="TEAM"):
    target = {"TEAM": world.medops, "PROJECT": world.medops_project, "ORG": world.org}[kind]
    return bind_role(
        world.user,
        permissions=permissions,
        kind=kind,
        scope_id=target.pk,
        slug=f"iso-{kind}-{uuid4().hex[:8]}",
    )


def member(world, *, selected=False):
    """The caller's tenant, with MedOps selected when ``selected``."""
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=world.user.pk,
            team_id=world.medops.pk if selected else None,
        )
    )


@contextmanager
def team_token(world, *, scopes=("admin",)):
    credential = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="team-token",
        token_hash=uuid4().hex * 2,
        token_last_4="1866",
        scopes=list(scopes),
    )
    marker = set_current_api_token(credential)
    try:
        yield credential
    finally:
        reset_current_api_token(marker)


def refused(result) -> bool:
    return not result.ok and any(error.code in {"PERMISSION_DENIED", "NOT_FOUND"} for error in result.errors)


# ---------------------------------------------------------------------------
# Environment specs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT"])
def test_spec_list_shows_the_callers_own_and_the_shared_specs(world, kind, selected):
    grant(world, Permission.AGENT_ENV_SPEC_READ, kind=kind)
    with member(world, selected=selected):
        rows = AgentsQuery().agent_environment_specs(world.info, org_id=str(world.org.guid))
    assert {row.slug for row in rows} == {"medops-env", "shared-env"}


def test_spec_list_is_the_whole_org_for_an_org_grant(world):
    grant(world, Permission.AGENT_ENV_SPEC_READ, kind="ORG")
    with member(world):
        rows = AgentsQuery().agent_environment_specs(world.info, org_id=str(world.org.guid))
    assert {row.slug for row in rows} == {"medops-env", "platform-env", "shared-env"}
    shared = next(row for row in rows if row.slug == "shared-env")
    owned = next(row for row in rows if row.slug == "medops-env")
    assert shared.team_id is None and shared.project_id is None
    assert str(owned.project_id) == str(world.medops_project.guid)


def test_team_token_sees_only_its_teams_and_the_shared_specs(world):
    grant(world, Permission.AGENT_ENV_SPEC_READ, kind="ORG")
    with member(world, selected=True), team_token(world):
        rows = AgentsQuery().agent_environment_specs(world.info, org_id=str(world.org.guid))
    assert {row.slug for row in rows} == {"medops-env", "shared-env"}


def test_spec_read_hides_another_teams_spec(world):
    grant(world, Permission.AGENT_ENV_SPEC_READ)
    with member(world, selected=True):
        assert AgentsQuery().agent_environment_spec(world.info, slug="platform-env") is None
        assert AgentsQuery().agent_environment_spec(world.info, slug="medops-env").slug == "medops-env"
        assert AgentsQuery().agent_environment_spec(world.info, slug="shared-env").slug == "shared-env"


@pytest.mark.parametrize("selected", [False, True])
def test_spec_update_checks_the_specs_owner(world, selected):
    grant(world, Permission.AGENT_ENV_SPEC_UPDATE)
    patch = UpdateAgentEnvironmentSpecInput(tool_preset="dev")
    with member(world, selected=selected):
        assert AgentsMutation().update_agent_environment_spec(world.info, slug="medops-env", input=patch).ok
        for slug in ["platform-env", "shared-env"]:
            result = AgentsMutation().update_agent_environment_spec(world.info, slug=slug, input=patch)
            assert refused(result), slug
    world.platform_spec.refresh_from_db()
    world.shared_spec.refresh_from_db()
    assert world.platform_spec.tool_preset == world.shared_spec.tool_preset == ""


def test_only_an_org_grant_edits_or_deletes_a_shared_spec(world):
    grant(world, Permission.AGENT_ENV_SPEC_UPDATE, Permission.AGENT_ENV_SPEC_DELETE, kind="ORG")
    with member(world):
        patch = UpdateAgentEnvironmentSpecInput(tool_preset="dev")
        assert AgentsMutation().update_agent_environment_spec(world.info, slug="shared-env", input=patch).ok
        assert AgentsMutation().delete_agent_environment_spec(world.info, slug="shared-env").ok


def test_team_token_cannot_write_shared_specs_with_an_org_role(world):
    grant(
        world,
        Permission.AGENT_ENV_SPEC_CREATE,
        Permission.AGENT_ENV_SPEC_UPDATE,
        Permission.AGENT_ENV_SPEC_DELETE,
        Permission.AGENT_ENV_SPEC_READ,
        kind="ORG",
    )
    with member(world, selected=True), team_token(world):
        assert AgentsQuery().agent_environment_spec(world.info, slug="shared-env") is not None
        assert _create(world, "new-owned-env", project_id=str(world.medops_project.guid)).ok
        assert (
            AgentsMutation()
            .update_agent_environment_spec(
                world.info, slug="medops-env", input=UpdateAgentEnvironmentSpecInput(tool_preset="dev")
            )
            .ok
        )
        assert refused(_create(world, "new-shared-env"))
        assert refused(
            AgentsMutation().update_agent_environment_spec(
                world.info, slug="shared-env", input=UpdateAgentEnvironmentSpecInput(tool_preset="changed")
            )
        )
        assert refused(AgentsMutation().delete_agent_environment_spec(world.info, slug="shared-env"))
    world.shared_spec.refresh_from_db()
    assert world.shared_spec.deleted_at is None and world.shared_spec.tool_preset == ""
    assert not AgentEnvironmentSpec.objects.filter(slug="new-shared-env").exists()


def test_spec_delete_checks_the_specs_owner(world):
    grant(world, Permission.AGENT_ENV_SPEC_DELETE)
    with member(world, selected=True):
        assert refused(AgentsMutation().delete_agent_environment_spec(world.info, slug="platform-env"))
        assert refused(AgentsMutation().delete_agent_environment_spec(world.info, slug="shared-env"))
        assert AgentsMutation().delete_agent_environment_spec(world.info, slug="medops-env").ok
    world.platform_spec.refresh_from_db()
    assert world.platform_spec.deleted_at is None


def _create(world, slug, **owner):
    return AgentsMutation().create_agent_environment_spec(
        world.info,
        input=CreateAgentEnvironmentSpecInput(name=slug, slug=slug, agent_type="claude", **owner),
        org_id=str(world.org.guid),
    )


def test_spec_create_is_checked_where_the_spec_will_be_owned(world):
    grant(world, Permission.AGENT_ENV_SPEC_CREATE)
    with member(world, selected=True):
        own = _create(world, "own-env", project_id=str(world.medops_project.guid))
        team = _create(world, "team-env", team_id=str(world.medops.guid))
        foreign = _create(world, "foreign-env", project_id=str(world.platform_project.guid))
        shared = _create(world, "loose-env")
        mixed = _create(
            world, "mixed-env", team_id=str(world.platform.guid), project_id=str(world.medops_project.guid)
        )
    assert own.ok and team.ok
    assert refused(foreign) and refused(shared)
    assert not mixed.ok and mixed.errors[0].field == "teamId"
    created = AgentEnvironmentSpec.objects.get(slug="own-env")
    assert (created.team_id, created.project_id) == (world.medops.pk, world.medops_project.pk)
    assert AgentEnvironmentSpec.objects.get(slug="team-env").project_id is None
    assert not AgentEnvironmentSpec.objects.filter(
        slug__in=["foreign-env", "loose-env", "mixed-env"]
    ).exists()


def test_an_org_grant_creates_an_org_shared_spec(world):
    grant(world, Permission.AGENT_ENV_SPEC_CREATE, kind="ORG")
    with member(world):
        assert _create(world, "org-env").ok
    created = AgentEnvironmentSpec.objects.get(slug="org-env")
    assert created.team_id is None and created.project_id is None


def test_secret_status_follows_spec_visibility(world, monkeypatch):
    AgentEnvironmentSpec.objects.filter(pk=world.platform_spec.pk).update(
        secret_refs=[{"env_var": "TOKEN", "uri": f"agents/{world.org.guid}/platform"}]
    )
    grant(world, Permission.SECRET_LIST)
    probed = []
    monkeypatch.setattr(
        "astrolift_dispatch.agent_secrets.probe_ref_statuses", lambda **kw: probed.append(kw) or []
    )
    with member(world, selected=True):
        assert AgentsQuery().agent_environment_spec_secret_status(world.info, slug="platform-env") == []
        AgentsQuery().agent_environment_spec_secret_status(world.info, slug="medops-env")
    assert len(probed) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda info: AgentsMutation().set_agent_secret_value(
            info, env_spec_slug="medops-env", env_var="TOKEN", value="v"
        ),
        lambda info: AgentsMutation().reveal_agent_secret_value(
            info, env_spec_slug="medops-env", env_var="TOKEN"
        ),
        lambda info: AgentsMutation().upsert_agent_secret_ref(
            info, env_spec_slug="medops-env", env_var="TOKEN", uri="agents/x/y"
        ),
    ],
    ids=["set", "reveal", "bind"],
)
def test_secret_values_and_bindings_need_an_org_grant(world, call):
    """Secret refs are confined per org only, so a team-scoped writer could
    name another team's secret; until they are confined per owner, value and
    binding operations stay org-level (#1866). The test info carries no
    session, so step-up passes and the refusal is the permission's."""
    grant(world, Permission.SECRET_WRITE, Permission.SECRET_READ)
    with member(world, selected=True):
        result = call(world.info)
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Dispatch: an env spec's secrets serve only agents that may use the spec
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatch_stubs(monkeypatch):
    starts = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append(workflow_id)
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="run-1", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    monkeypatch.setattr(
        "astrolift_agents.services.task_preparation.prepare_agent_task", lambda task, **kw: None
    )
    return starts


def _run(world, spec):
    Workload.objects.filter(pk=world.medops_agent.pk).update(
        run_family=Workload.RunFamily.TASK, run_mode=Workload.RunMode.ONCE
    )
    return AgentsMutation().run_astrolift_agent(
        world.info,
        input=RunAstroliftAgentInput(agent_slug="medops-bot", environment_spec_id=str(spec.guid)),
    )


def test_dispatch_refuses_another_teams_spec(world, dispatch_stubs):
    grant(world, Permission.AGENT_DISPATCH, Permission.AGENT_READ)
    with member(world, selected=True):
        foreign = _run(world, world.platform_spec)
        shared = _run(world, world.shared_spec)
        own = _run(world, world.medops_spec)
    assert not foreign.ok and foreign.errors[0].code == "NOT_FOUND"
    assert foreign.errors[0].field == "environmentSpecId"
    assert shared.ok and own.ok
    assert set(AgentTask.objects.values_list("environment_spec__slug", flat=True)) == {
        "shared-env",
        "medops-env",
    }


def test_default_spec_is_never_another_projects(world):
    from astrolift_agents.services.task_preparation import default_environment_spec

    canonical = _spec(world, "medops-bot", team=world.platform, project=world.platform_project)
    assert default_environment_spec(world.medops_agent) is None
    AgentEnvironmentSpec.objects.filter(pk=canonical.pk).update(
        team=world.medops, project=world.medops_project
    )
    assert default_environment_spec(world.medops_agent).pk == canonical.pk
    AgentEnvironmentSpec.objects.filter(pk=canonical.pk).update(team=None, project=None)
    assert default_environment_spec(world.medops_agent).pk == canonical.pk


def test_spec_use_follows_its_owner(world):
    from astrolift_agents.visibility import spec_usable_by_app

    team_spec = _spec(world, "team-only", team=world.medops)
    assert spec_usable_by_app(world.shared_spec, world.platform_app)
    assert spec_usable_by_app(world.medops_spec, world.medops_app)
    assert not spec_usable_by_app(world.medops_spec, world.platform_app)
    assert spec_usable_by_app(team_spec, world.medops_app)
    assert not spec_usable_by_app(team_spec, world.platform_app)
    world.medops_project.soft_delete()
    world.medops_spec.refresh_from_db()
    assert not spec_usable_by_app(world.medops_spec, world.medops_app)


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selected", [False, True])
def test_box_list_narrows_to_the_callers_scope(world, selected):
    grant(world, Permission.AGENT_READ)
    with member(world, selected=selected):
        rows = AgentsQuery().agent_boxes(world.info, org_id=str(world.org.guid))
    assert {row.slug for row in rows} == {"box-medops-bot"}


def test_box_list_is_the_whole_org_for_an_org_grant(world):
    grant(world, Permission.AGENT_READ, kind="ORG")
    with member(world):
        rows = AgentsQuery().agent_boxes(world.info, org_id=str(world.org.guid))
    assert {row.slug for row in rows} == {"box-medops-bot", "box-platform-bot", "box-org"}


def test_box_read_is_checked_at_the_boxs_scope(world):
    grant(world, Permission.AGENT_READ)
    with member(world, selected=True):
        assert AgentsQuery().agent_box(world.info, slug="box-medops-bot").slug == "box-medops-bot"
        for slug in ["box-platform-bot", "box-org"]:
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_box(world.info, slug=slug)


def test_box_destroy_is_checked_at_the_boxs_scope(world, monkeypatch):
    monkeypatch.setattr("astrolift_agents.services.agent_box.stop_agent_box", lambda box, **kw: None)
    grant(world, Permission.AGENT_DISPATCH)
    with member(world, selected=True):
        assert refused(AgentsMutation().destroy_agent_box(world.info, slug="box-platform-bot"))
        assert refused(AgentsMutation().destroy_agent_box(world.info, slug="box-org"))
        assert AgentsMutation().destroy_agent_box(world.info, slug="box-medops-bot").ok
    assert AgentBox.all_objects.get(slug="box-medops-bot").deleted_at is not None
    assert AgentBox.objects.filter(slug__in=["box-platform-bot", "box-org"]).count() == 2


def _ensure(world, **fields):
    return AgentsMutation().ensure_agent_box(
        world.info, input=EnsureAgentBoxInput(**fields), org_id=str(world.org.guid)
    )


@pytest.fixture
def started(monkeypatch):
    boxes = []
    monkeypatch.setattr("astrolift_agents.services.agent_box.start_agent_box", boxes.append)
    return boxes


def test_ensure_is_checked_where_the_box_will_run(world, started):
    grant(world, Permission.AGENT_DISPATCH)
    AgentBox.objects.all().delete()
    with member(world, selected=True):
        assert refused(_ensure(world, agent_slug="platform-bot"))
        assert refused(_ensure(world, environment_spec_slug="platform-env"))
        assert refused(_ensure(world, image="example/agent:1"))
        assert refused(_ensure(world, environment_spec_slug="shared-env"))
        assert refused(_ensure(world, agent_slug="medops-bot", environment_spec_slug="platform-env"))
        assert _ensure(world, agent_slug="medops-bot", environment_spec_slug="shared-env").ok
        assert _ensure(world, environment_spec_slug="medops-env").ok
    assert [box.agent_definition_id for box in started] == [world.medops_agent.pk, None]
    spec_box = started[1]
    assert (spec_box.team_id, spec_box.project_id) == (world.medops.pk, world.medops_project.pk)


def test_an_org_grant_ensures_an_org_level_box(world, started):
    grant(world, Permission.AGENT_DISPATCH, kind="ORG")
    AgentBox.objects.all().delete()
    with member(world):
        assert _ensure(world, environment_spec_slug="shared-env").ok
        assert _ensure(world, image="example/agent:1").ok
    assert all(box.team_id is None and box.project_id is None for box in started)


def test_ensure_never_hands_over_a_live_box_of_another_scope(world, started):
    """Agent slugs are unique per app, so two teams' agents can derive the
    same box address. The live box stays with the scope that made it."""
    from django.contrib.auth.models import AnonymousUser

    AgentBox.objects.filter(pk=world.medops_box.pk).delete()
    Workload.objects.filter(pk=world.platform_agent.pk).update(slug="medops-bot")
    AgentBox.objects.filter(pk=world.platform_box.pk).update(slug="box-medops-bot")
    grant(world, Permission.AGENT_DISPATCH)
    # A token-ensured box has no human owner, so its address is the agent's
    # slug alone: the one the platform team's live box already holds.
    anonymous = SimpleNamespace(user=AnonymousUser())
    world.info = SimpleNamespace(context=SimpleNamespace(user=anonymous.user, request=anonymous))
    with member(world):
        result = _ensure(world, agent_slug="medops-bot")
    assert not result.ok and result.errors[0].code == "CONFLICT"
    assert not started
    assert AgentBox.objects.get(slug="box-medops-bot").agent_definition_id == world.platform_agent.pk


# ---------------------------------------------------------------------------
# The exec relay (box attach and app exec)
# ---------------------------------------------------------------------------


@pytest.fixture
def relay_tenant():
    """The relay's checks pin the tenant and the bearer on the handshake's
    context, as middleware does for a request; clear both after."""
    yield
    clear_current_tenant()
    set_current_api_token(None)


def test_box_attach_is_checked_at_the_boxs_scope(world, relay_tenant):
    from core.schema.exec_ws import _check_box_attach_permission

    grant(world, Permission.AGENT_BOX_ATTACH)
    attach = _check_box_attach_permission.func
    kwargs = {"tenant_org_id": world.org.pk, "actor_user_id": world.user.pk}
    assert attach(box_slug="box-medops-bot", **kwargs) is True
    assert attach(box_slug="box-platform-bot", **kwargs) is False
    assert attach(box_slug="box-org", **kwargs) is False


def test_app_exec_is_checked_at_the_apps_scope(world, relay_tenant):
    from core.schema.exec_ws import _check_exec_permission

    grant(world, Permission.APP_EXEC_POD)
    execute = _check_exec_permission.func
    kwargs = {"tenant_org_id": world.org.pk, "actor_user_id": world.user.pk}
    assert execute(app_slug=world.medops_app.slug, **kwargs) is True
    assert execute(app_slug=world.platform_app.slug, **kwargs) is False


def test_a_bearers_scopes_cap_the_exec_relay(world, relay_tenant):
    from core.schema.exec_ws import _check_box_attach_permission

    grant(world, Permission.AGENT_BOX_ATTACH, kind="ORG")
    read_only = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="read-only",
        token_hash=uuid4().hex * 2,
        token_last_4="1866",
        scopes=["read:apps"],
    )
    kwargs = {"tenant_org_id": world.org.pk, "actor_user_id": world.user.pk, "box_slug": "box-medops-bot"}
    assert _check_box_attach_permission.func(api_token=read_only, **kwargs) is False
    set_current_api_token(None)
    assert _check_box_attach_permission.func(**kwargs) is True


# ---------------------------------------------------------------------------
# MCP repo tools
# ---------------------------------------------------------------------------


def _mcp_call(world, credential, name, arguments):
    from astrolift_agents.tests.test_mcp_gateway import _request
    from astrolift_agents.views.mcp import mcp_gateway

    marker = set_current_api_token(credential)
    try:
        with tenant_context(
            TenantContext(
                organization_id=world.org.pk, actor_user_id=world.user.pk, team_id=credential.team_id
            )
        ):
            response = mcp_gateway(
                _request(world.user, credential, "tools/call", {"name": name, "arguments": arguments})
            )
    finally:
        reset_current_api_token(marker)
    return json.loads(response.content)["result"]


def test_mcp_repo_tools_check_the_named_project(world, monkeypatch):
    from astrolift_identity.api_tokens import SCOPE_MCP_WRITE

    synced = []
    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync.register_agent_repo",
        lambda **kw: synced.append(kw["project"].pk) or SimpleNamespace(status="ok", agents=[], workflows=[]),
    )
    grant(world, Permission.AGENT_CREATE, Permission.AGENT_UPDATE)
    plaintext = "alft_at_iso-1866"
    credential = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="mcp",
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        token_last_4="1866",
        scopes=[SCOPE_MCP_WRITE],
    )
    for project, allowed in [(world.platform_project, False), (world.medops_project, True)]:
        result = _mcp_call(
            world,
            credential,
            "astrolift_sync_agent_repo",
            {"project_id": str(project.guid), "source_repo": "acme/agents"},
        )
        assert result["isError"] is (not allowed), result
    assert synced == [world.medops_project.pk]


def test_import_never_moves_another_projects_agent(world):
    from astrolift_agents.services.agent_importers import import_agent_spec
    from astrolift_agents.services.imported_agent_registration import (
        ImportedAgentRegistrationError,
        persist_imported_agent_package,
    )

    package = import_agent_spec(
        "agents_md",
        {"name": "Review bot", "content": "Review changes."},
        options={"runtime_image": "example/agent:1"},
    ).package
    first = persist_imported_agent_package(project=world.platform_project, package=package, slug="review-bot")
    with pytest.raises(ImportedAgentRegistrationError, match="already in use"):
        persist_imported_agent_package(project=world.medops_project, package=package, slug="review-bot")
    first.app.refresh_from_db()
    assert first.app.project_id == world.platform_project.pk
    assert first.environment_spec.project_id == world.platform_project.pk


def test_registration_never_rewrites_another_scopes_spec(world):
    from astrolift_agents.services.project_membership import SpecOwnedElsewhere, spec_for_registration

    with pytest.raises(SpecOwnedElsewhere):
        spec_for_registration(organization=world.org, slug="platform-env", app=world.medops_app)
    fresh = spec_for_registration(organization=world.org, slug="new-env", app=world.medops_app)
    assert fresh.pk is None
    assert (fresh.team_id, fresh.project_id) == (world.medops.pk, world.medops_project.pk)


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT"])
def test_registration_cannot_rewrite_shared_spec_with_only_local_grants(world, kind):
    from astrolift_agents.services.agent_importers import import_agent_spec
    from astrolift_agents.services.imported_agent_registration import (
        ImportedAgentRegistrationError,
        persist_imported_agent_package,
    )
    from astrolift_registry.models import RegisteredApp

    grant(world, Permission.AGENT_CREATE, Permission.AGENT_ENV_SPEC_UPDATE, kind=kind)
    package = import_agent_spec(
        "agents_md",
        {"name": "Replacement bot", "content": "Review changes."},
        options={"runtime_image": "example/agent:2"},
    ).package
    with member(world, selected=True), pytest.raises(ImportedAgentRegistrationError, match="org-shared"):
        persist_imported_agent_package(project=world.medops_project, package=package, slug="shared-env")
    world.shared_spec.refresh_from_db()
    assert world.shared_spec.image_tag == "example/agent:1"
    assert not RegisteredApp.objects.filter(organization=world.org, slug="shared-env").exists()


def test_manifest_sync_cannot_rewrite_shared_spec_with_project_grants(world):
    from astrolift_agents.services.project_membership import SpecOwnedElsewhere
    from astrolift_registry.services.manifest_sync import _upsert_agent_environment_spec

    grant(world, Permission.AGENT_UPDATE, Permission.AGENT_ENV_SPEC_UPDATE, kind="PROJECT")
    world.shared_spec.slug = world.medops_agent.slug
    world.shared_spec.save(update_fields=["slug"])
    with member(world, selected=True), pytest.raises(SpecOwnedElsewhere, match="org-shared"):
        _upsert_agent_environment_spec(
            app=world.medops_app,
            workload_slug=world.medops_agent.slug,
            raw_manifest=SimpleNamespace(raw={"environment": {"CHANGED": "yes"}}),
            source_repo="https://example.com/agent.git",
            deploy_branch="main",
            manifest_path="astrolift.toml",
        )
    world.shared_spec.refresh_from_db()
    assert world.shared_spec.image_tag == "example/agent:1" and world.shared_spec.env_vars == {}


def test_org_grant_can_register_against_a_shared_spec(world):
    from astrolift_agents.services.agent_importers import import_agent_spec
    from astrolift_agents.services.imported_agent_registration import persist_imported_agent_package

    grant(world, Permission.AGENT_ENV_SPEC_UPDATE, kind="ORG")
    package = import_agent_spec(
        "agents_md",
        {"name": "Replacement bot", "content": "Review changes."},
        options={"runtime_image": "example/agent:2"},
    ).package
    with member(world):
        registered = persist_imported_agent_package(
            project=world.medops_project, package=package, slug="shared-env"
        )
    world.shared_spec.refresh_from_db()
    assert registered.environment_spec.pk == world.shared_spec.pk
    assert world.shared_spec.image_tag == "example/agent:2"


def test_team_token_cannot_register_against_shared_spec_with_org_grant(world):
    from astrolift_agents.services.project_membership import SpecOwnedElsewhere, spec_for_registration

    grant(world, Permission.AGENT_ENV_SPEC_UPDATE, kind="ORG")
    with (
        member(world, selected=True),
        team_token(world),
        pytest.raises(SpecOwnedElsewhere, match="org-shared"),
    ):
        spec_for_registration(organization=world.org, slug="shared-env", app=world.medops_app)


# ---------------------------------------------------------------------------
# Dispatch Service REST: log and meter ingest
# ---------------------------------------------------------------------------


def _dispatcher(org, name):
    from astrolift_agents.models import DispatcherInstance

    raw = uuid4().hex
    DispatcherInstance.objects.create(
        organization=org,
        name=name,
        slug=f"{name}-{uuid4().hex[:6]}",
        endpoint="https://dispatch.invalid",
        api_key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        status="active",
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.mark.django_db(transaction=True)
def test_log_and_meter_ingest_need_the_dispatchers_key_and_org(world):
    from astrolift_identity.models import Organization
    from astrolift_lifecycle.models import AgentRun

    run = AgentRun.objects.create(workload=world.medops_agent, status=AgentRun.Status.RUNNING)
    other = Organization.objects.create(name="Other", slug="other-1866")
    own_key, other_key = _dispatcher(world.org, "own"), _dispatcher(other, "other")
    logs = f"/api/dispatch/v1/tasks/{run.guid}/logs/"
    meter = f"/api/dispatch/v1/tasks/{run.guid}/meter/"
    lines = json.dumps({"lines": ["hello"]})
    usage = json.dumps({"cpu_seconds": 1.0, "source": "wall_time_estimate"})
    client = Client()

    assert client.post(logs, data=lines, content_type="application/json").status_code == 401
    assert client.post(meter, data=usage, content_type="application/json").status_code == 401
    assert client.post(logs, data=lines, content_type="application/json", **other_key).status_code == 404
    assert client.post(meter, data=usage, content_type="application/json", **other_key).status_code == 404
    run.refresh_from_db()
    assert not run.log_excerpt

    assert client.post(logs, data=lines, content_type="application/json", **own_key).status_code == 200
    assert client.post(meter, data=usage, content_type="application/json", **own_key).status_code == 201
    run.refresh_from_db()
    assert run.log_excerpt == "hello"
