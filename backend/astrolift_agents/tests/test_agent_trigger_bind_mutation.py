"""Tests for the agent trigger BIND mutation surface (spec 33, PR-6 / #951).

PR-6 (#929) added ``WorkflowWebhook.agent_definition`` + the SCM dispatch
fan-out, so a bound webhook DOES dispatch an agent Task. #951 exposes the
missing GraphQL seam to create / bind / list / unbind that webhook:

  * ``createAgentTrigger`` — binds a ``WorkflowWebhook`` to a ``kind=agent``
    Workload, persisting ``input_mapping`` + the SCM ``scm_repo`` /
    ``branch_pattern`` filters; returns the endpoint + plaintext signing
    secret (shown once);
  * ``agentTriggers`` — lists an agent's bound webhooks (read surface for the
    run-spec editor's Trigger card);
  * ``unbindAgentTrigger`` — disables a binding by slug (slugs are never
    recycled, so removal = ``enabled=False``).

Every write is org-scoped, permission-gated (``app.update``), and denies a
cross-org target. The dispatch end-to-end (a POST to the bound endpoint fires
the agent with mapped input) reuses the PR-6 path and is asserted here against
the live webhook endpoint.

Resolvers are exercised by direct invocation (the agents-test convention) with
a controllable permission resolver + tenant context bound, mirroring
``test_update_agent_run_spec.py``.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from django.test import Client

from astrolift_agents.models import AgentTask, WorkflowWebhook
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture(autouse=True)
def _temporal_disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture
def temporal_recorder(monkeypatch):
    """Record DispatchAgentTaskWorkflow starts without a Temporal server."""
    starts: list[tuple] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return starts


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False, is_superuser=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _agent(org, *, app_slug="trigger-app", workload_slug="trigger-agent", kind=Workload.Kind.AGENT):
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    project = Project.objects.create(organization=org, team=team, name=f"P-{app_slug}", slug=f"p-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=workload_slug.replace("-", " ").title(),
        slug=workload_slug,
        kind=kind.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.TRIGGER.value,
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Bind Org", slug="bind-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Bind Other", slug="bind-other")


def _bind(info, org, with_tenant_org, slug, **kwargs):
    with with_tenant_org(org):
        return AgentsMutation().create_agent_trigger(info, agent_slug=slug, **kwargs)


def _unbind(info, org, with_tenant_org, slug):
    with with_tenant_org(org):
        return AgentsMutation().unbind_agent_trigger(info, slug=slug)


def _list(info, org, with_tenant_org, agent_slug):
    with with_tenant_org(org):
        return AgentsQuery().agent_triggers(info, org_id=str(org.guid), agent_slug=agent_slug)


# ---------------------------------------------------------------------------
# create / bind
# ---------------------------------------------------------------------------


def test_bind_persists_webhook_with_mapping_and_filters(permission_resolver, info, org, with_tenant_org):
    """A bind creates a WorkflowWebhook bound to the agent, org-scoped, with
    the input_mapping + SCM filters persisted, and returns the endpoint +
    plaintext secret (stored only as a hash)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    agent = _agent(org)

    result = _bind(
        info,
        org,
        with_tenant_org,
        agent.slug,
        scm_repo="acme/x",
        branch_pattern="release/*",
        input_mapping={"pr": "pull_request.number"},
    )

    assert result.ok is True
    assert result.endpoint == f"/api/webhooks/workflow/{org.slug}/{result.slug}"
    assert result.signing_secret

    hook = WorkflowWebhook.objects.get(slug=result.slug)
    assert hook.agent_definition_id == agent.id
    assert hook.organization_id == org.id
    assert hook.workflow_definition_id is None
    assert hook.scm_repo == "acme/x"
    assert hook.branch_pattern == "release/*"
    assert hook.input_mapping == {"pr": "pull_request.number"}
    assert hook.enabled is True
    # Secret stored as a hash of the returned plaintext, never the plaintext.
    assert hook.secret_hash == hashlib.sha256(result.signing_secret.encode()).hexdigest()


def test_bind_without_filters_defaults_blank(permission_resolver, info, org, with_tenant_org):
    """Filters/mapping are optional — a bare bind stores blanks / empty map."""
    permission_resolver.grant(Permission.APP_UPDATE)
    agent = _agent(org)

    result = _bind(info, org, with_tenant_org, agent.slug)

    assert result.ok is True
    hook = WorkflowWebhook.objects.get(slug=result.slug)
    assert hook.scm_repo == ""
    assert hook.branch_pattern == ""
    assert hook.input_mapping == {}


def test_bind_denied_without_permission(info, org, with_tenant_org):
    """No app.update grant -> the resolver-entry gate fires (deny-by-default,
    matching the sibling create_agent_trigger) and nothing is created."""
    agent = _agent(org)

    with pytest.raises(PermissionDenied):
        _bind(info, org, with_tenant_org, agent.slug)

    assert WorkflowWebhook.objects.count() == 0


def test_bind_cross_org_agent_not_found(permission_resolver, info, org, other_org, with_tenant_org):
    """An agent slug that exists only in another org is not resolvable for the
    caller's tenant — no webhook bound to the foreign agent."""
    permission_resolver.grant(Permission.APP_UPDATE)
    foreign = _agent(other_org, app_slug="foreign-app", workload_slug="foreign-agent")

    result = _bind(info, org, with_tenant_org, foreign.slug)

    assert result.ok is False
    assert result.message == "agent not found"
    assert WorkflowWebhook.objects.filter(agent_definition=foreign).count() == 0


def test_bind_non_agent_workload_rejected(permission_resolver, info, org, with_tenant_org):
    """A workload in the caller's org but not kind=agent can't be bound."""
    permission_resolver.grant(Permission.APP_UPDATE)
    deployment = _agent(org, app_slug="web-app", workload_slug="web", kind=Workload.Kind.DEPLOYMENT)

    result = _bind(info, org, with_tenant_org, deployment.slug)

    assert result.ok is False
    assert result.message == "agent not found"
    assert WorkflowWebhook.objects.count() == 0


def test_bind_rejects_non_object_input_mapping(permission_resolver, info, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    agent = _agent(org)

    result = _bind(info, org, with_tenant_org, agent.slug, input_mapping=["not", "an", "object"])

    assert result.ok is False
    assert "inputMapping" in result.message
    assert WorkflowWebhook.objects.count() == 0


# ---------------------------------------------------------------------------
# unbind
# ---------------------------------------------------------------------------


def test_unbind_disables_webhook(permission_resolver, info, org, with_tenant_org):
    """Unbind disables the binding (slugs are never recycled, so removal is a
    disable, not a delete)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    agent = _agent(org)
    bound = _bind(info, org, with_tenant_org, agent.slug)

    result = _unbind(info, org, with_tenant_org, bound.slug)

    assert result.ok is True
    hook = WorkflowWebhook.objects.get(slug=bound.slug)
    assert hook.enabled is False


def test_unbind_cross_org_not_found(permission_resolver, info, org, other_org, with_tenant_org):
    """A caller can't unbind a webhook owned by another org — not found, and
    the foreign webhook is left enabled."""
    permission_resolver.grant(Permission.APP_UPDATE)
    foreign_agent = _agent(other_org, app_slug="foreign-app", workload_slug="foreign-agent")
    foreign_hook = WorkflowWebhook.objects.create(
        agent_definition=foreign_agent,
        organization=other_org,
        slug="foreign-hook",
        secret_hash="x",
        enabled=True,
    )

    result = _unbind(info, org, with_tenant_org, foreign_hook.slug)

    assert result.ok is False
    assert result.message == "trigger not found"
    foreign_hook.refresh_from_db()
    assert foreign_hook.enabled is True


def test_unbind_denied_without_permission(info, org, with_tenant_org):
    agent = _agent(org)
    hook = WorkflowWebhook.objects.create(
        agent_definition=agent,
        organization=org,
        slug="some-hook",
        secret_hash="x",
        enabled=True,
    )

    with pytest.raises(PermissionDenied):
        _unbind(info, org, with_tenant_org, hook.slug)

    hook.refresh_from_db()
    assert hook.enabled is True


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_agent_triggers_lists_bound_webhooks(permission_resolver, info, org, with_tenant_org):
    """The list surface returns the agent's bound webhooks with their filters
    + mapping (no secret)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _bind(
        info,
        org,
        with_tenant_org,
        agent.slug,
        scm_repo="acme/x",
        branch_pattern="main",
        input_mapping={"pr": "pull_request.number"},
    )

    rows = _list(info, org, with_tenant_org, agent.slug)

    assert len(rows) == 1
    assert rows[0].scm_repo == "acme/x"
    assert rows[0].branch_pattern == "main"
    assert rows[0].input_mapping == {"pr": "pull_request.number"}
    assert rows[0].enabled is True
    assert rows[0].endpoint == f"/api/webhooks/workflow/{org.slug}/{rows[0].slug}"


def test_agent_triggers_cross_org_empty(permission_resolver, info, org, other_org, with_tenant_org):
    """Listing a foreign agent's slug returns nothing (no cross-tenant leak)."""
    permission_resolver.grant(Permission.AGENT_READ)
    foreign = _agent(other_org, app_slug="foreign-app", workload_slug="foreign-agent")
    WorkflowWebhook.objects.create(
        agent_definition=foreign,
        organization=other_org,
        slug="foreign-listed",
        secret_hash="x",
        enabled=True,
    )

    rows = _list(info, org, with_tenant_org, foreign.slug)

    assert rows == []


# ---------------------------------------------------------------------------
# end-to-end: a POST to the bound endpoint dispatches the agent (PR-6 path)
# ---------------------------------------------------------------------------


def test_bound_webhook_post_dispatches_agent_with_mapped_input(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    """Acceptance (2): binding via the mutation then POSTing the endpoint with
    a valid signature dispatches an AgentTask carrying the mapped input."""
    permission_resolver.grant(Permission.APP_UPDATE)
    agent = _agent(org)
    bound = _bind(
        info,
        org,
        with_tenant_org,
        agent.slug,
        input_mapping={"pr": "pull_request.number"},
    )

    resp = Client().post(
        bound.endpoint,
        data='{"pull_request": {"number": 7}, "extra": "ignored"}',
        content_type="application/json",
        HTTP_X_ASTROLIFT_SIGNATURE=bound.signing_secret,
    )

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["ok"] is True and body["dispatched"] is True

    task = AgentTask.objects.get(agent_definition=agent)
    assert task.status == AgentTask.Status.QUEUED
    # The binding's input_mapping shaped the dispatch payload.
    assert task.dispatch_input == {"pr": 7}
    assert temporal_recorder[0][1][0].trigger_payload == {"pr": 7}
