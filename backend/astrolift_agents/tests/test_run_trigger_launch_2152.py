# ruff: noqa: F811  (pytest fixtures imported from test_run_agent_mutation)
"""``launchTask`` and ``runAstroliftAgent`` record who and what started the run (#2152).

The session path is covered in ``test_run_agent_mutation``; this pins the
token path (an API run) and the Brief-based ``launchTask``, and that the
``AstroliftAgentTask`` type exposes the initiator.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentTask, Brief
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.types import agent_task_to_type
from astrolift_agents.tests.test_run_agent_mutation import (  # noqa: F401 - fixtures
    _agent_workload,
    info,
    org,
    temporal_recorder,
    user,
    with_tenant_org,
)
from astrolift_graphql import GUID
from core.permissions import Permission

pytestmark = pytest.mark.django_db


@pytest.fixture
def as_token(monkeypatch):
    token = SimpleNamespace(pk=7, team_id=None, organization_id=None, scopes=["admin"])
    monkeypatch.setattr("astrolift_identity.api_tokens.get_current_api_token", lambda: token)
    monkeypatch.setattr("astrolift_identity.api_tokens.token_scope_allows_permission", lambda *_a: True)
    return token


def test_run_agent_through_a_token_is_an_api_run(
    permission_resolver, info, org, user, with_tenant_org, temporal_recorder, as_token
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)
    with with_tenant_org(org, actor_user_id=user.id):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )
    assert result.ok, result.errors
    task = AgentTask.objects.get(guid=str(result.data.id))
    assert (task.trigger_kind, task.triggered_by_user_id) == ("api", user.id)
    assert result.data.trigger_kind == "api"
    assert result.data.triggered_by_user_id == str(user.id)
    assert result.data.triggered_by_me is True


def test_launch_task_records_the_caller(permission_resolver, info, org, user, with_tenant_org):
    permission_resolver.grant(Permission.APP_DEPLOY)
    brief = Brief.objects.create(
        organization=org, content_hash="c" * 64, storage_key="", manifest_snapshot={}
    )
    with with_tenant_org(org, actor_user_id=user.id):
        result = AgentsMutation().launch_task(info, brief_id=GUID(str(brief.guid)), org_id=str(org.guid))
    assert result.ok, result.errors
    task = AgentTask.objects.get(guid=str(result.data.task_id))
    assert (task.trigger_kind, task.triggered_by_user_id) == ("manual", user.id)


def test_task_type_marks_only_the_viewers_own_runs(org, user, with_tenant_org):
    task = AgentTask.objects.create(organization=org, trigger_kind="webhook")
    with with_tenant_org(org, actor_user_id=user.id):
        shaped = agent_task_to_type(task)
    assert (shaped.trigger_kind, shaped.triggered_by_user_id, shaped.triggered_by_me) == (
        "webhook",
        None,
        False,
    )
